"""Parse what Docker reports about a container: state, resource use and events.

These run on the HOST, so the sandboxed agent cannot influence them.
"""
import json
import re
import threading

_UNITS = {"b": 1, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4,
          "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}


def parse_size(text):
    """'12.5MiB' -> bytes. Returns None if it cannot be read."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]*)\s*", text or "")
    if not m:
        return None
    unit = (m.group(2) or "b").lower()
    if unit not in _UNITS:
        return None
    return int(float(m.group(1)) * _UNITS[unit])


def _pct(text):
    try:
        return float(str(text).strip().rstrip("%"))
    except ValueError:
        return None


def parse_stats(line: str) -> dict:
    """One line of `docker stats --no-stream --format '{{json .}}'`."""
    try:
        d = json.loads(line)
    except ValueError:
        return {}
    used, _, limit = str(d.get("MemUsage", "")).partition("/")
    try:
        pids = int(d.get("PIDs"))
    except (TypeError, ValueError):
        pids = None
    return {"mem_bytes": parse_size(used), "mem_limit_bytes": parse_size(limit),
            "mem_pct": _pct(d.get("MemPerc")), "cpu_pct": _pct(d.get("CPUPerc")),
            "pids": pids}


def parse_state(text: str) -> dict:
    """Output of `docker inspect --format '{{json .State}}'`."""
    try:
        s = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(s, dict):
        return {}
    return {"running": bool(s.get("Running")), "oom_killed": bool(s.get("OOMKilled")),
            "exit_code": s.get("ExitCode"), "error": s.get("Error") or None}


class EventCounter:
    """Counts container events (oom, die, ...) from `docker events --format '{{json .}}'`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}

    def feed(self, line: str):
        try:
            d = json.loads(line)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        action = str(d.get("Action") or d.get("status") or "")
        if action:
            with self._lock:
                self._counts[action] = self._counts.get(action, 0) + 1

    def count(self, action: str) -> int:
        with self._lock:
            return self._counts.get(action, 0)
