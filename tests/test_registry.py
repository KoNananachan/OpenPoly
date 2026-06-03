"""Registry: discovers good impls, rejects broken ones."""

from __future__ import annotations

import logging

import pytest

from openpoly.sections._contract_test import ContractFailure, validate
from openpoly.sections._registry import scan


FIXTURE_PKG = "tests.fixtures.dummies"


def test_scan_includes_good_rejects_broken(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="openpoly.sections._registry")
    entries = scan(packages=[(FIXTURE_PKG, "builtin")])
    names = {e.name for e in entries}

    assert "DummyGoodAnalyzer" in names
    assert "DummyMissingConfig" not in names
    assert "DummyContractFail" not in names

    rejected_log = caplog.text
    assert "Rejected" in rejected_log
    assert "DummyMissingConfig" in rejected_log
    assert "DummyContractFail" in rejected_log


def test_good_entry_shape() -> None:
    entries = scan(packages=[(FIXTURE_PKG, "builtin")])
    good = next(e for e in entries if e.name == "DummyGoodAnalyzer")

    assert good.type == "analyzer"
    assert good.version == "0.0.1"
    assert good.requires == ["llm"]
    assert good.source == "builtin"
    assert good.module.endswith(".good_v0")

    schema = good.param_schema
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "threshold" in props
    assert "label" in props
    assert props["threshold"]["default"] == 0.5
    assert props["label"]["default"] == "good"


def test_scan_default_packages_runs_without_error() -> None:
    """Built-in dirs are empty in M9 but scan() must not crash."""
    entries = scan()
    # No real impls yet — sanity check shape only.
    assert isinstance(entries, list)


def test_validate_missing_config_raises() -> None:
    from tests.fixtures.dummies.analyzer.missing_config import DummyMissingConfig

    with pytest.raises(ContractFailure, match="missing required class attribute: Config"):
        validate(DummyMissingConfig)


def test_validate_contract_test_raises() -> None:
    from tests.fixtures.dummies.analyzer.contract_fail import DummyContractFail

    with pytest.raises(ContractFailure, match="CONTRACT_TEST failed"):
        validate(DummyContractFail)


def test_validate_bad_section_type_raises() -> None:
    from pydantic import BaseModel

    class _Cfg(BaseModel):
        pass

    class Bad:
        SECTION_TYPE = "not_a_real_type"
        SECTION_VERSION = "0.0.1"
        REQUIRES: list = []
        Config = _Cfg

        def run(self, input):
            pass

    with pytest.raises(ContractFailure, match="SECTION_TYPE"):
        validate(Bad)


def test_validate_bad_capability_raises() -> None:
    from pydantic import BaseModel

    class _Cfg(BaseModel):
        pass

    class Bad:
        SECTION_TYPE = "analyzer"
        SECTION_VERSION = "0.0.1"
        REQUIRES = ["llm", "telepathy"]
        Config = _Cfg

        def run(self, input):
            pass

    with pytest.raises(ContractFailure, match="unknown capability"):
        validate(Bad)
