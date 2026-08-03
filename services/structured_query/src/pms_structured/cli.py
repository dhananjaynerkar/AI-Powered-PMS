"""Local Phase 09 catalog commands."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from pms_common.database import create_database_engine
from pms_common.settings import Settings
from pms_retrieval.embedding import BgeM3EmbeddingAdapter

from pms_structured.catalog import SemanticCatalogBuilder

GOVERNED_VIEWS = (
    "tenant_360",
    "tenancy_360",
    "plot_360",
    "agreement_360",
    "bill_360",
    "payment_360",
    "outstanding_360",
    "inspection_360",
    "legal_case_360",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pms_structured.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    refresh = commands.add_parser("catalog-refresh")
    refresh.add_argument("--embed", action="store_true")
    commands.add_parser("templates-check")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    settings = Settings()
    if arguments.command == "templates-check":
        from pms_structured.templates import ApprovedTemplateRegistry

        registry = ApprovedTemplateRegistry(
            Path(settings.sql_template_dir),
            max_joins=settings.text_to_sql_max_joins,
        )
        print(json.dumps({"status": "PASS", "templates": sorted(registry.template_ids)}))
        return 0

    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.begin() as connection:
            result = SemanticCatalogBuilder(
                extract_schema=settings.extract_schema,
                governed_views=GOVERNED_VIEWS,
            ).refresh(
                connection,
                embedder=BgeM3EmbeddingAdapter(settings) if arguments.embed else None,
            )
    finally:
        engine.dispose()
    print(json.dumps({"status": "PASS", **asdict(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
