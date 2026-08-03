from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pms_common import preflight
from pms_common.preflight import CheckStatus


def test_java_major_supports_legacy_and_modern_versions() -> None:
    assert preflight._java_major('java version "1.8.0_491"') == 8
    assert preflight._java_major('openjdk version "17.0.12"') == 17
    assert preflight._java_major("unrecognized") is None


def test_java_8_is_explicitly_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    process = subprocess.CompletedProcess(
        args=("java", "-version"),
        returncode=0,
        stdout="",
        stderr='java version "1.8.0_491"',
    )
    monkeypatch.setattr(preflight, "_run_version_command", lambda command: process)

    result = preflight.check_java()

    assert result.status is CheckStatus.SKIPPED
    assert "Java 8" in result.detail
    assert "PDF parsing remains disabled" in result.detail


def test_java_17_is_found_after_legacy_path_java(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preflight,
        "_java_commands",
        lambda environ: (("java8", "-version"), ("java17", "-version")),
    )

    def version(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        major = "1.8.0_491" if command[0] == "java8" else "17.0.19"
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr=f'java version "{major}"',
        )

    monkeypatch.setattr(preflight, "_run_version_command", version)

    result = preflight.check_java({})

    assert result.status is CheckStatus.PASS
    assert result.detail == "Java major version 17"


def test_java_home_unset_is_reported() -> None:
    result = preflight.check_java_home({})

    assert result.status is CheckStatus.WARN
    assert result.detail == "JAVA_HOME is unset; configure JDK 17 before PDF parsing"


def test_java_home_accepts_java_17(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "bin" / "java.exe"
    executable.parent.mkdir()
    executable.touch()
    process = subprocess.CompletedProcess(
        args=(str(executable), "-version"),
        returncode=0,
        stdout="",
        stderr='java version "17.0.19"',
    )
    monkeypatch.setattr(preflight, "_run_version_command", lambda command: process)

    result = preflight.check_java_home({"JAVA_HOME": str(tmp_path)})

    assert result.status is CheckStatus.PASS
    assert result.detail == "JAVA_HOME uses Java 17"


def test_java_home_rejects_java_8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "bin" / "java.exe"
    executable.parent.mkdir()
    executable.touch()
    process = subprocess.CompletedProcess(
        args=(str(executable), "-version"),
        returncode=0,
        stdout="",
        stderr='java version "1.8.0_491"',
    )
    monkeypatch.setattr(preflight, "_run_version_command", lambda command: process)

    result = preflight.check_java_home({"JAVA_HOME": str(tmp_path)})

    assert result.status is CheckStatus.WARN
    assert "uses Java 8" in result.detail
    assert "JDK 17 or newer is required" in result.detail


def test_ollama_resolves_standard_user_install(tmp_path: Path) -> None:
    executable = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()

    command = preflight._ollama_command({"LOCALAPPDATA": str(tmp_path)})

    assert command == (str(executable), "--version")


def test_ollama_inaccessible_standard_install_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def inaccessible(path: Path) -> bool:
        raise PermissionError(path)

    monkeypatch.setattr(Path, "is_file", inaccessible)

    command = preflight._ollama_command({"LOCALAPPDATA": str(tmp_path)})

    assert command == ("ollama", "--version")


def test_missing_external_command_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "_run_version_command", lambda command: None)

    result = preflight.check_command("ollama", ("ollama", "--version"), "not installed")

    assert result.status is CheckStatus.SKIPPED
    assert result.detail == "not installed"


def test_required_paths_reports_missing_items(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").touch()

    result = preflight.check_required_paths(tmp_path)

    assert result.status is CheckStatus.FAIL
    assert "docs" in result.detail
    assert "data" in result.detail


def test_required_paths_accepts_complete_phase_00_inputs(tmp_path: Path) -> None:
    for relative_path in preflight.REQUIRED_REPOSITORY_PATHS:
        path = tmp_path / relative_path
        if relative_path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        else:
            path.mkdir(parents=True, exist_ok=True)

    result = preflight.check_required_paths(tmp_path)

    assert result.status is CheckStatus.PASS
    assert result.detail == "12/12 present"


def test_postgres_is_not_contacted_without_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("PostgreSQL connection must not be attempted")

    monkeypatch.setattr(preflight.socket, "create_connection", unexpected_connection)

    result = preflight.check_postgres(tmp_path, {})

    assert result.status is CheckStatus.SKIPPED
    assert result.detail == ".env absent; no connection attempted"


def test_sensitive_tracked_paths_detects_secrets_and_document_corpus() -> None:
    paths = [
        "README.md",
        ".env",
        "config/signing.key",
        "data/inbox/policy.pdf",
    ]

    findings = preflight._sensitive_tracked_paths(paths)

    assert findings == [".env", "config/signing.key", "data/inbox/policy.pdf"]


def test_git_checks_skip_without_local_repository(tmp_path: Path) -> None:
    env_result = preflight.check_env_ignored(tmp_path)
    tracking_result = preflight.check_sensitive_tracking(tmp_path)

    assert env_result.status is CheckStatus.SKIPPED
    assert tracking_result.status is CheckStatus.SKIPPED


def test_print_results_reports_each_status(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        preflight.CheckResult("one", CheckStatus.PASS, "ok"),
        preflight.CheckResult("two", CheckStatus.WARN, "warning"),
        preflight.CheckResult("three", CheckStatus.SKIPPED, "not applicable"),
        preflight.CheckResult("four", CheckStatus.FAIL, "bad"),
    ]

    preflight._print_results(results)

    output = capsys.readouterr().out
    assert "PASS=1" in output
    assert "FAIL=1" in output
    assert "WARN=1" in output
    assert "SKIPPED=1" in output
