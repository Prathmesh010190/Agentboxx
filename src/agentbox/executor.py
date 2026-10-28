"""Runs a shell command and observes it from the outside."""
import hashlib
import os
import re
import time

from .error_signatures import extract_go, extract_node
from .redact import redact_all
from .streaming import DEFAULT_MAX_STREAM_BYTES, run_streaming
from .workspace import workspace_hash

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
    if "Traceback (most recent call last)" not in stderr:
        found = extract_go(stderr) or extract_node(stderr)
        if found:
            cls, msg = found
            return f"{cls}:{fingerprint(cls + ' ' + msg)}", cls
    last = [ln for ln in stderr.strip().splitlines() if ln.strip()][-3:]
    classes = _ERR_CLASS.findall(stderr)
    cls = classes[-1] if classes else None
    sig = fingerprint("\n".join(last))
    return (f"{cls}:{sig}" if cls else sig), cls


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


def _stream_record(text: str, cap) -> dict:
    # total_chars is kept for compatibility; it counts bytes seen on the stream.
    return {
        "text": text,
        "total_chars": cap.total,
        "total_bytes": cap.total,
        "truncated": cap.truncated,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),   # of the stored, redacted text
    }


def build_observation(cmd, code, out, err, timed_out, exceeded, workspace, duration_ms) -> dict:
    """Turn a finished command into a log record. Used by every backend."""
    out_text, err_text = out.text(), err.text()
    cmd, out_text, err_text, redactions = redact_all(cmd, out_text, err_text)
    sig, cls = error_signature(err_text, code)
    return {
        "redactions": redactions,
        "command": cmd,
        "command_fingerprint": fingerprint(cmd),
        "exit_code": code,
        "timed_out": timed_out,
        "output_limit_exceeded": exceeded,
        "stdout": _stream_record(out_text, out),
        "stderr": _stream_record(err_text, err),
        "error_signature": sig,
        "error_class": cls,
        "workspace_hash": workspace_hash(workspace),
        "duration_ms": duration_ms,
    }


def run_command(cmd: str, workspace: str, timeout_s: int = 60,
                max_output_bytes: int = DEFAULT_MAX_STREAM_BYTES) -> dict:
    os.makedirs(workspace, exist_ok=True)
    t0 = time.monotonic()
    code, out, err, timed_out, exceeded = run_streaming(
        ["bash", "--noprofile", "--norc", "-c", cmd],
        cwd=workspace, timeout_s=timeout_s, max_bytes=max_output_bytes)
    return build_observation(cmd, code, out, err, timed_out, exceeded, workspace,
                             int((time.monotonic() - t0) * 1000))
