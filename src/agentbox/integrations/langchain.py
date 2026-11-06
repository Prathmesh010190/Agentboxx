"""LangChain and LangGraph callback handler for Agentboxx.

Records tool execution start, end, and error events into Agentboxx's
HMAC-signed and hash-chained flight log.
"""
import json
import time
from typing import Any
from uuid import UUID

try:
    from langchain_core.callbacks.base import BaseCallbackHandler
except ImportError:
    raise ImportError(
        "LangChain integration requires langchain-core. "
        "Install it with: pip install agentboxx[langchain]"
    ) from None

from ..recorder import Recorder


class AgentboxxCallbackHandler(BaseCallbackHandler):
    """A LangChain BaseCallbackHandler that records tool actions into Agentboxx.

    Usable as:
        handler = AgentboxxCallbackHandler("session-1")
        chain.invoke(..., config={"callbacks": [handler]})
    """

    def __init__(
        self,
        session_id: str,
        log_dir: str = "./runs",
        *,
        recorder: Recorder | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.recorder = (
            recorder
            if recorder is not None
            else Recorder(session_id=session_id, log_dir=log_dir, **kwargs)
        )
        self._pending_tools: dict[UUID | str, dict[str, Any]] = {}

    @property
    def session_id(self) -> str:
        return self.recorder.session_id

    @property
    def log_path(self):
        return self.recorder.log_path

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record the start of a tool execution."""
        tool_name = "tool"
        if serialized:
            tool_name = serialized.get("name") or serialized.get("id", ["tool"])[-1]
        elif metadata and metadata.get("name"):
            tool_name = str(metadata["name"])

        cmd = input_str
        if not cmd and inputs:
            try:
                cmd = json.dumps(inputs, ensure_ascii=False)
            except (TypeError, ValueError):
                cmd = str(inputs)

        self._pending_tools[run_id] = {
            "tool": tool_name,
            "command": cmd or "",
            "start_time": time.monotonic(),
            "parent_run_id": parent_run_id,
        }

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a completed tool execution into the signed chain."""
        pending = self._pending_tools.pop(run_id, None)
        duration_ms = 0
        tool_name = "tool"
        cmd = ""
        if pending:
            tool_name = pending.get("tool", "tool")
            cmd = pending.get("command", "")
            duration_ms = max(0, int((time.monotonic() - pending["start_time"]) * 1000))

        if isinstance(output, str):
            stdout = output
        else:
            try:
                stdout = json.dumps(output, ensure_ascii=False)
            except (TypeError, ValueError):
                stdout = str(output)

        self.recorder.record_step(
            command=cmd,
            stdout=stdout,
            exit_code=0,
            tool=tool_name,
            duration_ms=duration_ms,
        )

    def on_tool_error(
        self,
        error: BaseException | Exception | str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a failed tool execution into the signed chain."""
        pending = self._pending_tools.pop(run_id, None)
        duration_ms = 0
        tool_name = "tool"
        cmd = ""
        if pending:
            tool_name = pending.get("tool", "tool")
            cmd = pending.get("command", "")
            duration_ms = max(0, int((time.monotonic() - pending["start_time"]) * 1000))

        stderr = str(error)

        self.recorder.record_step(
            command=cmd,
            stderr=stderr,
            exit_code=1,
            tool=tool_name,
            duration_ms=duration_ms,
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any] | Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Finalize and close when the outermost chain finishes."""
        if parent_run_id is None:
            self.close(reason="chain_end")

    def close(self, reason: str = "done") -> None:
        """Idempotently close the underlying recorder and write closing manifest/receipt."""
        self.recorder.close(reason=reason)

