# Security policy

LeaseLedger v0.1 is a local library/CLI and is not a hosted service. There is
no remote API or authentication system. SQLite files contain job payloads,
errors and events: do not store secrets, payment card details or personal data
without a separate encryption, retention and access-control design.

**Only import trusted local handler modules.** Python handlers run with the
worker's operating-system permissions. A malicious handler can access the
host; this project does not sandbox arbitrary code.

Database files created by LeaseLedger receive private file permissions on
Unix where supported. Existing database file permissions are respected; the
operator is responsible for securing the containing directory, backups and
WAL/journal files. Never place the database on a network filesystem.

If you discover a vulnerability, please report privately using GitHub's
**Report a vulnerability** feature when enabled, rather than posting secrets
or exploit details in a public issue. Supported branch: `main`.
