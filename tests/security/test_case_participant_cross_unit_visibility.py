"""Static contract checks for the bounded shared-case RLS correction."""

from pathlib import Path

ROOT = Path(__file__).parents[2]
REVISION = ROOT / "db/migrations/versions/20260803_0014_case_participant_cross_unit_visibility.py"


def test_cross_unit_visibility_migration_is_limited_to_assigned_chat_cases() -> None:
    source = REVISION.read_text(encoding="utf-8")

    assert 'SCHEMA = "pms_chat"' in source
    assert "case_record_select" in source
    assert "ANY(participant_subjects)" in source
    assert "department_id" in source
    assert "classification_rank" in source
    assert 'schema="public"' not in source
    assert "pms_extract_2010_2023" not in source
    assert "GRANT" not in source
    assert "BYPASSRLS" not in source
    assert "DISABLE ROW LEVEL SECURITY" not in source
    assert "FOR ALL" not in source
