"""Run agent commands inside an isolated Docker container (v0.2).

The signing key and log files stay on the HOST. The container only sees
the workspace folder, has no network by default, a read-only filesystem,
no Linux capabilities, and resource limits.
"""
import os
import subprocess
import time
import uuid

from .executor import build_observation
from .streaming import DEFAULT_MAX_STREAM_BYTES, run_streaming

DEFAULT_IMAGE = "python:3.12-slim"


class DockerNotAvailable(RuntimeError):
    pass


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class DockerSandbox:
    def __init__(self, workspace="./workspace-docker", image=DEFAULT_IMAGE,
                 network="none", memory="512m", cpus="1", pids=128):
        self.workspace = os.path.abspath(workspace)
        self.image, self.network = image, network
        self.memory, self.cpus, self.pids = memory, cpus, pids
        self.name = f"agentbox-{uuid.uuid4().hex[:8]}"
        self.started = False

    def describe(self) -> dict:
        return {"backend": "docker", "image": self.image, "network": self.network,
                "memory": self.memory, "cpus": self.cpus, "pids_limit": self.pids,
                "read_only_rootfs": True, "cap_drop": "ALL"}

    def start(self):
        if not docker_available():
            raise DockerNotAvailable("Docker is not running or not installed.")
        os.makedirs(self.workspace, exist_ok=True)
        cmd = [
            "docker", "run", "-d", "--rm", "--name", self.name,
            "--network", self.network,
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", str(self.pids),
            "--memory", self.memory, "--memory-swap", self.memory,
            "--cpus", str(self.cpus),
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp",
            "-v", f"{self.workspace}:/workspace",
            "-w", "/workspace",
            self.image, "sleep", "infinity",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise DockerNotAvailable(f"Could not start container: {r.stderr.strip()}")
        self.started = True

    def stop(self):
        if self.started:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
            self.started = False

    def _kill_stragglers(self):
        """Kill everything in the container except PID 1 (the idle `sleep`)."""
        try:
            subprocess.run(["docker", "exec", self.name, "sh", "-c", 'for p in /proc/[0-9]*; do pid=${p#/proc/}; if [ "$pid" -gt 1 ] && [ "$pid" != "$$" ]; then kill -9 "$pid" 2>/dev/null; fi; done'],
                           capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def run(self, cmd: str, timeout_s: int = 60,
            max_output_bytes: int = DEFAULT_MAX_STREAM_BYTES) -> dict:
        if not self.started:
            raise RuntimeError("Call start() before run().")
        argv = ["docker", "exec", "-w", "/workspace", self.name,
                "timeout", "-k", "2", str(timeout_s),
                "bash", "--noprofile", "--norc", "-c", cmd]
        t0 = time.monotonic()
        # The in-container `timeout` is the normal deadline (exit 124); the host-side
        # deadline is a backstop if `docker exec` itself hangs.
        code, out, err, timed_out, exceeded = run_streaming(
            argv, cwd=None, timeout_s=timeout_s + 10, max_bytes=max_output_bytes,
            cleanup=self._kill_stragglers)
        return build_observation(cmd, code, out, err, timed_out or code == 124, exceeded,
                                 self.workspace, int((time.monotonic() - t0) * 1000))
