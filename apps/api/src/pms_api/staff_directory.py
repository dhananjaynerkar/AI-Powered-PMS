"""Read-only staff-recipient lookup for the local database login workflow."""

from __future__ import annotations

from dataclasses import dataclass

from pms_common.security import AuthorizationContext, AuthorizationDenied, UserRole
from sqlalchemy import Engine, text


class StaffDirectoryError(ValueError):
    """Raised when a requested handoff recipient is not in the trusted directory."""


@dataclass(frozen=True, slots=True)
class StaffRecipient:
    """A safe, displayable assignee. Password fields are intentionally absent."""

    subject: str
    display_name: str
    username: str
    designation: str | None
    role: UserRole


class PostgresStaffDirectory:
    """Resolve same-scope DO, NO and HOD recipients from ``public`` tables."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def recipients(
        self,
        context: AuthorizationContext,
        *,
        role: UserRole,
    ) -> tuple[StaffRecipient, ...]:
        _require_staff_context(context)
        if role not in _STAFF_ROLES:
            raise StaffDirectoryError("handoff role is not supported")
        if context.department_id is None or context.unit_id is None:
            raise AuthorizationDenied("department and unit scope are required")
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT u.name, u.user_name, u.designation, ar.role_id
                    FROM public.admin_users AS u
                    JOIN public.admin_roles AS ar
                      ON ar.admin_id = u.admin_id
                     AND ar.is_active = true
                    WHERE upper(ar.role_id) = :role_id
                      AND upper(coalesce(u.account_status_code, '')) IN ('A', 'ACTIVE', '1', 'Y')
                      AND lower(nullif(trim(u.department), '')) = :department_id
                      AND trim(coalesce(u.unit::text, '')) = :unit_id
                      AND nullif(trim(u.user_name), '') IS NOT NULL
                    ORDER BY coalesce(nullif(trim(u.name), ''), u.user_name), u.user_name
                    """
                ),
                {
                    "role_id": _ROLE_CODES[role],
                    "department_id": context.department_id.casefold(),
                    "unit_id": context.unit_id,
                },
            ).mappings()
            return tuple(_recipient_from_row(dict(row), role) for row in rows)

    def require_recipient(
        self,
        context: AuthorizationContext,
        *,
        role: UserRole,
        subject: str,
    ) -> StaffRecipient:
        clean_subject = subject.strip()
        for recipient in self.recipients(context, role=role):
            if recipient.subject == clean_subject:
                return recipient
        raise StaffDirectoryError("selected recipient is not eligible for this handoff")


_STAFF_ROLES = frozenset(
    {
        UserRole.DATA_ENTRY_OPERATOR,
        UserRole.NODAL_REGIONAL_OFFICER,
        UserRole.HOD,
    }
)
_ROLE_CODES = {
    UserRole.DATA_ENTRY_OPERATOR: "DO",
    UserRole.NODAL_REGIONAL_OFFICER: "NO",
    UserRole.HOD: "HO",
}


def _require_staff_context(context: AuthorizationContext) -> None:
    if not context.roles.intersection(_STAFF_ROLES):
        raise AuthorizationDenied("staff workflow role is required")


def _recipient_from_row(row: dict[str, object], role: UserRole) -> StaffRecipient:
    username = str(row["user_name"]).strip()
    name = _optional_text(row.get("name"))
    return StaffRecipient(
        subject=f"local.{username}",
        display_name=name or username,
        username=username,
        designation=_optional_text(row.get("designation")),
        role=role,
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return None if normalized.lower() in {"", "null", "none"} else normalized
