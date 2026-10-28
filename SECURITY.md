# Security policy

agentbox is a research prototype whose purpose is trustworthy records and containment, so problems in those areas are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Include what you found, steps to reproduce, and the version or commit. This is a small, volunteer-run project, so replies are best effort, but we aim to acknowledge a report within 7 days.

## In scope

- Code running in the sandbox that can read, change or delete the signing key or log files
- A log that passes `agentbox verify` after being altered, forged or truncated, beyond the limits documented in the README
- Secrets reaching a signed log despite the documented redaction patterns working as described
- A Docker configuration weakness introduced by agentbox (for example a missing restriction)

## Out of scope

- Vulnerabilities in Docker, the Linux kernel or other third-party software themselves
- The local backend having no isolation (documented in the threat model)
- Secrets that the heuristic redaction patterns do not recognize (please open a normal issue or pull request with a new pattern)
- Findings that need the signing key or host access, since the key holder is trusted by design

## Supported versions

Only the latest release and the `main` branch receive fixes.

## Known limits

See the Threat model and Limitations sections of the README. The signature scheme is HMAC, so anyone holding the key can forge logs, and Docker isolation is strong but not a perfect boundary.
