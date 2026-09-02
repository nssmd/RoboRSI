# Security Policy

## Secrets

Store provider keys and channel credentials in environment variables or a
secret manager. Do not commit `.env`, access tokens, private endpoints, SSH
material, or resolved configuration copied from private infrastructure.

## Agent-generated code

Treat generated skills and patches as untrusted input. Keep them inside the
proposal, validation, and approval flow before they enter the active skill
library. Run robot and simulator workloads in an isolated environment.

## Reporting

Use a private GitHub security advisory for `nssmd/RoboRSI` when a report could
expose credentials, private infrastructure, or an exploitable code path.
