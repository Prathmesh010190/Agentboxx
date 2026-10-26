# Architecture

agentbox is a small trusted core that everything else plugs into. The on-disk log is the product: a third party should be able to verify it from a written spec, without using agentbox's code.

Legend: **built** exists on `main`, **planned** is a goal, not a feature.

## Design rules

1. **The log format is the real API.** Verification must not depend on our implementation.
2. **A small core, everything else a plugin.** The core uses only the standard library and has no model, cloud or framework dependency.
3. **Three trust zones.** The host is trusted, the sandbox is untrusted, and the model provider is outside our control and sees only redacted data.
4. **Honest claims.** Every capability documents its limits.

## Trust zones

| Zone | Trusted? | Holds |
|---|---|---|
| Host | Yes | Signing key, logs, recorder, detectors |
| Sandbox | No | The agent's commands and workspace |
| Model provider | Outside our control | Redacted observations |

## Components

| # | Component | Built | Planned |
|---|---|---|---|
| 1 | Task contract | none | goal, step / time / output limits, host-run success check, allowed commands |
| 2 | Recorder core | redaction, fingerprints, error signatures, output cap, process-tree kill | per-run budgets, policy checks |
| 3 | Analysis | repeat, error, no-progress and cycle detectors, hint feedback | drift detector, plugin interface, intervention ladder |
| 4 | Evidence layer | hash chain, HMAC signatures, signed closing manifest, `verify` | Ed25519 run receipt, JSON Schema, `docs/log-format.md` |
| 5 | Sandbox backends | local, Docker | resource and crash events, gVisor, bubblewrap, egress proxy |
| 6 | Exporters and verifier | `report`, `list`, `verify`, `demo` | JSON, HTML replay, OpenTelemetry |
| 7 | Adapters | scripted model, Anthropic adapter (unvalidated) | other providers, `agentbox run -- <command>` |

## Plugin interfaces (planned)

These match how the code already behaves informally. Writing them down lets contributors add backends and detectors without touching the core. They are not enforced or registered through entry points yet.

~~~python
from typing import Protocol

class Sandbox(Protocol):
    def start(self) -> None: ...
    def run(self, cmd: str, timeout_s: int, max_output_bytes: int) -> dict: ...
    def stop(self) -> None: ...
    def describe(self) -> dict: ...

class Detector(Protocol):
    def observe(self, step: dict) -> list[dict]: ...

class ModelAdapter(Protocol):
    def next_action(self, messages: list[dict]) -> dict: ...

class Exporter(Protocol):
    def export(self, log_path: str, destination: str) -> None: ...
~~~

## Target layout (not a refactor to do now)

~~~text
src/agentbox/
  core/        events, recorder, policy, contract
  evidence/    chain, manifest, receipt, verify
  sandbox/     base, local, docker
  analysis/    detectors/, interventions
  redact/      patterns
  agents/      loop, models/
  export/      report, otel, html
  cli/
docs/          log-format.md, schema.json, threat-model.md, architecture.md, vision.md
~~~

Files move in one mechanical change after the interfaces exist, so open pull requests are not disrupted.

## Release phases

| Version | Goal | Done when |
|---|---|---|
| v0.2 | Safe to run | Output cap and tree kill (done), resource and crash events |
| v0.3 | Proven and verifiable | Three live-model sessions published, Ed25519 receipts, log spec and schema |
| v0.4 | Extensible | Plugin interfaces, JSON and HTML export, OpenTelemetry, task contract |
| v0.5 | Stronger isolation | gVisor or bubblewrap backend, network egress proxy |
| v1.0 | Stable | Frozen schema, outside review of the threat model, benchmark over 50+ recorded runs |

## Out of scope

Federation, delegation chains, agent-to-agent gateways, digital twins and multi-agent debate are recorded in [vision.md](vision.md) as ideas only.

## Project health

- A redacted public trace dataset, so detectors can be compared fairly
- A threat model updated with every feature, and `SECURITY.md`
- Fuzz and tamper tests for the verifier
- Semantic versioning, with the schema version in every record
