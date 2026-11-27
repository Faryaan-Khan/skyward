# Security

## Reporting a vulnerability
Please report security issues privately through GitHub's
[security advisories](https://github.com/Faryaan-Khan/skyward/security/advisories/new)
rather than a public issue. I aim to respond within a few days.

## How Skyward is protected
| Area | Protection |
| --- | --- |
| Browser | Strict Content-Security-Policy (no inline scripts, only this site + jsDelivr), HSTS, `X-Frame-Options: DENY`, no-sniff, strict referrer, locked-down Permissions-Policy, Subresource Integrity on CDN scripts |
| Cross-site scripting | All user and API text is escaped before display; only `https://` links are rendered |
| Abuse / DoS | Per-visitor rate limits on every API route (stricter for searches and alert sign-ups), 16 KB request body cap, cached responses |
| Database | SQLAlchemy parameterised queries only (no string-built SQL); Postgres over TLS |
| Price alerts | Double opt-in for email, signed one-purpose links (confirm / unsubscribe / Telegram connect), one-click unsubscribe, manage tokens stored as SHA-256 hashes, per-address daily caps |
| Telegram | Webhook only accepts requests carrying Telegram's secret header |
| Secrets | Only in environment variables on Render / GitHub, never in the repo; admin endpoints need a constant-time-compared token |
| Supply chain | Dependabot updates, `pip-audit` on every push, CodeQL code scanning, deploys only after tests pass |
| Privacy | No accounts and no tracking; alerts are tied to a private token kept in your own browser; email addresses are never returned by the API |
