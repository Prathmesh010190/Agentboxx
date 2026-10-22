"""Full pipeline with a scripted fake model: agent -> Docker sandbox -> recorder -> loop hints.
Run: python examples/demo_agent_loop.py
"""
import shutil
from pathlib import Path

from agentbox import Recorder
from agentbox.agent import ScriptedModel, run_agent
from agentbox.docker_backend import DockerSandbox

Path("runs/agent-demo.ndjson").unlink(missing_ok=True)
shutil.rmtree("workspace-agent", ignore_errors=True)

sandbox = DockerSandbox("./workspace-agent")
sandbox.start()
rec = Recorder("agent-demo", workspace="./workspace-agent", log_dir="./runs", sandbox=sandbox)
try:
    out = run_agent("Make main.py run without errors.", ScriptedModel(), rec)
finally:
    rec.close("agent finished")
print("\nResult:", out)
