"""Probe private local readiness without printing credentials or provider/customer data."""

import json
import sys
import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path


def wait_until_ready(token_path: Path, attempts: int = 60, interval: float = 2) -> None:
    """Wait for authenticated database/ownership readiness; never call PayPal."""
    token = token_path.read_text().strip()
    if not token or any(character.isspace() for character in token):
        raise SystemExit("Invalid deployment health credential")
    for attempt in range(attempts):
        # Direct HTTP never follows redirects or forwards this credential to a proxy.
        connection = HTTPConnection("127.0.0.1", 8100, timeout=3)
        try:
            connection.request("GET", "/health/ready", headers={"Authorization": f"Bearer {token}"})
            response = connection.getresponse()
            if response.status in {401, 403}:
                raise SystemExit("Deployment health authentication failed") from None
            if response.status == 200 and json.loads(response.read(4096)) == {"status": "ready"}:
                print("Authenticated readiness passed")
                return
        except (OSError, HTTPException, json.JSONDecodeError, UnicodeDecodeError):
            pass
        finally:
            connection.close()
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise SystemExit("Deployment readiness timed out; inspect the server without replaying actions")


if __name__ == "__main__":
    wait_until_ready(Path(sys.argv[1]))
