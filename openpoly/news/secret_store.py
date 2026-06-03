"""Local file-backed secret store (v5).

Plaintext JSON at ``~/.openpoly/secrets.json`` (chmod 0o600). Name format is
``<seg>(/<seg>)*`` where ``<seg>`` matches ``[A-Za-z0-9_-]+`` — slashes are a
naming convention for grouping (e.g. ``demo/news_source/tradingnews``); the
store itself treats names as opaque flat keys.

Trust model (micro-stakes paper, single-user, fast-ship): plaintext at rest, same-user
processes can read. Backend assumes loopback bind. For mainnet, swap this for
an OS-keychain backed store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Segments of [A-Za-z0-9_-]+ separated by single `/`. Rejects leading/trailing
# slash, consecutive slashes, dots, spaces, colons, and any path-traversal
# attempt (`..` fails the regex by virtue of `.` not being in the char class).
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*$")

_DESIRED_MODE = 0o600

_ENV_OVERRIDE = "OPENPOLY_SECRET_STORE"


@dataclass(frozen=True)
class SecretEntry:
    """List output — never carries the secret value by design."""

    name: str
    created_at: float


class StoreError(Exception):
    """Base for secret-store failures."""


class InvalidName(StoreError):
    """Supplied name (or value) failed validation."""


class NameNotFound(StoreError):
    """No entry exists for the given name."""


def _default_path() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openpoly" / "secrets.json"


def validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise InvalidName(f"invalid name: {name!r}")


class LocalSecretStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._lock = asyncio.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._load_if_exists()

    # Never include value(s) in repr.
    def __repr__(self) -> str:
        return f"LocalSecretStore(path={self._path}, n_entries={len(self._cache)})"

    # ---------- internal IO ----------

    def _load_if_exists(self) -> None:
        if not self._path.exists():
            return
        try:
            current_mode = stat.S_IMODE(self._path.stat().st_mode)
            if current_mode != _DESIRED_MODE:
                logger.warning(
                    "secret store at %s has mode %o (expected %o); leaving as-is",
                    self._path,
                    current_mode,
                    _DESIRED_MODE,
                )
        except OSError as exc:  # pragma: no cover — defensive only
            logger.warning("failed to stat secret store: %s", exc)
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("failed to load secret store at %s: %s", self._path, exc)
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            self._cache = entries

    def _persist(self) -> None:
        """Atomic rewrite with mode 0o600 enforced.

        Uses ``os.open`` with explicit mode bits so the tmp file never exists
        at a wider permission than desired, then a same-dir ``os.replace`` for
        atomic publish.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.parent / f"{self._path.name}.tmp"
        data = {"entries": self._cache}
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _DESIRED_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)
        # Re-assert in case umask widened the tmp file's effective mode.
        os.chmod(self._path, _DESIRED_MODE)

    # ---------- public API ----------

    async def set(self, name: str, value: str) -> SecretEntry:
        validate_name(name)
        if not isinstance(value, str) or value == "":
            raise InvalidName("value must be a non-empty string")
        async with self._lock:
            now = time.time()
            existing = self._cache.get(name)
            created_at = existing["created_at"] if existing else now
            self._cache[name] = {"value": value, "created_at": created_at}
            self._persist()
            return SecretEntry(name=name, created_at=created_at)

    def get(self, name: str) -> str:
        """Sync — resolver in ``secrets.py`` is sync and calls this."""
        validate_name(name)
        entry = self._cache.get(name)
        if entry is None:
            raise NameNotFound(name)
        return entry["value"]

    def list_entries(self, prefix: str | None = None) -> list[SecretEntry]:
        items = [
            SecretEntry(name=n, created_at=e["created_at"])
            for n, e in self._cache.items()
            if prefix is None or n.startswith(prefix)
        ]
        items.sort(key=lambda s: s.name)
        return items

    async def delete(self, name: str) -> None:
        validate_name(name)
        async with self._lock:
            if name not in self._cache:
                raise NameNotFound(name)
            del self._cache[name]
            self._persist()


# Lazy singleton — instantiating at import time would touch the user's real
# ~/.openpoly/secrets.json during test collection, which is gross. Callers
# (resolver in secrets.py, HTTP routes) use get_store().
_singleton: LocalSecretStore | None = None


def get_store() -> LocalSecretStore:
    global _singleton
    if _singleton is None:
        _singleton = LocalSecretStore()
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Test hook — drop the cached singleton so a fresh store is built next
    time. Used by tests that monkeypatch ``OPENPOLY_SECRET_STORE`` after
    other modules may have already touched ``get_store()``."""
    global _singleton
    _singleton = None
