from datetime import UTC, datetime
from pathlib import Path

from hennongxi_data_agent.dataset import run_preflight
from hennongxi_master.watershed import load_approved_watershed

from tests.e2e.seed import seed_deterministic_data


def test_e2e_seed_is_repeatable_and_passes_real_data_preflight(tmp_path: Path) -> None:
    first = seed_deterministic_data(tmp_path)
    second = seed_deterministic_data(tmp_path)

    assert first.manifest_path == second.manifest_path
    assert run_preflight(
        second.manifest_path,
        data_root=second.data_root,
        cache_dir=second.cache_dir,
    ).ok
    watershed = load_approved_watershed(
        second.manifest_path,
        created_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    assert watershed.name == "神农溪流域"
