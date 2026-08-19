import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from shortener import Handler, Service, URLStore, ValidationError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = URLStore()

    def tearDown(self):
        self.store.close()

    def test_create_resolve_and_analytics(self):
        link = self.store.create("https://example.com/docs", token="docs")
        self.assertEqual(self.store.resolve("docs", "test-agent", "127.0.0.1"), link["url"])
        self.assertEqual(self.store.stats("docs")["clicks"], 1)

    def test_rejects_unsafe_url_and_duplicate_token(self):
        with self.assertRaises(ValidationError):
            self.store.create("javascript:alert(1)")
        self.store.create("https://example.com", token="same")
        with self.assertRaises(ValidationError):
            self.store.create("https://other.example", token="same")

    def test_rejects_private_and_local_network_targets(self):
        for target in [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://[::1]/admin",
            "http://10.0.0.5/",
            "http://192.168.1.10/",
            "http://169.254.169.254/latest/meta-data",
        ]:
            with self.subTest(target=target):
                with self.assertRaises(ValidationError):
                    self.store.create(target)

    def test_expired_link_does_not_record_click(self):
        self.store.create("https://example.com", token="tiny", ttl_seconds=1)
        self.store.connection.execute("UPDATE links SET expires_at = 0 WHERE token = 'tiny'")
        self.store.connection.commit()
        self.assertIsNone(self.store.resolve("tiny"))
        self.assertEqual(self.store.stats("tiny")["clicks"], 0)


class HTTPTests(unittest.TestCase):
    def test_root_reports_service_status(self):
        store = URLStore()
        Handler.service = Service(store)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = json.loads(response.read())
        thread.join()
        server.server_close()
        store.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(body["status"], "ok")

    def test_create_redirect_and_stats_contract(self):
        store = URLStore()
        Handler.service = Service(store, "http://127.0.0.1")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        connection = HTTPConnection("127.0.0.1", server.server_port)

        def request(method, path, body=None, headers=None):
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            connection.request(method, path, body, headers or {})
            result = connection.getresponse()
            payload = result.read()
            thread.join()
            return result.status, result.getheader("Location"), payload

        status, _, payload = request(
            "POST", "/api/links", json.dumps({"url": "https://example.com", "token": "demo"}),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(payload)["token"], "demo")
        status, location, _ = request("GET", "/demo")
        self.assertEqual(status, 302)
        self.assertEqual(location, "https://example.com")
        status, _, payload = request("GET", "/api/links/demo/stats")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["clicks"], 1)
        server.server_close()
        store.close()

    def test_rate_limit_blocks_excess_requests(self):
        store = URLStore()
        service = Service(store, "http://127.0.0.1", max_requests_per_minute=2)
        Handler.service = service
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        connection = HTTPConnection("127.0.0.1", server.server_port)

        def request_once():
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            connection.request(
                "POST",
                "/api/links",
                json.dumps({"url": "https://example.com"}),
                {"Content-Type": "application/json"},
            )
            result = connection.getresponse()
            payload = result.read()
            thread.join()
            return result.status, payload

        status1, _ = request_once()
        status2, _ = request_once()
        status3, payload3 = request_once()
        server.server_close()
        store.close()

        self.assertEqual(status1, 201)
        self.assertEqual(status2, 201)
        self.assertEqual(status3, 429)
        self.assertIn("rate limit", json.loads(payload3).get("error", "").lower())


if __name__ == "__main__":
    unittest.main()
