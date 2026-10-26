import os
import subprocess
import time
import uuid

from .executor import _clip, _txt, error_signature, fingerprint, workspace_hash
from .redact import redact_all

DEFAULT_IMAGE = "python:3.12-slim"


class DockerNotAvailable(RuntimeError):
    pass


def docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class DockerSandbox:
    def __init__(
        self,
        workspace="./workspace-docker",
        image=DEFAULT_IMAGE,
        network="none",
        memory="512m",
        cpus="1",
        pids=128,
    ):
        self.workspace = os.path.abspath(workspace)
        self.image = image
        self.network = network
        self.memory = memory
        self.cpus = cpus
        self.pids = pids
        self.name = f"agentbox-{uuid.uuid4().hex[:8]}"
        self.started = False

    def describe(self) -> dict:
        return {
            "backend": "docker",
            "image": self.image,
            "network": self.network,
            "memory": self.memory,
            "cpus": self.cpus,
            "pids_limit": self.pids,
            "read_only_rootfs": True,
            "cap_drop": "ALL",
        }

    def start(self):
        if not docker_available():
            raise DockerNotAvailable(
                "Docker is not running or not installed."
            )

        os.makedirs(self.workspace, exist_ok=True)

        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            self.name,
            "--network",
            self.network,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.pids),
            "--memory",
            self.memory,
            "--memory-swap",
            self.memory,
            "--cpus",
            str(self.cpus),
        ]

        # os.getuid() and os.getgid() are unavailable on Windows.
        # Preserve UID/GID mapping on Unix-like hosts.
        if os.name != "nt":
            cmd.extend([
                "--user",
                f"{os.getuid()}:{os.getgid()}",
            ])

        cmd.extend([
            "-e",
            "HOME=/tmp",
            "-v",
            f"{self.workspace}:/workspace",
            "-w",
            "/workspace",
            self.image,
            "sleep",
            "infinity",
        ])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise DockerNotAvailable(
                f"Could not start container: {result.stderr.strip()}"
            )

        self.started = True

    def stop(self):

        if not self.started:
            return

        result = subprocess.run(
            ["docker", "rm", "-f", self.name],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise DockerNotAvailable(
                f"Could not stop container: {result.stderr.strip()}"
            )

        self.started = False

    def run(self, cmd: str, timeout_s: int = 60) -> dict:

        if not self.started:
            raise RuntimeError("Call start() before run().")

        argv = [
            "docker",
            "exec",
            "-w",
            "/workspace",
            self.name,
            "timeout",
            "-k",
            "2",
            str(timeout_s),
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            cmd,
        ]

        start_time = time.monotonic()

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s + 10,
            )
            code = result.returncode
            out = result.stdout
            err = result.stderr

        except subprocess.TimeoutExpired as exc:
            code = 124
            out = _txt(exc.stdout)
            err = _txt(exc.stderr)

        cmd, out, err, redactions = redact_all(cmd, out, err)
        signature, error_class = error_signature(err, code)

        return {
            "redactions": redactions,
            "command": cmd,
            "command_fingerprint": fingerprint(cmd),
            "exit_code": code,
            "timed_out": code == 124,
            "stdout": _clip(out),
            "stderr": _clip(err),
            "error_signature": signature,
            "error_class": error_class,
            "workspace_hash": workspace_hash(self.workspace),
            "duration_ms": int(
                (time.monotonic() - start_time) * 1000
            ),
        }
