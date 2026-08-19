"""Governed, stateful DAG orchestration for software-engineering agents."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class RunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ApprovalRequired(Exception):
    pass


class PolicyViolation(Exception):
    pass


Gate = Callable[[dict[str, Any]], None]


@dataclass
class Task:
    name: str
    action: Callable[[dict[str, Any]], Any]
    dependencies: set[str] = field(default_factory=set)
    high_impact: bool = False
    retries: int = 2
    fallback: Callable[[dict[str, Any]], Any] | None = None
    rollback: Callable[[dict[str, Any]], Any] | None = None
    entry_gate: Gate | None = None
    exit_gate: Gate | None = None
    policy_tags: set[str] = field(default_factory=set)


@dataclass
class Run:
    id: str
    context: dict[str, Any]
    status: RunStatus | None = None
    task_states: dict[str, str] = field(default_factory=dict)
    audit: list[dict[str, Any]] = field(default_factory=list)
    approvals: set[str] = field(default_factory=set)
    started_at: float = field(default_factory=time.time)
    plan_version: int = 1
    decision_lineage: list[dict[str, Any]] = field(default_factory=list)


class PolicyEngine:
    @staticmethod
    def check(task: Task, context: dict[str, Any]) -> None:
        retention_days = context.get("retention_days")
        if retention_days is not None and (not isinstance(retention_days, int) or retention_days < 1 or retention_days > 365):
            raise PolicyViolation("compliance policy requires retention_days between 1 and 365")
        if context.get("security_scan") == "failed":
            raise PolicyViolation("security policy rejected failed security scan")
        if "production" in task.policy_tags and context.get("change_ticket") is None:
            raise PolicyViolation("change-control policy requires change_ticket for production work")


class Orchestrator:
    def __init__(self, tasks: list[Task], max_workers: int = 4, state_path: str = "orchestrator.db") -> None:
        self.tasks = {task.name: task for task in tasks}
        self.max_workers = max_workers
        self.state_path = state_path
        self._validate_graph()
        self._database = sqlite3.connect(state_path, check_same_thread=False)
        self._database.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, status TEXT, context TEXT NOT NULL,
                task_states TEXT NOT NULL, audit TEXT NOT NULL, approvals TEXT NOT NULL,
                started_at REAL NOT NULL, plan_version INTEGER NOT NULL,
                decision_lineage TEXT NOT NULL, updated_at REAL NOT NULL
            )"""
        )
        self._database.commit()

    def _validate_graph(self) -> None:
        for task in self.tasks.values():
            missing = task.dependencies - self.tasks.keys()
            if missing:
                raise ValueError(f"{task.name} has missing dependencies: {sorted(missing)}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError("task graph contains a cycle")
            if name in visited:
                return
            visiting.add(name)
            for dependency in self.tasks[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in self.tasks:
            visit(name)

    def run(self, context: dict[str, Any] | None = None, approvals: set[str] | None = None) -> Run:
        run = Run(id=str(uuid.uuid4()), context=dict(context or {}), approvals=set(approvals or set()))
        run.context["run_id"] = run.id
        run.decision_lineage.append({"at": time.time(), "event": "plan_created", "version": run.plan_version})
        self._persist(run)
        return self._execute_run(run)

    def resume(self, run_id: str, approvals: set[str] | None = None) -> Run:
        run = self.load(run_id)
        run.approvals.update(approvals or set())
        run.status = None
        self._record(run, "run_resumed", approvals=sorted(run.approvals))
        return self._execute_run(run)

    def replan(self, run_id: str, changed_context: dict[str, Any], changed_tasks: set[str]) -> Run:
        run = self.load(run_id)
        unknown = changed_tasks - self.tasks.keys()
        if unknown:
            raise ValueError(f"unknown changed tasks: {sorted(unknown)}")
        impacted = self._descendants(changed_tasks) | changed_tasks
        run.context.update(changed_context)
        for task_name in impacted:
            run.task_states.pop(task_name, None)
            run.context.pop(task_name, None)
        invalidated_approvals = run.approvals & {
            task_name for task_name in impacted if self.tasks[task_name].high_impact
        }
        run.approvals.difference_update(invalidated_approvals)
        run.plan_version += 1
        decision = {
            "at": time.time(), "event": "plan_revised", "version": run.plan_version,
            "changed_tasks": sorted(changed_tasks), "invalidated_tasks": sorted(impacted),
            "invalidated_approvals": sorted(invalidated_approvals),
        }
        run.decision_lineage.append(decision)
        self._record(run, "replan", version=decision["version"], changed_tasks=decision["changed_tasks"], invalidated_tasks=decision["invalidated_tasks"])
        run.status = None
        self._persist(run)
        return self._execute_run(run)

    def load(self, run_id: str) -> Run:
        row = self._database.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return Run(
            id=row[0], status=RunStatus(row[1]) if row[1] else None,
            context=json.loads(row[2]), task_states=json.loads(row[3]), audit=json.loads(row[4]),
            approvals=set(json.loads(row[5])), started_at=row[6], plan_version=row[7],
            decision_lineage=json.loads(row[8]),
        )

    def _execute_run(self, run: Run) -> Run:
        completed = {name for name, state in run.task_states.items() if state in {"succeeded", "succeeded_fallback"}}
        pending = set(self.tasks) - completed
        failed = False
        while pending and not failed:
            ready = [
                self.tasks[name] for name in pending
                if self.tasks[name].dependencies <= completed
            ]
            if not ready:
                failed = True
                self._record(run, "safe_stop", reason="no runnable tasks")
                break
            runnable: list[Task] = []
            for task in ready:
                if task.high_impact and task.name not in run.approvals:
                    run.task_states[task.name] = "awaiting_approval"
                    self._record(run, "approval_required", task=task.name)
                    run.status = RunStatus.BLOCKED
                    run.context["latency_seconds"] = time.time() - run.started_at
                    self._persist(run)
                    return run
                runnable.append(task)
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(runnable))) as pool:
                futures = {pool.submit(self._execute, task, run): task for task in runnable}
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        future.result()
                        completed.add(task.name)
                        pending.remove(task.name)
                    except Exception as exc:  # bounded execution boundary
                        run.task_states[task.name] = "failed"
                        self._record(run, "task_failed", task=task.name, error=str(exc))
                        if task.rollback:
                            try:
                                task.rollback(run.context)
                                run.task_states[task.name] = "rolled_back"
                                run.status = RunStatus.ROLLED_BACK
                                self._record(run, "rollback", task=task.name)
                            except Exception as rollback_error:
                                self._record(run, "rollback_failed", task=task.name, error=str(rollback_error))
                        failed = True
                        break
            self._persist(run)
        if run.status not in {RunStatus.ROLLED_BACK, RunStatus.BLOCKED}:
            run.status = RunStatus.FAILED if failed else RunStatus.SUCCEEDED
        run.context["latency_seconds"] = time.time() - run.started_at
        self._record(run, "run_finished", status=run.status.value, plan_version=run.plan_version)
        self._persist(run)
        return run

    def _execute(self, task: Task, run: Run) -> None:
        self._record(run, "task_started", task=task.name)
        last_error: Exception | None = None
        for attempt in range(task.retries + 1):
            try:
                PolicyEngine.check(task, run.context)
                if task.entry_gate:
                    task.entry_gate(run.context)
                result = task.action(run.context)
                if task.exit_gate:
                    task.exit_gate(run.context)
                if result is not None:
                    run.context[task.name] = result
                run.task_states[task.name] = "succeeded"
                self._record(run, "task_succeeded", task=task.name, attempt=attempt + 1)
                return
            except (ApprovalRequired, PolicyViolation):
                raise
            except Exception as exc:
                last_error = exc
                self._record(run, "retry", task=task.name, attempt=attempt + 1, error=str(exc))
        if task.fallback:
            run.context[task.name] = task.fallback(run.context)
            run.task_states[task.name] = "succeeded_fallback"
            self._record(run, "fallback", task=task.name)
            return
        raise RuntimeError(f"{task.name} exhausted retries: {last_error}")

    def _descendants(self, roots: set[str]) -> set[str]:
        descendants: set[str] = set()
        changed = True
        while changed:
            changed = False
            for task in self.tasks.values():
                if task.name not in descendants and task.dependencies & (roots | descendants):
                    descendants.add(task.name)
                    changed = True
        return descendants

    def _persist(self, run: Run) -> None:
        self._database.execute(
            """INSERT OR REPLACE INTO runs
            (id,status,context,task_states,audit,approvals,started_at,plan_version,decision_lineage,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run.id, run.status.value if run.status else None, json.dumps(run.context),
             json.dumps(run.task_states), json.dumps(run.audit), json.dumps(sorted(run.approvals)),
             run.started_at, run.plan_version, json.dumps(run.decision_lineage), time.time()),
        )
        self._database.commit()

    @staticmethod
    def _record(run: Run, event: str, **details: Any) -> None:
        run.audit.append({"at": time.time(), "event": event, **details})

    @staticmethod
    def metrics(run: Run) -> dict[str, Any]:
        events = run.audit
        retries = sum(1 for event in events if event["event"] == "retry")
        rollbacks = sum(1 for event in events if event["event"] == "rollback")
        return {
            "success_rate": 1.0 if run.status == RunStatus.SUCCEEDED else 0.0,
            "retry_count": retries,
            "rollback_count": rollbacks,
            "latency_seconds": run.context.get("latency_seconds"),
            "mttr_seconds": run.context.get("latency_seconds") if rollbacks else 0.0,
        }
