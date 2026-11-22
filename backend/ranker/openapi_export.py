"""Export the OpenAPI schema to ``docs/openapi.json`` and gate staleness in CI.

``python -m ranker.openapi_export``          writes the file.
``python -m ranker.openapi_export --check``  exits 1 when the committed copy differs.

No database is needed (the app connects only in its lifespan), but ``ranker.main`` builds the
app at import time and settings require ``SESSION_SECRET``; CI sets dummy ``DATABASE_URL`` and
``SESSION_SECRET`` values for this step.
"""

import argparse
import json
import sys
from pathlib import Path

from ranker.main import create_app

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None, path: Path = DEFAULT_PATH) -> int:
    parser = argparse.ArgumentParser(prog="ranker.openapi_export")
    parser.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = parser.parse_args(argv)
    fresh = render()
    if args.check:
        current = path.read_text() if path.exists() else ""
        if current != fresh:
            print(
                f"{path} is stale: run `python -m ranker.openapi_export` and commit the result",
                file=sys.stderr,
            )
            return 1
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
