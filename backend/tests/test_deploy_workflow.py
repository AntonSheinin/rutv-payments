"""Exercise the workflow's inline deployment against isolated command doubles."""

import base64
import fcntl
import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


class DeployWorkflowTests(unittest.TestCase):
    """Exercise preflight, locking and readiness without real deployment commands."""

    def setUp(self) -> None:
        """Create a disposable deployment directory and commands that cannot contact Docker."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/deploy.yml"
        self.workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
        step = next(
            step for step in self.workflow["jobs"]["deploy"]["steps"] if step.get("id") == "deploy"
        )
        self.runner_script = step["run"]
        match = re.search(r"<<'REMOTE'\n(.*?)\nREMOTE(?:\n|$)", self.runner_script, re.DOTALL)
        assert match is not None
        self.remote_script = match[1]
        (self.root / ".env.prod").write_text("DISPOSABLE=true\n")
        self.previous_manifest = "services: {}\n"
        (self.root / "docker-compose.yml").write_text(self.previous_manifest)
        self.manifest = (workflow_path.parents[2] / "docker-compose.yml").read_text()
        commands = self.root / "bin"
        commands.mkdir()
        self.log = self.root / "commands.log"
        docker = commands / "docker"
        docker.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\n'
            'if [[ "$*" == *"config --quiet"* ]]; then exit "${CONFIG_EXIT:-0}"; fi\n'
            'if [[ "$*" == *"exec -T api python -m app.readiness"* ]]; '
            'then exit "${READINESS_EXIT:-0}"; fi\n'
        )
        docker.chmod(0o700)
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
                "-s",
                "--",
                str(self.root),
                "ghcr.io/example/payments@sha256:" + "a" * 64,
                base64.b64encode(self.manifest.encode()).decode(),
            ],
            env={**self.environment, **settings},
            input=self.remote_script,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_success_pulls_before_startup_and_checks_readiness(self) -> None:
        """Install one manifest and check readiness after Compose starts both services."""
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text().splitlines()
        self.assertIn("config --quiet", commands[0])
        self.assertTrue(commands[1].startswith("pull ghcr.io/example/payments@sha256:"))
        self.assertTrue(commands[2].endswith("rutv-payments:production"))
        self.assertIn("--project-name rutv-payments", commands[3])
        self.assertTrue(commands[3].endswith("up -d --no-build"))
        self.assertTrue(commands[4].endswith("exec -T api python -m app.readiness"))
        self.assertEqual((self.root / "docker-compose.yml").read_text(), self.manifest)
        self.assertFalse(list(self.root.glob(".compose.*")))

    def test_failed_readiness_stops_without_rollback(self) -> None:
        """A failed readiness check must fail the run without replay or automatic rollback."""
        result = self.deploy(READINESS_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Deployment ready:", result.stdout)
        self.assertTrue(
            self.log.read_text().splitlines()[-1].endswith("exec -T api python -m app.readiness")
        )

    def test_server_lock_blocks_manifest_and_container_changes(self) -> None:
        """An earlier remote deployment can outlive its GitHub runner connection."""
        with (self.root / ".deploy.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertEqual((self.root / "docker-compose.yml").read_text(), self.previous_manifest)

    def test_workflow_requires_manual_dispatch(self) -> None:
        """Pushes must never deploy; only an explicit dispatch starts the workflow."""
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})

    def test_invalid_configuration_stops_before_pull_or_container_changes(self) -> None:
        """A failed preflight must leave the running deployment untouched."""
        result = self.deploy(CONFIG_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertEqual((self.root / "docker-compose.yml").read_text(), self.previous_manifest)
        self.assertFalse(list(self.root.glob(".compose.*")))

    def test_runner_streams_manifest_and_commands_in_one_verified_session(self) -> None:
        """Exercise runner packaging with a fake SSH client that never contacts a server."""
        ssh = self.root / "bin" / "ssh"
        log = self.root / "ssh.jsonl"
        ssh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "key = pathlib.Path(args[args.index('-i') + 1])\n"
            "assert key.stat().st_mode & 0o777 == 0o600\n"
            "with open(os.environ['SSH_TEST_LOG'], 'a') as output:\n"
            "    output.write(json.dumps({'args': args, 'stdin': sys.stdin.read()}) + '\\n')\n"
        )
        ssh.chmod(0o700)
        repository = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["bash", "-s"],
            input=self.runner_script,
            cwd=repository,
            env={
                **self.environment,
                "SSH_TEST_LOG": str(log),
                "TMPDIR": str(self.root),
                "DEPLOY_HOST": "example.invalid",
                "DEPLOY_USER": "test",
                "DEPLOY_PORT": "22",
                "DEPLOY_PATH": str(self.root),
                "DEPLOY_KNOWN_HOSTS": "disposable-host-key",
                "DEPLOY_SSH_PRIVATE_KEY": "disposable-private-key",
                "IMAGE_REF": "ghcr.io/example/payments@sha256:" + "a" * 64,
                "GITHUB_SHA": "b" * 40,
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        arguments = shlex.split(calls[0]["args"][-1])
        self.assertEqual(base64.b64decode(arguments[-1]).decode(), self.manifest)
        self.assertEqual(calls[0]["stdin"].strip(), self.remote_script.strip())
        self.assertIn("StrictHostKeyChecking=yes", calls[0]["args"])
        args = calls[0]["args"]
        self.assertFalse(Path(args[args.index("-i") + 1]).exists())
        self.assertNotIn("disposable-private-key", result.stdout + result.stderr)
