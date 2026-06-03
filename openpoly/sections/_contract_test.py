"""Contract validation invoked by the registry on every discovered impl.

Two layers:
- check_protocol_conformance: built-in checks for required class attrs and types.
- run_impl_contract_test: optional CONTRACT_TEST staticmethod on the class.

Failure in either raises ContractFailure and the impl is rejected from the catalog.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

REQUIRED_CLASS_ATTRS = ("SECTION_TYPE", "SECTION_VERSION", "REQUIRES", "Config")
ALLOWED_TYPES = {
    "news_source",
    "market_source",
    "embedding",
    "analyzer",
    "entry",
    "exit",
    "database",
}
ALLOWED_CAPS = {
    "news",
    "llm",
    "market_data",
    "order_book",
    "news_history",
    "portfolio",
}


class ContractFailure(Exception):
    """Raised when an impl fails contract validation."""


def check_protocol_conformance(impl: type[Any]) -> None:
    name = impl.__name__
    for attr in REQUIRED_CLASS_ATTRS:
        if not hasattr(impl, attr):
            raise ContractFailure(f"{name} missing required class attribute: {attr}")

    if impl.SECTION_TYPE not in ALLOWED_TYPES:
        raise ContractFailure(
            f"{name}.SECTION_TYPE='{impl.SECTION_TYPE}' not in {sorted(ALLOWED_TYPES)}"
        )

    if not isinstance(impl.SECTION_VERSION, str) or not impl.SECTION_VERSION:
        raise ContractFailure(f"{name}.SECTION_VERSION must be a non-empty str")

    if not isinstance(impl.REQUIRES, list):
        raise ContractFailure(f"{name}.REQUIRES must be list[Capability]")
    for cap in impl.REQUIRES:
        if cap not in ALLOWED_CAPS:
            raise ContractFailure(f"{name}.REQUIRES contains unknown capability: {cap!r}")

    if not (isinstance(impl.Config, type) and issubclass(impl.Config, BaseModel)):
        raise ContractFailure(f"{name}.Config must be a pydantic BaseModel subclass")

    run_attr = getattr(impl, "run", None)
    if not callable(run_attr):
        raise ContractFailure(f"{name}.run() must be callable")


def run_impl_contract_test(impl: type[Any]) -> None:
    test = getattr(impl, "CONTRACT_TEST", None)
    if test is None:
        return
    if not callable(test):
        raise ContractFailure(f"{impl.__name__}.CONTRACT_TEST must be callable")
    try:
        test()
    except Exception as exc:
        raise ContractFailure(f"{impl.__name__}.CONTRACT_TEST failed: {exc}") from exc


def validate(impl: type[Any]) -> None:
    """Run all contract checks. Raises ContractFailure on first failure."""
    check_protocol_conformance(impl)
    run_impl_contract_test(impl)
