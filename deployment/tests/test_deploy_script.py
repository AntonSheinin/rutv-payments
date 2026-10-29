"""Exercise deployment ordering and failure handling with local command doubles."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class DeployScriptTests(unittest.TestCase):
    """Keep the release pointer unchanged until authenticated readiness succeeds."""

    def setUp(self) -> None:
        """Create a disposable release tree and commands that cannot contact Docker."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.release = self.root / "releases" / "new"
        (self.release / "deployment").mkdir(parents=True)
        script = Path(__file__).resolve().parents[1] / "deploy.sh"
        shutil.copyfile(script, self.release / "deployment" / "deploy.sh")
        (self.root / ".env.prod").write_text("DISPOSABLE=true\n")
        (self.root / ".health-api-key").write_text("disposable-token\n")
        self.previous = self.root / "releases" / "previous"
        self.previous.mkdir()
        (self.root / "current").symlink_to(self.previous)
        commands = self.root / "bin"
        commands.mkdir()
        self.log = self.root / "commands.log"
        docker = commands / "docker"
        docker.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\n'
            'if [[ "$*" == *"config --quiet"* ]]; then exit "${CONFIG_EXIT:-0}"; fi\n'
        )
        docker.chmod(0o700)
        python = commands / "python3"
        python.write_text('#!/bin/bash\nexit "${READINESS_EXIT:-0}"\n')
        python.chmod(0o700)
        self.environment = {
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "DEPLOY_TEST_LOG": str(self.log),
        }

    def deploy(self, **settings: str) -> subprocess.CompletedProcess[str]:
        """Run the real shell script against isolated fake Docker/readiness commands."""
        return subprocess.run(
            [
                "bash",
                str(self.release / "deployment" / "deploy.sh"),
                str(self.root),
                "ghcr.io/example/payments@sha256:" + "a" * 64,
            ],
            env={**self.environment, **settings},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_success_pulls_before_replacing_api_and_updates_pointer(self) -> None:
        """Only a ready deployment becomes current, with a stable Compose project."""
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text().splitlines()
        self.assertIn("config --quiet", commands[0])
        self.assertTrue(commands[2].startswith("pull ghcr.io/example/payments@sha256:"))
        self.assertTrue(commands[3].startswith("tag ghcr.io/example/payments@sha256:"))
        self.assertTrue(commands[3].endswith("rutv-payments:production"))
        self.assertTrue(commands[4].endswith("up -d --no-build --wait --wait-timeout 90 db"))
        self.assertTrue(commands[5].endswith("up -d --no-build --no-deps api"))
        self.assertIn("--project-name rutv-payments", commands[5])
        self.assertFalse((self.release / "image.env").exists())
        self.assertEqual(
            (self.release / "image.txt").read_text().strip(),
            "ghcr.io/example/payments@sha256:" + "a" * 64,
        )
        self.assertEqual((self.root / "current").resolve(), self.release)

    def test_failed_readiness_preserves_previous_pointer_without_rollback(self) -> None:
        """An API may already be replaced when readiness fails; never replay or roll back."""
        result = self.deploy(READINESS_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "current").resolve(), self.previous)
        self.assertEqual(len(self.log.read_text().splitlines()), 6)

    def test_invalid_configuration_stops_before_pull_or_container_changes(self) -> None:
        """A failed preflight must leave the running deployment untouched."""
        result = self.deploy(CONFIG_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertEqual((self.root / "current").resolve(), self.previous)
