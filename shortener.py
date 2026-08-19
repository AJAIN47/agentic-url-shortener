"""URL shortener service with SQLite persistence and analytics."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import dumps, loads
from typing import Any
from urllib.parse import urlparse

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")


class ValidationError(ValueError):
    pass


class URLStore:
    def __init__(self, database: str = ":memory:") -> None:
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS links (
                token TEXT PRIMARY KEY, url TEXT NOT NULL, created_at REAL NOT NULL,
                expires_at REAL, clicks INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL,
                clicked_at REAL NOT NULL, user_agent TEXT, client_ip TEXT
            );
            CREATE INDEX IF NOT EXISTS clicks_token_idx ON clicks(token);
            """
        )

    @staticmethod
    def validate_url(url: str) -> None:
        candidate = (url or "").strip()
        if not candidate:
            raise ValidationError("url is required")
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError("url must be an absolute http or https URL")
        if len(candidate) > 2048:
            raise ValidationError("url must be 2048 characters or fewer")
        hostname = parsed.hostname
        if not hostname:
            raise ValidationError("url must include a valid hostname")
        lower_host = hostname.lower()
        if lower_host.endswith(".local") or lower_host == "localhost":
            raise ValidationError("url must not target local or private network hosts")
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            # Hostnames are allowed if they resolve externally. We reject the common local-only suffixes above.
            return
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValidationError("url must not target local or private network addresses")

    def create(self, url: str, token: str | None = None, ttl_seconds: int | None = None) -> dict[str, Any]:
        self.validate_url(url)
        if ttl_seconds is not None and (ttl_seconds <= 0 or ttl_seconds > 31_536_000):
            raise ValidationError("ttl_seconds must be between 1 and 31536000")
        token = token or hashlib.sha256(f"{url}:{time.time_ns()}".encode()).hexdigest()[:8]
        if not TOKEN_RE.fullmatch(token):
            raise ValidationError("token must be 4-32 URL-safe characters")
        now = time.time()
        expires = now + ttl_seconds if ttl_seconds else None
        with self.lock:
            try:
                self.connection.execute(
                    "INSERT INTO links(token,url,created_at,expires_at) VALUES(?,?,?,?)",
                    (token, url, now, expires),
                )
                self.connection.commit()
            except sqlite3.IntegrityError as exc:
                raise ValidationError("token already exists") from exc
        return {"token": token, "url": url, "created_at": now, "expires_at": expires}

    def resolve(self, token: str, user_agent: str = "", client_ip: str = "") -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT url, expires_at FROM links WHERE token = ?", (token,)
            ).fetchone()
            if not row or (row["expires_at"] is not None and row["expires_at"] <= time.time()):
                return None
            now = time.time()
            self.connection.execute("UPDATE links SET clicks = clicks + 1 WHERE token = ?", (token,))
            self.connection.execute(
                "INSERT INTO clicks(token,clicked_at,user_agent,client_ip) VALUES(?,?,?,?)",
                (token, now, user_agent[:256], client_ip[:64]),
            )
            self.connection.commit()
            return str(row["url"])

    def stats(self, token: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT token,url,created_at,expires_at,clicks FROM links WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def close(self) -> None:
        self.connection.close()


@dataclass
class Service:
    store: URLStore
    public_base: str = "http://localhost:8080"
    max_requests_per_minute: int | None = None
    rate_limit_window_seconds: int = 60
    _request_times: dict[str, deque[float]] = field(default_factory=dict, init=False, repr=False)
    _request_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def is_rate_limited(self, client_ip: str) -> bool:
        if self.max_requests_per_minute is None:
            return False
        now = time.time()
        with self._request_lock:
            window = self._request_times.setdefault(client_ip, deque())
            while window and now - window[0] > self.rate_limit_window_seconds:
                window.popleft()
            if len(window) >= self.max_requests_per_minute:
                return True
            window.append(now)
            return False


class Handler(BaseHTTPRequestHandler):
    service: Service

    def _json(self, status: int, body: dict[str, Any]) -> None:
        data = dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/links":
            self._json(404, {"error": "not found"})
            return
        client_ip = self.client_address[0]
        if self.service.is_rate_limited(client_ip):
            self._json(429, {"error": "rate limit exceeded: too many requests from this client"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValidationError("request body too large or missing")
            raw = self.rfile.read(length)
            if not raw.strip():
                raise ValidationError("request body cannot be empty")
            payload = loads(raw)
            if not isinstance(payload, dict):
                raise ValidationError("request JSON must be an object")
            url = payload.get("url")
            if not isinstance(url, str):
                raise ValidationError("url is required")
            link = self.service.store.create(url, payload.get("token"), payload.get("ttl_seconds"))
            link["short_url"] = f"{self.service.public_base}/{link['token']}"
            self._json(201, link)
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON payload"})
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            self._json(400, {"error": str(exc) or "invalid JSON"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._json(200, {
                "service": "agentic-url-shortener",
                "status": "ok",
                "create": "POST /api/links",
                "redirect": "GET /{token}",
                "stats": "GET /api/links/{token}/stats",
            })
            return
        if self.path.startswith("/api/links/") and self.path.endswith("/stats"):
            token = self.path[len("/api/links/") : -len("/stats")]
            result = self.service.store.stats(token)
            self._json(200, result) if result else self._json(404, {"error": "link not found"})
            return
        if self.path.startswith("/") and self.path.count("/") == 1:
            token = self.path[1:]
            url = self.service.store.resolve(token, self.headers.get("User-Agent", ""), self.client_address[0])
            if url:
                self.send_response(302)
                self.send_header("Location", url)
                self.end_headers()
            else:
                self._json(404, {"error": "link not found or expired"})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, *_args: Any) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8080, database: str = "shortener.db", max_requests_per_minute: int | None = None) -> None:
    service = Service(
        URLStore(database),
        f"http://{host}:{port}",
        max_requests_per_minute=max_requests_per_minute,
    )
    Handler.service = service
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        print(f"URL shortener listening at {service.public_base}")
        server.serve_forever()
    finally:
        server.server_close()
        service.store.close()


if __name__ == "__main__":
    import os

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    database = os.getenv("DATABASE", "shortener.db")
    max_rpm = os.getenv("MAX_REQUESTS_PER_MINUTE")
    serve(
        host=host,
        port=port,
        database=database,
        max_requests_per_minute=int(max_rpm) if max_rpm else None,
    )
