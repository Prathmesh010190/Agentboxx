# Log format (version 0.1)

A session produces two files: `<session>.ndjson` (the log) and `<session>.manifest.json` (written when the session closes). The format is meant to be verifiable without agentbox: `examples/verify_standalone.py` is an independent implementation of this page using only the Python standard library, and `docs/schema.json` and `docs/manifest.schema.json` describe the JSON shapes.

## Records

The log is UTF-8 text, one JSON object per line (ndjson). Each line is a **record**:

~~~json
{
  "event": { "...": "see below" },
  "prev_hash": "64 hex characters",
  "hash": "64 hex characters",
  "sig": "64 hex characters"
}
~~~

The `event` object is the envelope:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | `"0.1"`. See Versioning |
| `event_id` | string | Random identifier, not used for verification |
| `event_type` | string | See Event types |
| `ts` | number | Unix time in seconds. Not trusted for ordering |
| `seq` | integer | 1, 2, 3, ... with no gaps. This is the ordering |
| `data` | object | Content that depends on `event_type` |

## Hash chain and signature

Let `canon(x)` be `json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded as UTF-8, and let `GENESIS` be 64 zeros.

~~~text
body_hash = sha256_hex(canon(event))
hash      = sha256_hex( (prev_hash + body_hash) as UTF-8 )     # prev_hash and body_hash are the hex strings
sig       = hmac_sha256_hex( key, hash as UTF-8 )
~~~

The first record has `prev_hash = GENESIS`. Every later record has `prev_hash` equal to the previous record's `hash`. The key is 32 random bytes, stored as hex at `~/.agentbox/key` and never mounted into the sandbox.

## Event types

| `event_type` | When | Main `data` fields |
|---|---|---|
| `session.start` | Once, first | `session_id`, `isolation` (`backend`, plus image, network and limits for Docker), optional `agent_id` |
| `step` | After each command | `step_index`, `thought`, `command`, `exit_code`, `timed_out`, `output_limit_exceeded`, `stdout`, `stderr`, `error_signature`, `error_class`, `workspace_hash`, `duration_ms`, `redactions`, optional `agent_id`, `tool`, `parent_step_index`, `usage` |
| `analysis.signal` | A detector fired | `step_index`, `kind`, `severity`, `count`, `hint` |
| `sandbox.resource` | Docker backend, after each step | `step_index`, `mem_bytes`, `mem_limit_bytes`, `mem_pct`, `cpu_pct`, `pids` |
| `sandbox.crash` | OOM kill or stopped container | `step_index`, `kind`, `exit_code`, `running`, `oom_killed`, `oom_events` |
| `session.end` | Once, last, when closed | `reason`, `steps` |

Commands, output and `thought` are redacted before they are hashed. `redactions` counts how many secrets were removed per pattern, never the values. `usage` holds token counts supplied by the caller; agentbox never computes them. A stream (`stdout`, `stderr`) holds `text` (the head and tail of the output), `total_bytes`, `truncated` and a `sha256` of the stored text.

## The closing manifest

~~~json
{
  "payload": { "log": "example.ndjson", "records": 3, "final_hash": "64 hex characters" },
  "sig": "64 hex characters"
}
~~~

`sig` is `hmac_sha256_hex(key, canon(payload))`. `records` is the `seq` of the last record and `final_hash` is its `hash`. The manifest sits next to the log, named by replacing `.ndjson` with `.manifest.json`.

## Verification

Check the records first, then the manifest:

1. For each line: it must parse as JSON with an `event`; `seq` must equal the expected number; `prev_hash` must equal the previous `hash` (or `GENESIS`); `hash` and `sig` must match the formulas above. Any failure is a **problem**.
2. If the manifest exists: its `sig` must match, and `records` and `final_hash` must equal the last record's `seq` and `hash`. Otherwise add a **problem**.
3. If there is no manifest and the last record is not `session.end`, the log is **incomplete**.

| Result | Meaning | Exit code of `agentbox verify` |
|---|---|---|
| Intact | No problems, and the log is closed | 0 |
| Tampered | One or more problems | 1 |
| Incomplete | No problems, but never closed (a crash, a running session, or a removed tail) | 2 |
| Bad input | Missing or unreadable file or key | 3 |

## Versioning

`schema_version` is `MAJOR.MINOR`. Adding optional fields or new event types is a minor change. Renaming or removing fields, or changing the hashing rules, is a major change. Readers must ignore unknown event types and unknown keys inside `data`. The envelope (`event`, `prev_hash`, `hash`, `sig`) is fixed.

## Limits

- **HMAC is symmetric.** Anyone who holds the key can create a valid log or manifest, and only people holding the key can verify. Public-key signing is planned.
- **Deleting the tail and the manifest** is reported as incomplete, not tampered, because it looks like a crash.
- **`ts` is a clock reading**, not proof of when something happened.
- **A valid log shows that records were not changed after writing.** It does not show that the agent's work was correct or that nothing bypassed the recorder.
- **Float formatting.** `ts` is a float, and the hash covers its text form as produced by Python. A verifier in another language must re-serialize floats the same way (shortest round-trip form), or hash the original text. This is a known rough edge.

## Example

First record of a real log (long values shortened):

~~~json
{
  "event": {
    "schema_version": "0.1",
    "event_id": "1f949d2e...",
    "event_type": "session.start",
    "ts": 1793248469.6843722,
    "seq": 1,
    "data": { "session_id": "example", "isolation": { "backend": "local" }, "agent_id": "coder-1" }
  },
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "hash": "8c29f2773f8c...",
  "sig": "0e96f14a18dc..."
}
~~~

To check a log with the independent verifier:

~~~bash
python examples/verify_standalone.py runs/example.ndjson --key-file ~/.agentbox/key
~~~
