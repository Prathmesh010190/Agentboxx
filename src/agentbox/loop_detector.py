"""Detects an agent that is stuck."""
from collections import deque
from dataclasses import dataclass


@dataclass
class LoopConfig:
    window: int = 10
    exact_warn: int = 3
    exact_crit: int = 5
    error_warn: int = 3
    error_crit: int = 5
    no_progress_warn: int = 4


class LoopDetector:
    def __init__(self, cfg: LoopConfig | None = None):
        self.cfg = cfg or LoopConfig()
        self.win: deque = deque(maxlen=self.cfg.window)

    def observe(self, step: dict) -> list[dict]:
        self.win.append(step)
        signals = []
        signals += self._exact(step)
        signals += self._error_repeat(step)
        signals += self._no_progress()
        signals += self._cycle()
        return signals

    @staticmethod
    def _sig(kind, severity, count, hint):
        return {"kind": kind, "severity": severity, "count": count, "hint": hint}

    def _exact(self, s):
        n = sum(1 for x in self.win if x["command_fingerprint"] == s["command_fingerprint"]
                and x["workspace_hash"] == s["workspace_hash"])   # same command on unchanged files
        if n >= self.cfg.exact_crit:
            return [self._sig("loop.exact", "critical", n, "Same command repeated. Stop and change approach.")]
        if n >= self.cfg.exact_warn:
            return [self._sig("loop.exact", "warn", n, "Same command repeated. Try something different.")]
        return []

    def _error_repeat(self, s):
        if not s.get("error_signature"):
            return []
        n = sum(1 for x in self.win if x.get("error_signature") == s["error_signature"])
        if n >= self.cfg.error_crit:
            return [self._sig("loop.error_repeat", "critical", n, "Same error again and again. Re-plan.")]
        if n >= self.cfg.error_warn:
            return [self._sig("loop.error_repeat", "warn", n, "Same error recurring. Form a new hypothesis.")]
        return []

    def _no_progress(self):
        steps = list(self.win)
        run = 0
        for x in reversed(steps):
            if x["workspace_hash"] == steps[-1]["workspace_hash"]:
                run += 1
            else:
                break
        failing = sum(1 for x in steps[-run:] if x["exit_code"] != 0)
        if run >= self.cfg.no_progress_warn and failing >= max(1, int(run * 0.8)):
            return [self._sig("loop.no_progress", "warn", run, "No files changed across several failing steps.")]
        return []

    def _cycle(self):
        seq = [x["command_fingerprint"] for x in self.win]
        for L in (2, 3):
            if len(seq) >= 2 * L and seq[-L:] == seq[-2 * L : -L] and len(set(seq[-L:])) > 1:
                return [self._sig("loop.cycle", "warn", 2, f"Repeating a cycle of {L} commands.")]
        return []
