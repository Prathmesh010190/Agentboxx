"""The main API. Wrap every action your agent takes in `recorder.run(...)`."""
from pathlib import Path

from .executor import run_command
from .loop_detector import LoopConfig, LoopDetector
from .redact import merge_counts, redact
from .signer import ChainWriter, load_or_create_key


class Recorder:
    def __init__(self, session_id: str, workspace: str = "./workspace",
                 log_dir: str = "./runs", key_path=None,
                 loop_cfg: LoopConfig | None = None, sandbox=None,
                 overwrite: bool = False):
        self.session_id, self.workspace, self.sandbox = session_id, workspace, sandbox
        self.log_path = Path(log_dir) / f"{session_id}.ndjson"
        self.writer = ChainWriter(self.log_path, load_or_create_key(key_path), overwrite=overwrite)
        self.detector = LoopDetector(loop_cfg)
        self.step_index = 0
        isolation = sandbox.describe() if sandbox else {"backend": "local"}
        self.writer.append("session.start", {"session_id": session_id, "isolation": isolation})

    def run(self, command: str, thought: str = "", timeout_s: int = 60) -> dict:
        self.step_index += 1
        if self.sandbox:
            obs = self.sandbox.run(command, timeout_s)
        else:
            obs = run_command(command, self.workspace, timeout_s)
        thought, t_counts = redact(thought)
        redactions = merge_counts(obs.get("redactions", {}), t_counts)
        step = {"session_id": self.session_id, "step_index": self.step_index,
                "thought": thought, **obs, "redactions": redactions}
        self.writer.append("step", step)
        signals = self.detector.observe(step)
        for s in signals:
            self.writer.append("analysis.signal", {"step_index": self.step_index, **s})
        return {"observation": obs, "signals": signals}

    def close(self, reason: str = "done"):
        self.writer.append("session.end", {"reason": reason, "steps": self.step_index})
        self.writer.write_manifest()
        if self.sandbox:
            self.sandbox.stop()
