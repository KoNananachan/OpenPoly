"""Discovery and registration of section impls.

Scans configured packages, imports each non-private submodule, finds classes
that look like Section impls (have SECTION_TYPE attr + defined in that module),
runs contract validation, and returns valid CatalogEntries. Invalid impls are
logged and skipped — they do not appear in the catalog.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Iterable

from ._contract_test import ContractFailure, validate

logger = logging.getLogger(__name__)


DEFAULT_PACKAGES: tuple[tuple[str, str], ...] = (
    ("openpoly.sections", "builtin"),
    ("openpoly.user_sections", "user"),
)


@dataclass(frozen=True)
class CatalogEntry:
    type: str
    name: str
    version: str
    module: str
    requires: list[str] = field(default_factory=list)
    param_schema: dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"


def _iter_section_modules(package_name: str) -> Iterable[str]:
    """Yield dotted names of non-private submodules under a package, recursively."""
    pkg = importlib.import_module(package_name)
    if not hasattr(pkg, "__path__"):
        return
    for info in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        if info.ispkg:
            continue
        leaf = info.name.rsplit(".", 1)[-1]
        if leaf.startswith("_"):
            continue
        yield info.name


def _extract_section_classes(module_name: str) -> list[type[Any]]:
    """Import a module and return classes defined there that have SECTION_TYPE."""
    mod = importlib.import_module(module_name)
    out: list[type[Any]] = []
    for _name, obj in inspect.getmembers(mod, inspect.isclass):
        if obj.__module__ != module_name:
            continue
        if not hasattr(obj, "SECTION_TYPE"):
            continue
        out.append(obj)
    return out


def scan(
    packages: Iterable[tuple[str, str]] = DEFAULT_PACKAGES,
) -> list[CatalogEntry]:
    """Scan packages for section impls. Returns valid entries; logs and skips invalid."""
    entries: list[CatalogEntry] = []
    for pkg_name, source in packages:
        try:
            module_names = list(_iter_section_modules(pkg_name))
        except ModuleNotFoundError:
            logger.info("Package %s not found, skipping.", pkg_name)
            continue
        except Exception as exc:
            logger.warning("Failed to enumerate %s: %s", pkg_name, exc)
            continue

        for mod_name in module_names:
            try:
                impls = _extract_section_classes(mod_name)
            except Exception as exc:
                logger.warning("Failed to import %s: %s", mod_name, exc)
                continue
            for impl in impls:
                try:
                    validate(impl)
                except ContractFailure as exc:
                    logger.warning("Rejected %s.%s: %s", mod_name, impl.__name__, exc)
                    continue
                entries.append(
                    CatalogEntry(
                        type=impl.SECTION_TYPE,
                        name=impl.__name__,
                        version=impl.SECTION_VERSION,
                        module=mod_name,
                        requires=list(impl.REQUIRES),
                        param_schema=impl.Config.model_json_schema(),
                        source=source,
                    )
                )
    return entries
