"""Run a command with bounded memory, a hard deadline, and a whole-tree kill.

Why this exists: subprocess.run(capture_output=True) keeps all output in memory
(a command that prints gigabytes can exhaust the host), and a timeout only
kills the direct child, leaving anything it started running.
"""
import os
import re
import signal
import subprocess
import threading
import time

HEAD_BYTES = 1900
TAIL_BYTES = 1900
DEFAULT_MAX_STREAM_BYTES = 10 * 1024 * 1024   # per stream (stdout, stderr)
_MAX_CUT_TOKEN = 512


class Capture:
    """Keeps the first HEAD_BYTES and last TAIL_BYTES of a stream, plus its total size."""

    def __init__(self, head: int = HEAD_BYTES, tail: int = TAIL_BYTES):
        self._head_n, self._tail_n = head, tail
        self._head, self._tail = bytearray(), bytearray()
        self.total = 0

    def feed(self, chunk: bytes):
        self.total += len(chunk)
        if len(self._head) < self._head_n:
            take = self._head_n - len(self._head)
            self._head += chunk[:take]
            chunk = chunk[take:]
        if chunk:
            self._tail += chunk
            if len(self._tail) > self._tail_n:
                del self._tail[: len(self._tail) - self._tail_n]

    @property
    def truncated(self) -> bool:
        return self.total > len(self._head) + len(self._tail)

    def text(self) -> str:
        head = bytes(self._head).decode("utf-8", "replace")
        tail = bytes(self._tail).decode("utf-8", "replace")
        if not self.truncated:
            return head + tail
        # A cut can land inside a token. Drop the partial token at each cut so a
        # secret is never left half-visible (redaction needs the whole token).
        m = re.search(r"\S+\Z", head)
        if m and len(m.group()) <= _MAX_CUT_TOKEN:
            head = head[: m.start()]
        m = re.match(r"\S+", tail)
        if m and len(m.group()) <= _MAX_CUT_TOKEN:
            tail = tail[m.end():]
        omitted = self.total - len(self._head) - len(self._tail)
        return f"{head}\n...[{omitted} bytes omitted]...\n{tail}"


def kill_tree(proc: subprocess.Popen):
    """SIGKILL the command's whole process group (it was started in its own session)."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass


def run_streaming(argv, cwd, timeout_s, max_bytes=DEFAULT_MAX_STREAM_BYTES, cleanup=None):
    """Run argv. Returns (exit_code, stdout_capture, stderr_capture, timed_out, output_exceeded).

    On timeout, or when either stream passes max_bytes, the whole process group is
    killed and cleanup() (if given) is called. Exit codes: 124 timeout, 137 output cap.
    """
    out, err = Capture(), Capture()
    kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL,
              "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    exceeded = threading.Event()

    def pump(stream, cap):
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    return
                cap.feed(chunk)
                if cap.total > max_bytes:
                    exceeded.set()
                    return
        except (OSError, ValueError):
            return

    threads = [threading.Thread(target=pump, args=(proc.stdout, out), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, err), daemon=True)]
    for t in threads:
        t.start()

    deadline = time.monotonic() + timeout_s
    timed_out = False
    while True:
        if exceeded.is_set():
            break
        if proc.poll() is not None and not any(t.is_alive() for t in threads):
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(0.01)

    if exceeded.is_set() or timed_out:
        kill_tree(proc)
        if cleanup:
            cleanup()
    proc.wait()
    for t in threads:
        t.join(timeout=2)
    for s in (proc.stdout, proc.stderr):
        try:
            s.close()
        except OSError:
            pass

    code = proc.returncode
    if timed_out:
        code = 124
    elif exceeded.is_set():
        code = 137
    return code, out, err, timed_out, exceeded.is_set()
