from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pms_forecasting.training import prediction_refresh_required


class _ScalarResult:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _Connection:
    def __init__(self, values: tuple[str | None, str | None]) -> None:
        self._values = iter(values)
        self.calls: list[tuple[object, Mapping[str, Any]]] = []

    def execute(self, statement: object, parameters: Mapping[str, Any]) -> _ScalarResult:
        self.calls.append((statement, parameters))
        return _ScalarResult(next(self._values))


def test_stale_snapshot_requires_regeneration() -> None:
    connection = _Connection(("snapshot-new", "snapshot-old"))

    assert prediction_refresh_required(connection, target_name="monthly_cash_collection")
    assert len(connection.calls) == 2


def test_matching_snapshot_does_not_regenerate() -> None:
    connection = _Connection(("snapshot-current", "snapshot-current"))

    assert not prediction_refresh_required(
        connection,
        target_name="monthly_cash_collection",
    )
