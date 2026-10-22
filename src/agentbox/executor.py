"""Runs a shell command and observes it from the outside."""
import hashlib
import os
import re
import subprocess
import time

_NORMALIZERS = [
    (re.compile(r"0x[0-9a-fA-F]+"), "<HEX>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID>"),
    (re.compile(r"/tmp/\S+"), "/tmp/<TMP>"),
    (re.compile(r"\d+"), "<N>"),
    (re.compile(r"\s+"), " "),
]
_ERR_CLASS = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception))\b", re.M)
MAX_KEEP = 4000


def normalize(text: str) -> str:
    for rx, rep in _NORMALIZERS:
        text = rx.sub(rep, text)
    return text.strip().lower()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()[:16]


def error_signature(stderr: str, exit_code: int):
    if exit_code == 0 or not stderr.strip():
        return None, None
    last = [l for l in stderr.strip().splitlines() if l.strip()][-3:]
    classes = _ERR_CLASS.findall(stderr)
    cls = classes[-1] if classes else None
    sig = fingerprint("\n".join(last))
    return (f"{cls}:{sig}" if cls else sig), cls


def workspace_hash(root: str) -> str:
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in {".git", "__pycache__", "node_modules"})
        for f in sorted(fn):
            p = os.path.join(dp, f)
            try:
                with open(p, "rb") as fh:
                    h.update(p.encode() + hashlib.sha256(fh.read()).digest())
            except OSError:
                pass
    return h.hexdigest()[:16]


def _clip(s: str) -> dict:
    return {
        "text": s if len(s) <= MAX_KEEP else s[: MAX_KEEP // 2] + "\n...[truncated]...\n" + s[-MAX_KEEP // 2 :],
        "total_chars": len(s),
        "sha256": hashlib.sha256(s.encode()).hexdigest(),
    }


def _txt(x) -> str:
    if x is None:
        return ""
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else x


def run_command(cmd: str, workspace: str, timeout_s: int = 60) -> dict:
    os.makedirs(workspace, exist_ok=True)
    t0 = time.monotonic()
    timed_out = False
    try:
        p = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", cmd],
            cwd=workspace, capture_output=True, text=True, timeout=timeout_s,
        )
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        timed_out, code = True, 124
        out, err = _txt(e.stdout), _txt(e.stderr)
    sig, cls = error_signature(err, code)
    return {
        "command": cmd,
        "command_fingerprint": fingerprint(cmd),
        "exit_code": code,
        "timed_out": timed_out,
        "stdout": _clip(out),
        "stderr": _clip(err),
        "error_signature": sig,
        "error_class": cls,
        "workspace_hash": workspace_hash(workspace),
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }
