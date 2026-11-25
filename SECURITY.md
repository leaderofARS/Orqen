# Security Policy

## Core Security Principles

ORQEN is designed with the following non-negotiable security invariants:

1. **Untrusted Inputs:** Model outputs, tool arguments, retrieved documents, and external agent messages are treated as untrusted input.
2. **Independent Authorization:** Authorization and permission policies are enforced independently of model-generated instructions.
3. **Least Privilege:** Dangerous tools and capabilities are disabled by default and require explicit allowlisting.
4. **Credential Hygiene:** Secrets, API keys, and tokens must never be committed to source control or emitted in telemetry/logs (redaction is enabled by default in `LoggingConfig`).

## Reporting a Vulnerability

Please do not open public GitHub issues for security vulnerabilities. Report vulnerabilities privately to the project maintainers with reproduction details and impact assessment.
