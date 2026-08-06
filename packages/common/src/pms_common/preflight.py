"""Read-only Phase 01 environment checks with explicit external-service skips."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

COMMAND_TIMEOUT_SECONDS = 5
POSTGRES_TIMEOUT_SECONDS = 3
MINIMUM_FREE_BYTES = 5 * 1024**3
REQUIRED_REPOSITORY_PATHS = (
    Path("AGENTS.md"),
    Path("docs/codex"),
    Path("docs/data_dictionary/TABLE_COLUMN_MANIFEST.csv"),
    Path("docs/data_dictionary/AI_Powered_PMS_2010_2023_Extracted_Data_Summary.md"),
    Path("services/rag/AGENTS.md"),
    Path("services/rule_engine/AGENTS.md"),
    Path("services/forecasting/AGENTS.md"),
    Path("sql/extraction/01_PRECHECK_SOURCE_COVERAGE.sql"),
    Path("sql/extraction/02_BUILD_FILTERED_EXTRACT.sql"),
    Path("sql/extraction/03_VALIDATE_EXTRACT.sql"),
    Path("sql/extraction/04_ONE_CLICK_CLEAN_BUILD_AND_SUMMARY_V2_SAFE_DATES.sql"),
    Path("data/inbox"),
)


class CheckStatus(StrEnum):
    """Supported preflight outcomes."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One preflight check and its safe, non-secret diagnostic."""

    name: str
    status: CheckStatus
    detail: str


def project_root() -> Path:
    """Return the repository root for the installed source layout."""

    return Path(__file__).resolve().parents[4]


def _run_version_command(command: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _first_output_line(process: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in (process.stdout, process.stderr) if part).strip()
    return output.splitlines()[0] if output else "command returned no version text"


def _is_file(path: Path) -> bool:
    """Return false when a candidate executable is absent or inaccessible."""

    try:
        return path.is_file()
    except OSError:
        return False


def _java_major(version_text: str) -> int | None:
    match = re.search(r'version "(?:(1)\.)?(\d+)', version_text)
    if match is None:
        return None
    return int(match.group(2))


def _java_commands(environ: Mapping[str, str]) -> tuple[tuple[str, ...], ...]:
    """Return bounded Java candidates, preferring configured and modern JDKs."""

    candidates: list[Path] = []
    java_home = environ.get("JAVA_HOME")
    if java_home:
        candidates.append(Path(java_home) / "bin" / "java.exe")
    program_files = Path(environ.get("ProgramFiles", r"C:\Program Files"))
    candidates.extend(
        sorted(
            program_files.glob("Java/jdk-*/bin/java.exe"),
            reverse=True,
        )
    )
    path_java = shutil.which("java")
    if path_java:
        candidates.append(Path(path_java))

    unique: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.resolve()).casefold()
        if normalized not in seen and _is_file(candidate):
            seen.add(normalized)
            unique.append((str(candidate), "-version"))
    if not unique:
        unique.append(("java", "-version"))
    return tuple(unique)


