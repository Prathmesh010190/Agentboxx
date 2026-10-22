# agentbox

A tamper-evident **flight recorder for AI agents**. agentbox runs an agent's commands in an isolated sandbox, records what actually happened (commands, output, exit codes, file changes), signs every record, and flags agents that get stuck in loops.

> **Status:** v0.1, early research prototype. Interfaces will change. Not production-ready.

## Why

Most agent tooling records what the agent *says* it did, from inside the agent's own process. agentbox observes from outside:

- **Host-side capture.** Output, exit codes and timing come from the host, not from the agent.
- **Tamper-evident logs.** Each record is hash-chained and HMAC-signed with a key that never enters the sandbox. Editing, deleting or reordering a record is detected by `agentbox verify`.
- **State-aware loop detection.** Repeated commands only count when the workspace didn't change, which separates retrying from making progress.

## How it works
model ──► command ──► sandbox (Docker) ──► output, exit code
▲ │
│ ▼
└──── [agentbox hint] ◄── loop detector ◄── recorder ──► signed .ndjson log

| Module | Role |
|---|---|
| `signer.py` | Hash-chained, HMAC-signed event log and `verify_file` |
| `executor.py` | Local command runner, error fingerprints, workspace hashing |
| `docker_backend.py` | Runs commands in a locked-down container |
| `loop_detector.py` | Exact repeat, repeated error, no-progress and cycle detection |
| `recorder.py` | Main API: `Recorder.run(command)` |
| `agent.py` | Minimal ReAct-style loop that feeds detector hints back to the model |

## Quick start

Requires Python 3.10+. Docker is optional and used by the sandbox demos.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest

python examples/demo_agent.py            # stuck-agent demo (local backend)
python examples/demo_docker.py           # shows what the Docker sandbox blocks
python examples/demo_agent_loop.py       # full pipeline: detect, hint, recover

agentbox report runs/agent-demo.ndjson   # readable summary
agentbox verify runs/agent-demo.ndjson   # integrity check
```

### Example: a stuck agent recovers

The scripted agent repeats a failing command. After the third identical failure on an unchanged workspace, the detector raises warnings and the next observation carries a hint:
[2] Run it. $ python3 main.py -> exit 1
[3] Run it. $ python3 main.py -> exit 1
[4] Run it. $ python3 main.py -> exit 1
!! warn: loop.exact
!! warn: loop.error_repeat
!! warn: loop.no_progress
[5] The hint is right: x = 0 causes the crash. Fix the value.
$ sed -i ‘s/x = 0/x = 2/’ main.py -> exit 0
[6] Run it again. $ python3 main.py -> exit 0

## Use it in your own agent

```python
from agentbox import Recorder
from agentbox.docker_backend import DockerSandbox

sandbox = DockerSandbox("./workspace")   # no network by default
sandbox.start()
rec = Recorder("my-session", workspace="./workspace", sandbox=sandbox)

result = rec.run("pytest -x", thought="Run the tests")
print(result["signals"])                 # loop warnings, if any

rec.close("done")                        # also stops the container
```

`agentbox.agent.run_agent` wraps this in a ReAct-style loop. `ScriptedModel` needs no API key. `AnthropicModel` is an optional adapter (`pip install anthropic`, set `ANTHROPIC_API_KEY`) and has **not yet been validated against the live API** (see issue #5).

## Threat model

- **Docker backend:** commands run as a non-root user in a container with no network, a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, and CPU, memory and process limits. The signing key and logs stay on the host and are never mounted into the container.
- **Local backend:** no isolation. The agent runs as your user and could read the signing key. Use it only for trusted experiments.
- Docker isolation is strong but not a perfect boundary. Run untrusted agents in a disposable VM.
- The log proves records were not altered after writing. It does not prove the agent's *intent*, and it cannot see anything that bypasses the executor.

## Known limitations

- Detector thresholds are initial values, not calibrated on real agent traces.
- No network capture yet (the sandbox simply has no network).
- No semantic drift detection yet; loop detection is command and state based.
- The recorder appends to an existing log file, which breaks verification (issue #3).

## Roadmap

- [x] Signed, hash-chained event log with verification
- [x] Docker sandbox backend
- [x] Loop detection with hint feedback
- [x] ReAct-style agent loop
- [ ] Validate against a live model (#5)
- [ ] Network egress capture
- [ ] Semantic drift detector
- [ ] Session replay UI
- [ ] Threshold calibration on recorded sessions

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and the issues labeled `good first issue`.

## License

Apache-2.0
