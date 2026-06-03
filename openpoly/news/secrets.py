"""api_key_ref resolution.

DB rows and section configs hold references (e.g. `env:OPENPOLY_TRADINGNEWS_KEY`)
instead of raw secrets. Resolution happens lazily at WS connect time so secrets
never appear in JSON / logs / template exports.

Supported schemes (v5):
- ``env:NAME``           — read from environment variable NAME.
- ``local:PATH``         — read from the local file store (see secret_store.py).

Reserved but not implemented:
- ``vault:path#key``     — HashiCorp Vault
- ``keychain:svc/acct``  — OS keychain (post-mainnet upgrade path)
"""

from __future__ import annotations

import os

from .secret_store import (
    InvalidName as _StoreInvalidName,
    NameNotFound,
    get_store,
)


class SecretsError(Exception):
    """Base class for secrets resolution failures."""


class SecretNotFound(SecretsError):
    """The referenced secret could not be found in its backing store."""


class UnsupportedScheme(SecretsError):
    """The reference uses a scheme the resolver does not recognize."""


def resolve(ref: str) -> str:
    """Resolve a `*_ref` string into the actual secret value.

    Errors are normalized to the local exception tree so HTTP callers (which
    catch ``SecretsError``) don't need to know about the underlying store's
    own exception types.
    """
    if not isinstance(ref, str) or ":" not in ref:
        raise UnsupportedScheme(f"Invalid ref format (expected 'scheme:value'): {ref!r}")
    scheme, _, value = ref.partition(":")
    if scheme == "env":
        val = os.environ.get(value)
        if val is None:
            raise SecretNotFound(f"Env var not set: {value}")
        return val
    if scheme == "local":
        try:
            return get_store().get(value)
        except NameNotFound as exc:
            raise SecretNotFound(f"Local secret not found: {value}") from exc
        except _StoreInvalidName as exc:
            raise UnsupportedScheme(f"Invalid local secret name: {value}") from exc
    if scheme in ("vault", "keychain"):
        raise NotImplementedError(f"Secret scheme not implemented yet: {scheme}")
    raise UnsupportedScheme(f"Unknown secret scheme: {scheme}")
