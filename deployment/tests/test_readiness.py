"""Readiness probe behavior without a server, database or financial requests."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from deployment.readiness import wait_until_ready


class ReadinessTests(unittest.TestCase):
    """Require authenticated readiness and keep all failure output free of secrets."""

    def setUp(self) -> None:
        """Create a disposable credential file and mock the HTTP connection."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token = Path(self.directory.name) / "token"
        self.token.write_text("disposable-private-health-token\n")
        self.factory = patch("deployment.readiness.HTTPConnection").start()
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
        with contextlib.redirect_stdout(io.StringIO()) as output:
            wait_until_ready(self.token, attempts=2, interval=0)
        self.assertEqual(self.connection.request.call_count, 2)
        self.connection.request.assert_called_with(
            "GET",
            "/health/ready",
            headers={"Authorization": "Bearer disposable-private-health-token"},
        )
        self.assertNotIn("disposable-private-health-token", output.getvalue())
        self.assertEqual(self.connection.close.call_count, 2)

    def test_authentication_failure_stops_without_retry(self) -> None:
        """Invalid deployment credentials fail immediately and never appear in errors."""
        for status in (401, 403):
            with self.subTest(status=status):
                self.connection.reset_mock()
                self.connection.getresponse.return_value = self.response(status)
                with self.assertRaisesRegex(
                    SystemExit, "^Deployment health authentication failed$"
                ):
                    wait_until_ready(self.token, attempts=2, interval=0)
                self.assertEqual(self.connection.request.call_count, 1)

    def test_redirect_or_wrong_success_body_is_not_readiness(self) -> None:
        """Do not follow redirects with credentials or accept another service's response."""
        for status, body in ((302, b""), (200, b"not json"), (200, b'{"status":"ok"}')):
            with self.subTest(status=status, body=body):
                self.connection.reset_mock()
                self.connection.getresponse.return_value = self.response(status, body)
                with self.assertRaisesRegex(SystemExit, "^Deployment readiness timed out;"):
                    wait_until_ready(self.token, attempts=1, interval=0)
                self.assertEqual(self.connection.request.call_count, 1)

    def test_connection_failure_times_out(self) -> None:
        """Connection failure stays a bounded deployment failure."""
        self.connection.request.side_effect = ConnectionRefusedError()
        with self.assertRaisesRegex(SystemExit, "^Deployment readiness timed out;"):
            wait_until_ready(self.token, attempts=2, interval=0)
        self.assertEqual(self.connection.close.call_count, 2)

    def test_invalid_credential_never_sends_a_request(self) -> None:
        """Reject missing or whitespace-containing credentials before HTTP."""
        for value in ("", "two tokens"):
            self.token.write_text(value)
            with self.assertRaisesRegex(SystemExit, "^Invalid deployment health credential$"):
                wait_until_ready(self.token)
        self.factory.assert_not_called()
