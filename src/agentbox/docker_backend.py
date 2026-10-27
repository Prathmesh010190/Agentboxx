"""Run agent commands inside an isolated Docker container (v0.2).

The signing key and log files stay on the HOST. The container only sees
the workspace folder, has no network by default, a read-only filesystem,
no Linux capabilities, and resource limits.
"""
import os
import subprocess
import threading
import time
import uuid

from .executor import build_observation
from .sandbox_stats import EventCounter, parse_state, parse_stats
from .streaming import DEFAULT_MAX_STREAM_BYTES, run_streaming

DEFAULT_IMAGE = "python:3.12-slim"


class DockerNotAvailable(RuntimeError):
    pass


def container_user() -> str:
    """The uid:gid the container runs as.

    Unix: the host user, so the mounted workspace stays writable.
    Windows has no uid or gid. Docker Desktop makes bind mounts writable for any user,
    so a fixed non-root user is used. The sandbox is never run as root by default.
    """
    getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
    if getuid and getgid:
        return f"{getuid()}:{getgid()}"
    return "1000:1000"


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class DockerSandbox:
    def __init__(self, workspace="./workspace-docker", image=DEFAULT_IMAGE,
                 network="none", memory="512m", cpus="1", pids=128, resource_stats=True, user=None):
        self.workspace = os.path.abspath(workspace)
        self.image, self.network = image, network
        self.memory, self.cpus, self.pids = memory, cpus, pids
        self.name = f"agentbox-{uuid.uuid4().hex[:8]}"
        self.started = False
        self.resource_stats = resource_stats   # adds about a second per step
        self.user = user or container_user()   # "uid:gid"; override if needed
        self.events = EventCounter()
        self._events_proc = None

    def describe(self) -> dict:
        return {"backend": "docker", "image": self.image, "network": self.network,
                "memory": self.memory, "cpus": self.cpus, "pids_limit": self.pids,
                "read_only_rootfs": True, "cap_drop": "ALL", "resource_stats": self.resource_stats, "user": self.user}

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
            "--user", self.user,
            "-e", "HOME=/tmp",
            "-v", f"{self.workspace}:/workspace",
            "-w", "/workspace",
            self.image, "sleep", "infinity",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise DockerNotAvailable(f"Could not start container: {r.stderr.strip()}")
        self.started = True
        self._start_event_watcher()

    def stop(self):
        if self.started:
            self._stop_event_watcher()
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
            self.started = False

    def _start_event_watcher(self):
        """Follow `docker events` for this container (host side, so the agent cannot touch it)."""
        since = str(int(time.time()) - 2)     # --since avoids missing events during start-up
        try:
            proc = subprocess.Popen(
                ["docker", "events", "--since", since, "--filter", f"container={self.name}",
                 "--format", "{{json .}}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        self._events_proc = proc

        def pump():
            for line in proc.stdout:
                self.events.feed(line)

        threading.Thread(target=pump, daemon=True).start()

    def _stop_event_watcher(self):
        if self._events_proc is not None:
            self._events_proc.terminate()
            self._events_proc = None

    def _snapshot(self, oom_before: int, code: int, exceeded: bool) -> dict:
        """What Docker says about the container after a step. Never raises."""
        snap: dict = {}
        try:
            r = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", self.name],
                               capture_output=True, text=True, timeout=15)
            snap.update(parse_state(r.stdout))
        except (OSError, subprocess.TimeoutExpired):
            pass
        if self.resource_stats and snap.get("running", True):
            try:
                r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", self.name],
                                   capture_output=True, text=True, timeout=30)
                lines = r.stdout.strip().splitlines()
                snap.update(parse_stats(lines[0] if lines else ""))
            except (OSError, subprocess.TimeoutExpired):
                pass
        if code == 137 and not exceeded:        # a kill: give the OOM event a moment to arrive
            deadline = time.monotonic() + 2.0
            while self.events.count("oom") == oom_before and time.monotonic() < deadline:
                time.sleep(0.1)
        snap["oom_events"] = self.events.count("oom") - oom_before
        return snap

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
        oom_before = self.events.count("oom")
        t0 = time.monotonic()
        # The in-container `timeout` is the normal deadline (exit 124); the host-side
        # deadline is a backstop if `docker exec` itself hangs.
        code, out, err, timed_out, exceeded = run_streaming(
            argv, cwd=None, timeout_s=timeout_s + 10, max_bytes=max_output_bytes,
            cleanup=self._kill_stragglers)
        obs = build_observation(cmd, code, out, err, timed_out or code == 124, exceeded,
                                self.workspace, int((time.monotonic() - t0) * 1000))
        obs["sandbox"] = self._snapshot(oom_before, code, exceeded)
        return obs
