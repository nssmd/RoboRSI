# Security Policy

## Secrets

RoboRSI configuration stores the name of an API-key environment variable,
never the key. Do not commit `.env`, provider tokens, private endpoints, or
resolved configs copied from private infrastructure.

## Agent-Generated Code

Adaptive proposals are untrusted input. They are parsed, restricted to a fixed
skill destination, scanned for direct simulator-state access, and staged in an
isolated campaign overlay. Only a native simulator-success harness can promote
them. Run experiments in an isolated account/container; this is research code,
not a general-purpose secure sandbox.

## Reporting

Report vulnerabilities through a private GitHub security advisory for
`nssmd/RoboRSI`. Include a minimal reproduction and avoid attaching
live credentials or private experiment artifacts.