def _ollama_command(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Resolve Ollama from PATH or its standard per-user Windows location."""

    local_app_data = environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        try:
            installed = candidate.is_file()
        except OSError:
            return ("ollama", "--version")
        if installed:
            return (str(candidate), "--version")
    path_ollama = shutil.which("ollama")
    if path_ollama:
        return (path_ollama, "--version")
    return ("ollama", "--version")


def check_python() -> CheckResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] == (3, 12):
        return CheckResult("python", CheckStatus.PASS, f"Python {version}")
    return CheckResult("python", CheckStatus.FAIL, f"Python 3.12 required; found {version}")


def check_virtual_environment(root: Path) -> CheckResult:
    expected = (root / ".venv").resolve()
    active = sys.prefix != sys.base_prefix
    actual = Path(sys.prefix).resolve()
    if active and actual == expected:
        return CheckResult("virtual_environment", CheckStatus.PASS, str(actual))
    return CheckResult(
        "virtual_environment",
        CheckStatus.FAIL,
        f"run with {expected / 'Scripts' / 'python.exe'}",
    )


def check_java(environ: Mapping[str, str] | None = None) -> CheckResult:
    checked_environ = os.environ if environ is None else environ
    found_major: int | None = None
    for command in _java_commands(checked_environ):
        process = _run_version_command(command)
        if process is None:
            continue
        output = "\n".join((process.stdout, process.stderr))
        major = _java_major(output)
        if process.returncode == 0 and major is not None and major >= 17:
            return CheckResult("java", CheckStatus.PASS, f"Java major version {major}")
        if found_major is None and major is not None:
            found_major = major
    if found_major is None:
        return CheckResult(
            "java",
            CheckStatus.SKIPPED,
            "Java unavailable; PDF parsing remains disabled until OpenJDK 17 is configured",
        )
    return CheckResult(
        "java",
        CheckStatus.SKIPPED,
        f"Java {found_major} found; PDF parsing remains disabled until OpenJDK 17 is configured",
    )


def check_java_home(environ: Mapping[str, str] | None = None) -> CheckResult:
    """Validate JAVA_HOME separately from Java installations found elsewhere."""

    checked_environ = os.environ if environ is None else environ
    java_home = checked_environ.get("JAVA_HOME")
    if not java_home:
        return CheckResult(
            "java_home",
            CheckStatus.WARN,
            "JAVA_HOME is unset; configure JDK 17 before PDF parsing",
        )

    executable = Path(java_home) / "bin" / "java.exe"
    if not _is_file(executable):
        return CheckResult(
            "java_home",
            CheckStatus.WARN,
            "JAVA_HOME does not contain bin/java.exe; configure JDK 17 before PDF parsing",
        )

    process = _run_version_command((str(executable), "-version"))
    if process is None or process.returncode != 0:
        return CheckResult(
            "java_home",
            CheckStatus.WARN,
            "JAVA_HOME Java could not be validated; configure JDK 17 before PDF parsing",
        )

    major = _java_major("\n".join((process.stdout, process.stderr)))
    if major is not None and major >= 17:
        return CheckResult("java_home", CheckStatus.PASS, f"JAVA_HOME uses Java {major}")
    if major is not None:
        return CheckResult(
            "java_home",
            CheckStatus.WARN,
            f"JAVA_HOME uses Java {major}; JDK 17 or newer is required for PDF parsing",
        )
    return CheckResult(
        "java_home",
        CheckStatus.WARN,
        "JAVA_HOME Java version could not be parsed; configure JDK 17 before PDF parsing",
    )


def check_command(name: str, command: Sequence[str], unavailable_detail: str) -> CheckResult:
    process = _run_version_command(command)
    if process is None:
        return CheckResult(name, CheckStatus.SKIPPED, unavailable_detail)
    if process.returncode != 0:
        return CheckResult(name, CheckStatus.WARN, _first_output_line(process))
    return CheckResult(name, CheckStatus.PASS, _first_output_line(process))


def check_disk_space(root: Path) -> CheckResult:
    free = shutil.disk_usage(root).free
    free_gib = free / 1024**3
    if free >= MINIMUM_FREE_BYTES:
        return CheckResult("disk_space", CheckStatus.PASS, f"{free_gib:.1f} GiB free")
    return CheckResult(
        "disk_space",
        CheckStatus.FAIL,
        f"{free_gib:.1f} GiB free; at least 5.0 GiB required",
    )


def check_required_paths(root: Path) -> CheckResult:
    required = tuple(root / path for path in REQUIRED_REPOSITORY_PATHS)
    missing = [str(path.relative_to(root)) for path in required if not path.exists()]
    if not missing:
        detail = f"{len(required)}/{len(required)} present"
        return CheckResult("required_paths", CheckStatus.PASS, detail)
    return CheckResult("required_paths", CheckStatus.FAIL, f"missing: {', '.join(missing)}")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def check_postgres(root: Path, environ: Mapping[str, str]) -> CheckResult:
    env_path = root / ".env"
    if not env_path.exists():
        return CheckResult("postgres", CheckStatus.SKIPPED, ".env absent; no connection attempted")
    values = {**_read_env_file(env_path), **environ}
    host = values.get("POSTGRES_HOST")
    port_text = values.get("POSTGRES_PORT", "5432")
    if not host:
        return CheckResult("postgres", CheckStatus.WARN, ".env has no POSTGRES_HOST")
    try:
        port = int(port_text)
    except ValueError:
        return CheckResult("postgres", CheckStatus.WARN, "POSTGRES_PORT is not an integer")
    try:
        connection = socket.create_connection((host, port), timeout=POSTGRES_TIMEOUT_SECONDS)
    except (OSError, TimeoutError):
        return CheckResult("postgres", CheckStatus.WARN, "configured endpoint is not reachable")
    connection.close()
    return CheckResult("postgres", CheckStatus.PASS, "configured endpoint is reachable")


def _local_git_repository(root: Path) -> bool:
    return (root / ".git").exists()


def check_env_ignored(root: Path) -> CheckResult:
    if not _local_git_repository(root):
        return CheckResult(
            "env_ignore",
            CheckStatus.SKIPPED,
            "project-local Git repository absent; ignore status cannot be verified",
        )
    process = _run_version_command(("git", "-C", str(root), "check-ignore", "-q", ".env"))
    if process is not None and process.returncode == 0:
        return CheckResult("env_ignore", CheckStatus.PASS, ".env is ignored")
    return CheckResult("env_ignore", CheckStatus.FAIL, ".env is not ignored")


def _sensitive_tracked_paths(paths: Sequence[str]) -> list[str]:
    sensitive_names = {".env", ".env.local", ".env.production"}
    sensitive_suffixes = {".key", ".pem", ".p12", ".pfx"}
    findings: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        normalized = raw_path.replace("\\", "/").lower()
        if (
            path.name.lower() in sensitive_names
            or path.suffix.lower() in sensitive_suffixes
            or normalized.startswith("data/inbox/")
        ):
            findings.append(raw_path)
    return findings


def check_sensitive_tracking(root: Path) -> CheckResult:
    if not _local_git_repository(root):
        return CheckResult(
            "sensitive_tracking",
            CheckStatus.SKIPPED,
            "project-local Git repository absent; tracked-file status cannot be verified",
        )
    process = _run_version_command(("git", "-C", str(root), "ls-files"))
    if process is None or process.returncode != 0:
        return CheckResult("sensitive_tracking", CheckStatus.WARN, "git ls-files unavailable")
    findings = _sensitive_tracked_paths(process.stdout.splitlines())
    if findings:
        return CheckResult(
            "sensitive_tracking",
            CheckStatus.FAIL,
            f"{len(findings)} sensitive or source-document path(s) tracked",
        )
    return CheckResult("sensitive_tracking", CheckStatus.PASS, "no sensitive path pattern tracked")


def run_preflight(
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[CheckResult]:
    """Run bounded, read-only Phase 01 checks."""

    checked_root = project_root() if root is None else root.resolve()
    checked_environ = os.environ if environ is None else environ
    return [
        check_python(),
        check_virtual_environment(checked_root),
        check_java(checked_environ),
        check_java_home(checked_environ),
        check_command(
            "docker",
            ("docker", "--version"),
            "Docker CLI unavailable; supporting services were not started",
        ),
        check_command(
            "ollama",
            _ollama_command(checked_environ),
            "Ollama unavailable; no model was downloaded or started",
        ),
        check_disk_space(checked_root),
        check_required_paths(checked_root),
        check_postgres(checked_root, checked_environ),
        check_env_ignored(checked_root),
        check_sensitive_tracking(checked_root),
    ]


def _print_results(results: Sequence[CheckResult]) -> None:
    for result in results:
        print(f"{result.status:<7} {result.name:<22} {result.detail}")
    counts = {status: sum(result.status == status for result in results) for status in CheckStatus}
    print(
        "SUMMARY "
        + " ".join(f"{status}={counts[status]}" for status in CheckStatus)
    )


def main() -> int:
    """Run the preflight CLI and fail only on enforceable local requirements."""

    results = run_preflight()
    _print_results(results)
    return 1 if any(result.status == CheckStatus.FAIL for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
