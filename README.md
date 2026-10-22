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

## Threat model (v0.1, honest)
The signing key is stored at `~/.agentbox/key`. In v0.1 the agent runs as the same OS user,
so a malicious agent could read it. Real isolation (Docker, separate user) is the v0.2 goal.

## Roadmap
- [ ] v0.2 Docker sandbox backend, key isolated from the agent
- [ ] Network egress capture
- [ ] Semantic drift detector
- [ ] Replay UI

## License
Apache-2.0
