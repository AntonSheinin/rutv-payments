"""Validate profile isolation using Compose rendering only, never running services."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class ComposeProfileTests(unittest.TestCase):
    """Confirm local and live deployments inject exclusively their selected dotenv file."""

    def test_local_and_production_profiles_are_isolated(self) -> None:
        """A production-only server must not need or read .env.dev."""
        repository = Path(__file__).resolve().parents[2]
        template = (repository / ".env.example").read_text()
        keys = {
            line.split("=", 1)[0]
            for line in template.splitlines()
            if line and not line.startswith("#")
        }
        environment = {key: value for key, value in os.environ.items() if key not in keys}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for profile in ("dev", "prod"):
                # These values are disposable markers, not real provider/server credentials.
                prefix = (
                    f"POSTGRES_PASSWORD={profile}-disposable\n"
                    f"APP_SECRET={profile}-disposable-signing-secret-more-than-32-characters\n"
                    f"PAYPAL_ENVIRONMENT={'sandbox' if profile == 'dev' else 'live'}\n"
                    f"COOKIE_SECURE={'false' if profile == 'dev' else 'true'}\n"
                    "ALLOWED_IPS=62.90.52.94\n"
                )
                replaced = {line.split("=", 1)[0] for line in prefix.splitlines()}
                body = "\n".join(
                    line
                    for line in template.splitlines()
                    if not line or line.startswith("#") or line.split("=", 1)[0] not in replaced
                )
                (root / f".env.{profile}").write_text(prefix + body + "\n")

            def configuration(profile: str) -> dict:
                """Render Compose JSON in memory without displaying resolved values."""
                command = [
                    "docker",
                    "compose",
                    "--project-directory",
                    str(root),
                    "--env-file",
                    str(root / f".env.{profile}"),
                    "-f",
                    str(repository / "docker-compose.yml"),
                ]
                result = subprocess.run(
                    command + ["config", "--format", "json"],
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return json.loads(result.stdout)

            local = configuration("dev")
            self.assertEqual(
                local["services"]["api"]["environment"]["PAYPAL_ENVIRONMENT"], "sandbox"
            )
            self.assertEqual(
                local["services"]["api"]["environment"]["POSTGRES_PASSWORD"], "dev-disposable"
            )
            (root / ".env.dev").unlink()
            production = configuration("prod")
            api, db = production["services"]["api"], production["services"]["db"]
            self.assertEqual(set(production["services"]), {"api", "db"})
            self.assertEqual(api["environment"]["PAYPAL_ENVIRONMENT"], "live")
            self.assertEqual(api["environment"]["COOKIE_SECURE"], "true")
            self.assertEqual(api["environment"]["POSTGRES_PASSWORD"], "prod-disposable")
            self.assertEqual(db["environment"]["POSTGRES_PASSWORD"], "prod-disposable")
            self.assertEqual(api["ports"][0]["host_ip"], "127.0.0.1")
            self.assertEqual(set(api["environment"]), keys)
            self.assertEqual(api["image"], "rutv-payments:production")
            self.assertEqual(api["pull_policy"], "never")
            self.assertEqual(set(production["networks"]), {"default"})
            self.assertNotIn("ports", db)
