"""Safe configuration diagnostics for Phase 02."""

from __future__ import annotations

import json

from pydantic import ValidationError

from pms_common.settings import Settings


def main() -> int:
    """Validate settings and print only explicitly non-secret diagnostics."""

    try:
        settings = Settings()
    except ValidationError as error:
        details = error.errors(include_input=False, include_url=False)
        print(json.dumps({"status": "INVALID", "errors": details}, sort_keys=True))
        return 1
    report = {"status": "VALID", "settings": settings.safe_diagnostics()}
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

