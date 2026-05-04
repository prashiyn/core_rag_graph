import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph_server import app


DEFAULT_OUTPUT = Path("docs/openapi.json")


def _render_openapi() -> str:
    spec = app.openapi()
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_openapi(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_openapi(), encoding="utf-8")


def _check_openapi(output_path: Path) -> int:
    expected = _render_openapi()
    if not output_path.exists():
        print(f"OpenAPI file missing: {output_path}")
        print("Run: uv run python scripts/openapi_spec.py")
        return 1

    current = output_path.read_text(encoding="utf-8")
    if current != expected:
        print(f"OpenAPI file is out of date: {output_path}")
        print("Run: uv run python scripts/openapi_spec.py")
        return 1

    print(f"OpenAPI file is up to date: {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or validate OpenAPI spec.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if OpenAPI output is stale.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output file path (default: docs/openapi.json).",
    )
    args = parser.parse_args()

    if args.check:
        return _check_openapi(args.output)

    _write_openapi(args.output)
    print(f"Wrote OpenAPI spec: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
