import unittest
from tempfile import NamedTemporaryFile

from orchestrator import Orchestrator, PolicyViolation, RunStatus, Task
from scenarios import build_scenario


class OrchestratorTests(unittest.TestCase):
    def test_parallel_ready_tasks_and_dependency_barrier(self):
        order = []
        tasks = [
            Task("root", lambda c: order.append("root")),
            Task("left", lambda c: order.append("left"), {"root"}),
            Task("right", lambda c: order.append("right"), {"root"}),
            Task("join", lambda c: order.append("join"), {"left", "right"}),
        ]
        run = Orchestrator(tasks).run()
        self.assertEqual(run.status, RunStatus.SUCCEEDED)
        self.assertEqual(order[0], "root")
        self.assertEqual(order[-1], "join")

    def test_high_impact_task_requires_approval(self):
        run = build_scenario("greenfield").run({"requirement": "x"})
        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertEqual(run.task_states["implementation"], "awaiting_approval")

    def test_retries_then_succeeds(self):
        attempts = []
        def flaky(context):
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("transient")
        run = Orchestrator([Task("flaky", flaky, retries=2)]).run()
        self.assertEqual(run.status, RunStatus.SUCCEEDED)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(Orchestrator.metrics(run)["retry_count"], 1)

    def test_blocked_run_persists_and_can_resume(self):
        with NamedTemporaryFile() as state:
            orchestrator = build_scenario("greenfield", state.name)
            run = orchestrator.run({"requirement": "build", "change_ticket": "CHG-1"})
            self.assertEqual(run.status, RunStatus.BLOCKED)
            resumed = orchestrator.resume(run.id, {"implementation", "release"})
            self.assertEqual(resumed.status, RunStatus.SUCCEEDED)
            self.assertEqual(orchestrator.load(run.id).status, RunStatus.SUCCEEDED)

    def test_replan_invalidates_downstream_tasks_and_preserves_lineage(self):
        with NamedTemporaryFile() as state:
            observed = []
            tasks = [
                Task("requirements", lambda c: observed.append(c["version"]) or c["version"]),
                Task("architecture", lambda c: "architecture-" + c["requirements"], {"requirements"}),
                Task("release", lambda c: "released", {"architecture"}),
            ]
            orchestrator = Orchestrator(tasks, state_path=state.name)
            run = orchestrator.run({"version": "one"})
            replanned = orchestrator.replan(run.id, {"version": "two"}, {"requirements"})
            self.assertEqual(replanned.status, RunStatus.SUCCEEDED)
            self.assertEqual(observed, ["one", "two"])
            self.assertEqual(replanned.plan_version, 2)
            self.assertTrue(any(item["event"] == "plan_revised" for item in replanned.decision_lineage))

    def test_policy_gate_rejects_noncompliant_context(self):
        task = Task("release", lambda c: "released", policy_tags={"production"})
        run = Orchestrator([task]).run({"retention_days": 400, "change_ticket": "CHG-1"})
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertTrue(any(event["event"] == "task_failed" for event in run.audit))

    def test_failed_task_rolls_back_and_reports_rollback_status(self):
        task = Task("deploy", lambda c: (_ for _ in ()).throw(RuntimeError("broken")), retries=0, rollback=lambda c: c.update({"reverted": True}))
        run = Orchestrator([task]).run()
        self.assertEqual(run.status, RunStatus.ROLLED_BACK)
        self.assertTrue(run.context["reverted"])
        self.assertEqual(Orchestrator.metrics(run)["rollback_count"], 1)


if __name__ == "__main__":
    unittest.main()
