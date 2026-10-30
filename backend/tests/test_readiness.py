"""Readiness probe behavior without a server, database or financial requests."""

import unittest
from unittest.mock import MagicMock, patch

from app.readiness import wait_until_ready


class ReadinessTests(unittest.TestCase):
    """Require the exact running-process readiness result over container loopback."""

    def setUp(self) -> None:
        """Mock the HTTP connection without any token or credential file."""
        self.factory = patch("app.readiness.HTTPConnection").start()
        self.addCleanup(patch.stopall)
        self.connection = self.factory.return_value

    def response(self, status: int, body: bytes = b"") -> MagicMock:
        """Construct an HTTP response with an explicit status and bounded body."""
        response = MagicMock(status=status)
        response.read.return_value = body
        return response

    def test_retries_unready_then_checks_exact_readiness(self) -> None:
        """A temporary unavailable result can recover through a GET-only probe."""
        self.connection.getresponse.side_effect = [
            self.response(503),
            self.response(200, b'{"status":"ready"}'),
        ]
        wait_until_ready(attempts=2, interval=0)
        self.assertEqual(self.connection.request.call_count, 2)
        self.connection.request.assert_called_with(
            "GET",
            "/internal/ready",
        )
        self.factory.assert_called_with("127.0.0.1", 8000, timeout=3)
        self.assertEqual(self.connection.close.call_count, 2)

    def test_access_failure_stops_without_retry(self) -> None:
        """A misconfigured internal endpoint must fail deployment immediately."""
        for status in (401, 403):
            with self.subTest(status=status):
                self.connection.reset_mock()
                self.connection.getresponse.return_value = self.response(status)
                with self.assertRaisesRegex(SystemExit, "^Container readiness access rejected$"):
                    wait_until_ready(attempts=2, interval=0)
                self.assertEqual(self.connection.request.call_count, 1)

    def test_redirect_or_wrong_success_body_is_not_readiness(self) -> None:
        """Do not follow redirects or accept another service's response."""
        for status, body in ((302, b""), (200, b"not json"), (200, b'{"status":"ok"}')):
            with self.subTest(status=status, body=body):
                self.connection.reset_mock()
                self.connection.getresponse.return_value = self.response(status, body)
                with self.assertRaisesRegex(SystemExit, "^Deployment readiness timed out;"):
                    wait_until_ready(attempts=1, interval=0)
                self.assertEqual(self.connection.request.call_count, 1)

    def test_connection_failure_times_out(self) -> None:
        """Connection failure stays a bounded deployment failure."""
        self.connection.request.side_effect = ConnectionRefusedError()
        with self.assertRaisesRegex(SystemExit, "^Deployment readiness timed out;"):
            wait_until_ready(attempts=2, interval=0)
        self.assertEqual(self.connection.close.call_count, 2)
