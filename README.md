# agentbox

A tamper-evident **flight recorder for AI agents**. It records what an agent actually did
(commands, outputs, exit codes, file changes), signs every record, and flags agents stuck in loops.

> Status: v0.1, early prototype. Local subprocess only; Docker sandbox is next.

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python examples/demo_agent.py
agentbox report runs/demo.ndjson
agentbox verify runs/demo.ndjson
pytest
```

## Threat model
- **Docker backend (v0.2):** commands run in a container with no network, a read-only
  filesystem, all Linux capabilities dropped, a non-root user, and CPU/memory/process limits.
  The signing key and logs stay on the host and are never mounted into the container.
- **Local backend (v0.1):** no isolation. The agent runs as your user and could read the key.
  Use it only for trusted experiments.
- Docker isolation is strong but not a perfect boundary. Run untrusted agents in a disposable VM.

## Roadmap
- [x] v0.2 Docker sandbox backend, key isolated from the agent
- [ ] Network egress capture
- [ ] Semantic drift detector
- [ ] Replay UI

## License
Apache-2.0

