"""Regenerate docs/openapi.yaml from the shared Python contract models."""

from __future__ import annotations

from pathlib import Path

import yaml
from hennongxi_contracts.openapi import build_openapi_document

ROOT = Path(__file__).parents[3]
OUTPUT_PATH = ROOT / "docs" / "openapi.yaml"


def main() -> None:
    rendered = yaml.safe_dump(
        build_openapi_document(),
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
