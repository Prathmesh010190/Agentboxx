"""The main API. Wrap every action your agent takes in `recorder.run(...)`."""
import hashlib
from pathlib import Path

from .executor import error_signature, fingerprint, run_command
from .loop_detector import LoopConfig, LoopDetector
from .redact import merge_counts, redact, redact_all
from .signer import ChainWriter, load_or_create_key, load_ed25519_private_key
from .workspace import workspace_hash


def _crash_kind(sbx: dict):
    if sbx.get("oom_events", 0) > 0 or sbx.get("oom_killed"):
        return "oom_kill"
    if sbx.get("running") is False:
        return "container_stopped"
    return None


def _make_stream_record(text: str, total_bytes: int | None = None, truncated: bool = False) -> dict:
    encoded = text.encode("utf-8")
    tb = len(encoded) if total_bytes is None else total_bytes
    return {
        "text": text,
        "total_chars": tb,
        "total_bytes": tb,
        "truncated": truncated,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


class Recorder:
    def __init__(self, session_id: str, workspace: str = "./workspace",
                 log_dir: str = "./runs", key_path=None,
                 loop_cfg: LoopConfig | None = None, sandbox=None,
                 overwrite: bool = False, agent_id: str | None = None,
                 ed25519_key_path=None):
        self.session_id, self.workspace, self.sandbox = session_id, workspace, sandbox
        self.agent_id = agent_id
        self.log_path = Path(log_dir) / f"{session_id}.ndjson"
        self.ed25519_key = load_ed25519_private_key(ed25519_key_path)
        self.writer = ChainWriter(
            self.log_path,
            load_or_create_key(key_path),
            overwrite=overwrite,
            ed25519_key=self.ed25519_key,
        )
        self.detector = LoopDetector(loop_cfg)
        self.step_index = 0
        self.closed = False
        isolation = sandbox.describe() if sandbox else {"backend": "local"}
        start = {"session_id": session_id, "isolation": isolation}
        if agent_id:
            start["agent_id"] = agent_id
        self.writer.append("session.start", start)

    def record_step(
        self,
        *,
        command: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        thought: str = "",
        tool: str | None = None,
        duration_ms: int = 0,
        timed_out: bool = False,
        output_limit_exceeded: bool = False,
        parent_step: int | None = None,
        usage: dict | None = None,
        workspace_hash_val: str | None = None,
    ) -> dict:
        """Record an already-executed action (e.g. from an agent framework callback)."""
        self.step_index += 1
        command, stdout, stderr, redactions = redact_all(command, stdout, stderr)
        thought, t_counts = redact(thought)
        redactions = merge_counts(redactions, t_counts)

        sig, cls = error_signature(stderr, exit_code)
        ws_hash = workspace_hash_val if workspace_hash_val is not None else workspace_hash(self.workspace)

        out_stream = _make_stream_record(stdout)
        err_stream = _make_stream_record(stderr)

        obs = {
            "command": command,
            "command_fingerprint": fingerprint(command),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "output_limit_exceeded": output_limit_exceeded,
            "stdout": out_stream,
            "stderr": err_stream,
            "error_signature": sig,
            "error_class": cls,
            "workspace_hash": ws_hash,
            "duration_ms": duration_ms,
            "redactions": redactions,
        }

        step = {
            "session_id": self.session_id,
            "step_index": self.step_index,
            "thought": thought,
            **obs,
        }
        if self.agent_id:
            step["agent_id"] = self.agent_id
        if tool:
            step["tool"] = tool
        if parent_step is not None:
            step["parent_step_index"] = int(parent_step)
        if usage:
            step["usage"] = {str(k): int(v) for k, v in usage.items()}

        self.writer.append("step", step)

        signals = self.detector.observe(step)
        for s in signals:
            self.writer.append("analysis.signal", {"step_index": self.step_index, **s})

        return {"observation": obs, "signals": signals}

    def run(self, command: str, thought: str = "", timeout_s: int = 60, *,
            tool: str | None = None, parent_step: int | None = None,
            usage: dict | None = None) -> dict:
        self.step_index += 1
        if self.sandbox:
            obs = self.sandbox.run(command, timeout_s)
        else:
            obs = run_command(command, self.workspace, timeout_s)
        sbx = obs.pop("sandbox", None)      # reported as separate events, not inside the step
        thought, t_counts = redact(thought)
        redactions = merge_counts(obs.get("redactions", {}), t_counts)
        step = {"session_id": self.session_id, "step_index": self.step_index,
                "thought": thought, **obs, "redactions": redactions}
        if self.agent_id:
            step["agent_id"] = self.agent_id
        if tool:
            step["tool"] = tool
        if parent_step is not None:
            step["parent_step_index"] = int(parent_step)
        if usage:
            step["usage"] = {str(k): int(v) for k, v in usage.items()}   # from the caller, never computed
        self.writer.append("step", step)
        if sbx:
            metrics = {k: sbx.get(k) for k in ("mem_bytes", "mem_limit_bytes", "mem_pct", "cpu_pct", "pids")}
            self.writer.append("sandbox.resource", {"step_index": self.step_index, **metrics})
            kind = _crash_kind(sbx)
            if kind:
                self.writer.append("sandbox.crash", {"step_index": self.step_index, "kind": kind,
                                                     "exit_code": obs.get("exit_code"), **sbx})
        signals = self.detector.observe(step)
        for s in signals:
            self.writer.append("analysis.signal", {"step_index": self.step_index, **s})
        return {"observation": obs, "signals": signals, "sandbox": sbx}

    def close(self, reason: str = "done"):
        if self.closed:
            return
        self.closed = True
        self.writer.append("session.end", {"reason": reason, "steps": self.step_index})
        self.writer.write_manifest()
        if self.ed25519_key:
            self.writer.write_receipt(session_id=self.session_id)
        if self.sandbox:
            self.sandbox.stop()
