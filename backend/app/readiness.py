"""Probe the running API from inside its container without credentials or provider calls."""

import json
import time
from http.client import HTTPConnection, HTTPException


def wait_until_ready(attempts: int = 60, interval: float = 2) -> None:
    """Wait for the running process's database/ownership checks over container loopback."""
    for attempt in range(attempts):
        # HTTPConnection bypasses environment proxies and never follows redirects.
        connection = HTTPConnection("127.0.0.1", 8000, timeout=3)
        try:
            connection.request("GET", "/internal/ready")
            response = connection.getresponse()
            if response.status in {401, 403}:
                raise SystemExit("Container readiness access rejected") from None
            if response.status == 200 and json.loads(response.read(4096)) == {"status": "ready"}:
                print("Container readiness passed")
                return
        except OSError, HTTPException, json.JSONDecodeError, UnicodeDecodeError:
            pass
        finally:
            connection.close()
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise SystemExit("Deployment readiness timed out; inspect the server without replaying actions")


if __name__ == "__main__":
    wait_until_ready()
