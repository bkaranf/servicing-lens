"""Tests for the network-free CLI."""

from __future__ import annotations

import json
import runpy
import sys

import pytest

from mortgage_servicing_dashboard.cli import main


def test_doctor_json_contains_only_safe_readiness_data(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SYNTHETIC_TEST_PROVIDER_KEY_NOT_VALID"
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    monkeypatch.delenv("MSD_MODEL", raising=False)
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "false")

    exit_code = main(["doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert payload["capabilities"]["status"] == "ready"
    assert payload["configuration"]["remote_tracing_allowed"] is False
    assert secret not in captured.out


def test_cli_reports_invalid_config_without_echo(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MSD_MODEL", raising=False)
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "true")

    exit_code = main(["doctor"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "Configuration is invalid" in captured.err


def test_doctor_text_entrypoint(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "false")
    monkeypatch.delenv("MSD_MODEL", raising=False)

    assert main(["doctor"]) == 0
    assert "customer data access: disabled" in capsys.readouterr().out


def test_python_module_entrypoint(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["msd-foundation", "doctor"])
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "false")
    monkeypatch.delenv("MSD_MODEL", raising=False)

    with pytest.raises(SystemExit) as error:
        runpy.run_module("mortgage_servicing_dashboard.__main__", run_name="__main__")

    assert error.value.code == 0
    assert "status: ready" in capsys.readouterr().out
