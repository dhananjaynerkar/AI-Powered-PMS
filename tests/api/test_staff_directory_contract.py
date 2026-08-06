"""Contract tests for safe local staff recipient handling."""

from __future__ import annotations

from pms_api.local_auth import _department_scope_value
from pms_api.staff_directory import StaffRecipient
from pms_common.security import UserRole


def test_department_scope_is_canonical_for_document_acl_matching() -> None:
    assert _department_scope_value(" Estate ") == "estate"
    assert _department_scope_value("null") is None


def test_staff_recipient_never_contains_password_fields() -> None:
    recipient = StaffRecipient(
        subject="local.user",
        display_name="User",
        username="user",
        designation="Officer",
        role=UserRole.HOD,
    )
    assert set(recipient.__dataclass_fields__) == {
        "subject",
        "display_name",
        "username",
        "designation",
        "role",
    }
