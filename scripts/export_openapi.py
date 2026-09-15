"""Export the OpenAPI schema to a file.

    python -m scripts.export_openapi [путь]

The same document Swagger UI serves at `/openapi.json`; writing it to disk gives
the frontend and the integration teams something to generate clients from
without running the service (`SOL-02`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_TARGET = Path("docs/openapi.json")


def main() -> None:
    # Imported here so `--help`-style misuse does not spin up the app config.
    from app.main import app

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    target.parent.mkdir(parents=True, exist_ok=True)

    schema = app.openapi()
    target.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    operations = sum(
        1
        for path in schema["paths"].values()
        for method in path
        if method in {"get", "post", "put", "patch", "delete"}
    )
    print(f"OpenAPI {schema['openapi']} -> {target}")
    print(f"  путей: {len(schema['paths'])}, операций: {operations}")
    print(f"  размер: {target.stat().st_size / 1024:.1f} КБ")


if __name__ == "__main__":
    main()
