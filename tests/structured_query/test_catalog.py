from __future__ import annotations

import pytest
from pms_structured.catalog import classify_column


@pytest.mark.parametrize(
    ("name", "data_type", "expected"),
    [
        ("canonical_tenant_id", "text", ("identifier", False, False)),
        ("final_amount", "numeric", ("measure", False, False)),
        ("bill_date", "date", ("date", False, False)),
        ("bill_status", "text", ("category", False, False)),
        ("tenant_name", "text", ("name", False, False)),
        ("inspection_remarks", "text", ("narrative", False, True)),
        ("mobile_number", "text", ("sensitive", True, False)),
        ("pan_no", "text", ("sensitive", True, False)),
    ],
)
def test_column_classification_is_deterministic(
    name: str,
    data_type: str,
    expected: tuple[str, bool, bool],
) -> None:
    assert classify_column(name, data_type) == expected
