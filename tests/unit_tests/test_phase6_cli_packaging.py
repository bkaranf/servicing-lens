"""Offline wheel checks for the Phase 6 CLI runtime configuration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import tomllib
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SHARED_ROOT = "share/public-mortgage-servicing-intelligence/config/phase5"
_PHASE5_RUNTIME_FILES = {
    "cohort-a-sources.v1.yaml",
    "cohort-a-universe.v1.yaml",
    "cohort-b-sources.v1.yaml",
    "cohort-b-universe.v1.yaml",
    "financial_fields.v1.yaml",
}
_COHORT_A = ("TFC", "WFC", "PFSI", "RKT")
_COHORT_B = ("TFC", "WFC", "JPM", "BAC", "USB", "PFSI", "RKT", "UWMC", "RITM", "LDI")


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_wheel_shared_data_is_the_bounded_phase5_runtime_set() -> None:
    with (_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    shared_data = project["tool"]["hatch"]["build"]["targets"]["wheel"]["shared-data"]

    packaged_phase5 = {
        Path(source).name: destination
        for source, destination in shared_data.items()
        if source.startswith("config/phase5/")
    }
    assert set(packaged_phase5) == _PHASE5_RUNTIME_FILES
    assert all(
        destination == f"{_SHARED_ROOT}/{filename}"
        for filename, destination in packaged_phase5.items()
    )
    assert not {
        "cohort-b-replay.v1.yaml",
        "evidence-cases.v1.yaml",
        "registry-evidence-fields.v1.yaml",
        "supported-universe.v1.yaml",
    } & set(packaged_phase5)


def test_built_wheel_installs_both_declarative_cohort_selectors(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("uv is required for the offline wheel contract")
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    environment.pop("MSI_CONFIG_DIR", None)
    wheel_dir = tmp_path / "wheel"
    install_root = tmp_path / "installed"

    _run(
        [uv, "build", "--offline", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=_ROOT,
        environment=environment,
    )
    wheels = tuple(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
    for filename in _PHASE5_RUNTIME_FILES:
        assert any(member.endswith(f"/{_SHARED_ROOT}/{filename}") for member in members)
    assert not any(
        forbidden in member
        for member in members
        for forbidden in (
            "phase5/cohort-b-replay.v1.yaml",
            "phase5/evidence-cases.v1.yaml",
            "phase5/registry-evidence-fields.v1.yaml",
            "tests/fixtures/phase5",
        )
    )

    _run(
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--target",
            str(install_root),
            str(wheel),
        ],
        cwd=tmp_path,
        environment=environment,
    )
    probe = textwrap.dedent(
        """
        import io
        import json
        import os
        import sys
        from contextlib import redirect_stderr, redirect_stdout
        from pathlib import Path

        install_root = Path(os.environ["MSI_PHASE6_INSTALL_ROOT"]).resolve()
        sys.prefix = str(install_root)
        sys.path.insert(0, str(install_root))
        import mortgage_servicing_dashboard.cli as cli
        from mortgage_servicing_dashboard.repository import config_directory

        module_path = Path(cli.__file__).resolve()
        assert module_path.is_relative_to(install_root)
        doctor_stdout = io.StringIO()
        doctor_stderr = io.StringIO()
        with redirect_stdout(doctor_stdout), redirect_stderr(doctor_stderr):
            doctor_exit_code = cli.main(["doctor", "--json"])
        replay_database = install_root.parent / "wheel-replay.db"
        replay_runtime = install_root.parent / "wheel-replay-runtime"
        replay_stdout = io.StringIO()
        replay_stderr = io.StringIO()
        with redirect_stdout(replay_stdout), redirect_stderr(replay_stderr):
            replay_exit_code = cli.main(
                [
                    "ingest",
                    "--phase5-cohort-b",
                    "--database-url",
                    f"sqlite:///{replay_database.as_posix()}",
                    "--runtime-dir",
                    str(replay_runtime),
                ]
            )
        payload = {
            "module_path": str(module_path),
            "config_directory": str(config_directory()),
            "doctor_exit_code": doctor_exit_code,
            "doctor_payload": json.loads(doctor_stdout.getvalue()),
            "doctor_stderr": doctor_stderr.getvalue(),
            "cohort_a": [
                company.ticker
                for company in cli._edgar_companies(phase5_cohort_a=True)
            ],
            "cohort_b": [
                company.ticker
                for company in cli._edgar_companies(phase5_cohort_b=True)
            ],
            "replay_exit_code": replay_exit_code,
            "replay_error": json.loads(replay_stderr.getvalue()),
            "replay_stdout": replay_stdout.getvalue(),
            "replay_database_exists": replay_database.exists(),
            "replay_runtime_exists": replay_runtime.exists(),
        }
        print(json.dumps(payload, sort_keys=True))
        """
    )
    environment["MSI_PHASE6_INSTALL_ROOT"] = str(install_root)
    output = _run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        environment=environment,
    )
    payload = json.loads(output)

    assert Path(payload["module_path"]).is_relative_to(install_root)
    assert Path(payload["config_directory"]) == (
        install_root / "share" / "public-mortgage-servicing-intelligence" / "config"
    )
    assert tuple(payload["cohort_a"]) == _COHORT_A
    assert tuple(payload["cohort_b"]) == _COHORT_B
    assert payload["doctor_exit_code"] == 0
    assert payload["doctor_stderr"] == ""
    assert payload["doctor_payload"]["phase5_runtime"] == {
        "financial_mapping_version": "financial-fields-phase5-v1",
        "source_case_counts": {"cohort_a": 64, "cohort_b": 160},
        "source_manifest_versions": {
            "cohort_a": "phase5-cohort-a-v1",
            "cohort_b": "phase5-cohort-b-v1",
        },
    }
    assert payload["replay_exit_code"] == 1
    assert payload["replay_error"]["code"] == "phase5_replay_unavailable"
    assert payload["replay_stdout"] == ""
    assert payload["replay_database_exists"] is False
    assert payload["replay_runtime_exists"] is False
