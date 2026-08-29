# Security policy

## Supported versions

Until 1.0, only the newest published alpha receives security fixes. Production
users should pin an exact version and test database backups and recovery.

## Reporting

Do not open a public issue for a suspected memory-safety, data-corruption, or
durability flaw. Use GitHub's private vulnerability-reporting form at
[Security advisories](https://github.com/jyj117/mdbx-py/security/advisories/new).
This channel is monitored by the repository maintainers and keeps the report
private while a fix and coordinated disclosure are prepared.

Include the clibmdbx version, `clibmdbx.diagnostics()` output, operating system,
Python version, flags/options, a minimal reproducer, and whether unsafe durability
flags were enabled. Never attach a database containing secrets or private data.

Upstream engine issues may also need coordinated disclosure to
[libmdbx](https://github.com/Mithril-mine/libmdbx/security), but please allow the
binding maintainers to determine whether the bug is in the wrapper first.
