"""Validate the approved, offline demonstration dataset without network access."""

from __future__ import annotations

import argparse
from pathlib import Path

from hennongxi_data_agent.dataset import (
    DatasetManifest,
    ManifestValidationError,
    load_manifest,
    run_preflight,
)

__all__ = ["DatasetManifest", "ManifestValidationError", "load_manifest", "run_preflight"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/demo"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run_preflight(
            args.manifest,
            data_root=args.data_root,
            cache_dir=args.cache_dir,
        )
    except ManifestValidationError as error:
        print(f"Data manifest error: {error}")
        print("Remediation: obtain G2 approval, then run `python scripts/cache_demo_data.py`.")
        return 1
    print(report.format())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
