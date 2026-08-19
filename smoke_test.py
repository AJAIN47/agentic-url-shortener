#!/usr/bin/env python3
"""Simple smoke test for the URL shortener API."""

import json
from http.client import HTTPConnection

BASE = "127.0.0.1"
PORT = 8080


def request(method: str, path: str, body: str | None = None, headers: dict[str, str] | None = None):
    conn = HTTPConnection(BASE, PORT, timeout=5)
    conn.request(method, path, body, headers or {})
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, response.getheader("Location"), payload.decode()


if __name__ == "__main__":
    status, _, payload = request(
        "POST",
        "/api/links",
        json.dumps({"url": "https://example.com", "token": "demo-smoke"}),
        {"Content-Type": "application/json"},
    )
    print("CREATE", status, payload)
    status, location, _ = request("GET", "/demo-smoke")
    print("REDIRECT", status, location)
    status, _, payload = request("GET", "/api/links/demo-smoke/stats")
    print("STATS", status, payload)
