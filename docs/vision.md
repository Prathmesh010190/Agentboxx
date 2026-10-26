# Vision: ideas, not implemented

Nothing here exists or is scheduled. These are directions we may explore once the core is proven with real agents. They are listed so contributors can see where the project could go, and so we do not mistake them for features.

| Idea | What it would mean | Smallest useful slice |
|---|---|---|
| Delegated capabilities | Each hop in an agent chain can only narrow permissions | A per-run policy of allowed commands in the task contract |
| MCP and A2A gateways | A policy and evidence boundary for open agent protocols | Record tool calls made through one MCP server |
| Digital-twin simulation | Run risky missions against a replica first | Run the same task twice, once in a throwaway copy of the workspace |
| Mission approval envelope | A signed statement of what an agent may do, for how long and at what cost | The task contract file |
| Separation of duties | The agent that acts cannot approve or alter evidence | Already true: the sandbox cannot reach the key or log |
| Portable evidence | Anyone can verify a completed run without our database | Ed25519 run receipts and a published log spec |
| Improvement loop | Failures become regression cases | Replay saved logs through the detectors |

A signature shows who vouched for a record and that it has not changed. It does not show that the work was correct, and nothing here should be described as proof of correctness.
