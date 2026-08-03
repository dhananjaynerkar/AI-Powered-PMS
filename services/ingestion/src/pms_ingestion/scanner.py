"""Optional bounded ClamAV INSTREAM integration."""

from __future__ import annotations

import socket
import struct
from typing import Protocol

from pms_common.settings import Settings


class MalwareDetected(ValueError):
    """Raised when ClamAV identifies malicious content."""


class MalwareScannerError(RuntimeError):
    """Raised when enabled malware scanning cannot reach a safe verdict."""


class MalwareScanner(Protocol):
    def scan(self, content: bytes) -> None:
        """Return only when the supplied bytes are clean."""


class DisabledMalwareScanner:
    """Explicit no-op used only when the feature flag is disabled."""

    def scan(self, content: bytes) -> None:
        del content


class ClamAvScanner:
    """Send bytes to clamd without writing an untrusted temporary file."""

    def __init__(self, settings: Settings) -> None:
        self._host = settings.clamav_host
        self._port = settings.clamav_port
        self._timeout = settings.db_connect_timeout_seconds

    def scan(self, content: bytes) -> None:
        try:
            with socket.create_connection(
                (self._host, self._port),
                timeout=self._timeout,
            ) as connection:
                connection.sendall(b"zINSTREAM\0")
                for offset in range(0, len(content), 64 * 1024):
                    chunk = content[offset : offset + 64 * 1024]
                    connection.sendall(struct.pack(">I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack(">I", 0))
                verdict = connection.recv(4096).decode("utf-8", errors="replace")
        except OSError as error:
            raise MalwareScannerError("ClamAV scan is unavailable") from error
        if " FOUND" in verdict:
            raise MalwareDetected("upload rejected by malware scan")
        if " OK" not in verdict:
            raise MalwareScannerError("ClamAV did not return a clean verdict")


def create_malware_scanner(settings: Settings) -> MalwareScanner:
    if settings.clamav_enabled:
        return ClamAvScanner(settings)
    return DisabledMalwareScanner()
