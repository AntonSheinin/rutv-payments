"""Exercise the workflow's inline deployment against isolated command doubles."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


class DeployWorkflowTests(unittest.TestCase):
    """Keep the release pointer unchanged until container readiness succeeds."""

    def setUp(self) -> None:
        """Create a disposable release tree and commands that cannot contact Docker."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.release = self.root / "releases" / "new"
        self.release.mkdir(parents=True)
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
                str(self.release),
                "ghcr.io/example/payments@sha256:" + "a" * 64,
            ],
            env={**self.environment, **settings},
            input=self.remote_script,
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
        self.assertTrue(commands[6].endswith("exec -T api python -m app.readiness"))
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
        self.assertTrue(
            self.log.read_text().splitlines()[-1].endswith("exec -T api python -m app.readiness")
        )

    def test_workflow_requires_manual_dispatch(self) -> None:
        """Pushes must never deploy; only an explicit dispatch starts the workflow."""
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})

    def test_invalid_configuration_stops_before_pull_or_container_changes(self) -> None:
        """A failed preflight must leave the running deployment untouched."""
        result = self.deploy(CONFIG_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertEqual((self.root / "current").resolve(), self.previous)

    def test_runner_transfers_only_manifest_and_streams_commands(self) -> None:
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
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": "1",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["stdin"], (repository / "docker-compose.yml").read_text())
        self.assertEqual(calls[1]["stdin"].strip(), self.remote_script.strip())
        self.assertIn("StrictHostKeyChecking=yes", calls[0]["args"])
        args = calls[0]["args"]
        self.assertFalse(Path(args[args.index("-i") + 1]).exists())
        self.assertNotIn("disposable-private-key", result.stdout + result.stderr)
