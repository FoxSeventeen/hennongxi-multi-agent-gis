"""Create the approved tiny GIS dataset in an isolated E2E volume."""

from __future__ import annotations

import shutil
from pathlib import Path

from tests.fixtures.deterministic_gis import (
    DeterministicGisFixture,
    write_deterministic_gis_fixture,
)

_GENERATED_DIRECTORIES = ("data", "cache", "outputs", "quality-reports")


def seed_deterministic_data(
    root: Path,
) -> DeterministicGisFixture:
    """Replace only known generated paths so cold and warm seeds are identical."""

    root.mkdir(parents=True, exist_ok=True)
    for directory_name in _GENERATED_DIRECTORIES:
        _remove_generated_path(root / directory_name)

    return write_deterministic_gis_fixture(root)


def main() -> None:
    fixture = seed_deterministic_data(Path("/e2e"))
    print(f"E2E fixture ready: {fixture.manifest_path}")


def _remove_generated_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


if __name__ == "__main__":
    main()
