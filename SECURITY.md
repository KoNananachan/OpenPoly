# Security Policy

openPoly is experimental software that can place **real-money orders** and that
handles **wallet credentials and signing**. Security matters here more than in a
typical project — please read this before reporting an issue.

## Reporting a vulnerability

**Do not open a public GitHub issue for security problems.** Disclose privately:

- Preferred: GitHub's **private vulnerability reporting** — go to the repo's
  **Security** tab → **Report a vulnerability**.
  (Maintainers: enable it under *Settings → Code security and analysis →
  Private vulnerability reporting*.)
- If that is unavailable, contact the maintainers privately.

Please include a description, reproduction steps, affected version/commit, and
the potential impact. We will acknowledge your report and work with you on a fix
and coordinated disclosure.

## What's in scope

- **Credential handling** — leaked keys, bypass of the `*_ref` indirection,
  secrets written to disk or logs, anything that exposes a private key or funder.
- **Order execution** — anything that could place, modify, or cancel orders
  unexpectedly, or move funds without explicit user intent.
- **Wallet signing** — incorrect or unsafe signing behavior.
- **Mode safety** — anything that could cause live (real-money) trading without
  the explicit opt-in.
- **Dependencies** — known-vulnerable third-party packages.

## What's *not* a vulnerability

- **Trading losses.** openPoly is a high-risk trading framework; losing money is
  market risk, not a security bug. See the [DISCLAIMER](./DISCLAIMER.md).
- Issues that only exist in your own `openpoly/user_sections/` code (it runs with
  full backend privileges by design and is not sandboxed).

## Secret-handling rules (users & contributors)

- Never commit `.env`, `*.db`/WAL sidecars, or any key material — all of these
  are gitignored for a reason.
- All credentials resolve via `*_ref` indirection (`env:` / `local:` / keychain).
  There are **no hardcoded secrets** in this repo and there should never be.
- Paper mode is the default; live trading must be an explicit opt-in.

## No warranty

openPoly is provided under the [MIT License](./LICENSE) **with no warranty**.
Running it — especially in live mode — is entirely at your own risk.
