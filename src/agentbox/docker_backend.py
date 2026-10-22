"""Run agent commands inside an isolated Docker container (v0.2).

The signing key and log files stay on the HOST. The container only sees
the workspace folder, has no network by default, a read-only filesystem,
no Linux capabilities, and resource limits.
"""
import os
import subprocess
import time
import uuid

from .executor import _clip, _txt, error_signature, fingerprint, workspace_hash

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

    def run(self, cmd: str, timeout_s: int = 60) -> dict:
        if not self.started:
            raise RuntimeError("Call start() before run().")
        argv = ["docker", "exec", "-w", "/workspace", self.name,
                "timeout", "-k", "2", str(timeout_s),
                "bash", "--noprofile", "--norc", "-c", cmd]
        t0 = time.monotonic()
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s + 10)
            code, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            code, out, err = 124, _txt(e.stdout), _txt(e.stderr)
        sig, cls = error_signature(err, code)
        return {
            "command": cmd,
            "command_fingerprint": fingerprint(cmd),
            "exit_code": code,
            "timed_out": code == 124,
            "stdout": _clip(out),
            "stderr": _clip(err),
            "error_signature": sig,
            "error_class": cls,
            "workspace_hash": workspace_hash(self.workspace),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }
