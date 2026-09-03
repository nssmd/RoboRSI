# Security Policy

## Secrets

Store provider keys and channel credentials in environment variables or a
secret manager. Do not commit `.env`, access tokens, private endpoints, SSH
material, or resolved configuration copied from private infrastructure.

## Agent-generated code

Treat generated skills and patches as untrusted input. Keep them inside the
proposal, validation, and approval flow before they enter the active skill
library. Agent-authored policies may only compose literal public skill names
through `_dispatch_tool`; direct runtime-state access, dynamic tool selection,
reflection, filesystem, process, network, and simulator-internal APIs are
rejected both during validation and immediately before apply. The
`--skip-harness` operator option does not bypass this capability check.
Agent-authored `SKILL.md` text is checked as well, because it becomes model
context on later runs.

Run robot and simulator workloads in an isolated environment. A static
capability check reduces the exposed surface but is not a replacement for OS
or container isolation.

## Reporting

Use a private GitHub security advisory for `nssmd/RoboRSI` when a report could
expose credentials, private infrastructure, or an exploitable code path.
