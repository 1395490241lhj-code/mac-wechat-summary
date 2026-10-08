# Security Policy

## Maintainer

Security maintainer: **Hongjing Liang** (GitHub: [@1395490241lhj-code](https://github.com/1395490241lhj-code)).

This project handles sensitive local data, including chat history, local databases, credentials or access material used to read those databases, imported archives, and AI-provider configuration. Security and privacy reports are welcome.

## Supported versions

Security fixes are developed against the current `main` branch. Older revisions may not receive backported fixes unless a release explicitly says otherwise.

## Reporting a vulnerability

Please **do not post secrets, database keys, API keys, real chat content, private archives, personal identifiers, or other sensitive user data in a public issue**.

If GitHub private vulnerability reporting is available for this repository, use it. Otherwise, open a minimal public issue asking for a private reporting channel and include only non-sensitive metadata needed to establish the affected component and version.

Useful reports include:

- affected commit or version;
- the component involved;
- a concise description of the security or privacy impact;
- minimal reproduction steps using synthetic or redacted data;
- whether the issue can cause unauthorized data access, data corruption, secret exposure, unsafe file handling, or unintended writes to a user's real data.

Please avoid publishing a working exploit or private user data before a fix is available.

## Security-sensitive areas

Examples of security-sensitive code in this project include:

- local WeChat database and SQLCipher handling;
- key extraction and local secret handling;
- macOS Keychain integration;
- archive and transcript parsing;
- path, container, CRC, size, and malformed-input validation;
- local persistence, migrations, retention, and deletion;
- MCP tool boundaries and local automation;
- test isolation from production user data;
- privacy boundaries around logs, diagnostics, and AI-provider requests.

## Scope and authorization

Security testing should be limited to systems, accounts, data, and devices you own or are explicitly authorized to test. Use synthetic or redacted fixtures whenever possible.

Do not use this project or its reporting process to access third-party data without authorization, deploy malware, steal credentials, bypass access controls on systems you do not own, or publish private user data.

## Disclosure

The maintainer will review reproducible reports, assess impact, and coordinate a fix or mitigation where appropriate. Credit can be provided to reporters who want it, unless disclosure would expose sensitive information.
