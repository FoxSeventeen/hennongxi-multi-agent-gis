from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from hennongxi_contracts import (
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    QualityConclusion,
    QualityMetrics,
    QualityThresholds,
)
from hennongxi_publisher_agent.report import ReportContent, ReportInputError, render_report
from PIL import Image
from pypdf import PdfReader

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CREATED_AT = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _artifact(artifact_type: ArtifactType, marker: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid5(TASK_ID, f"analysis:1:{artifact_type.value}"),
        task_id=TASK_ID,
        attempt=1,
        artifact_type=artifact_type,
        status=ArtifactStatus.COMPLETE,
        media_type=(
            "application/json"
            if artifact_type in {ArtifactType.AREA_STATISTICS, ArtifactType.QUALITY_REPORT}
            else "image/tiff; application=geotiff"
        ),
        created_at=CREATED_AT,
        checksum_sha256=marker * 64,
        byte_size=100,
    )


def _content(*, artifacts: tuple[ArtifactRef, ...] | None = None) -> ReportContent:
    return ReportContent(
        task_id=TASK_ID,
        attempt=1,
        correlation_id=CORRELATION_ID,
        created_at=CREATED_AT,
        before_date=date(2019, 8, 19),
        after_date=date(2024, 8, 12),
        attribution="包含经修改的 Copernicus Sentinel 数据",
        statistics=AreaStatistics(
            increase_hectares=12.34,
            stable_hectares=56.78,
            decrease_hectares=9.87,
            valid_hectares=79.0,
        ),
        quality=QualityMetrics(
            coverage_ratio=1.0,
            valid_pixel_ratio=0.956,
            output_complete=True,
            elapsed_ms=1234,
            thresholds=QualityThresholds(
                minimum_watershed_coverage_ratio=0.95,
                minimum_valid_pixel_ratio=0.9,
            ),
            conclusion=QualityConclusion.PASS,
            passed=True,
            evidence=("范围覆盖率通过", "有效像元率通过", "五项成果完整", "耗时已记录"),
        ),
        artifacts=artifacts
        or tuple(
            _artifact(artifact_type, marker)
            for artifact_type, marker in zip(
                (
                    ArtifactType.NDVI_BEFORE,
                    ArtifactType.NDVI_AFTER,
                    ArtifactType.NDVI_DIFFERENCE,
                    ArtifactType.CHANGE_CLASSIFICATION,
                    ArtifactType.AREA_STATISTICS,
                    ArtifactType.QUALITY_REPORT,
                ),
                "abcdef",
                strict=True,
            )
        ),
    )


def _text(payload: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(payload)).pages)


def test_report_renders_extractable_chinese_sections_and_verified_values() -> None:
    payload = render_report(_content())

    reader = PdfReader(BytesIO(payload))
    extracted = _text(payload)
    assert payload.startswith(b"%PDF-")
    assert len(reader.pages) >= 2
    for expected in (
        "神农溪生态变化监测报告",
        str(TASK_ID),
        str(CORRELATION_ID),
        "2019-08-19",
        "2024-08-12",
        "执行计划与 Agent 链",
        "Data Agent",
        "Analysis Agent",
        "Quality Agent",
        "Publisher Agent",
        "面积统计",
        "增加 12.34 公顷",
        "稳定 56.78 公顷",
        "减少 9.87 公顷",
        "有效面积 79.00 公顷",
        "质量评价",
        "覆盖率 100.00%",
        "有效像元率 95.60%",
        "结论 PASS",
        "限制与解释边界",
        "成果校验和",
        "a" * 64,
        "f" * 64,
        "第 1 页",
    ):
        assert expected in extracted


def test_report_rejects_incomplete_or_cross_task_artifacts() -> None:
    incomplete = _content().artifacts[:-1]
    with pytest.raises(ReportInputError, match="complete verified artifact set"):
        render_report(_content(artifacts=incomplete))

    foreign = _artifact(ArtifactType.QUALITY_REPORT, "f").model_copy(
        update={"task_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")}
    )
    mixed = (*_content().artifacts[:-1], foreign)
    with pytest.raises(ReportInputError, match="task and attempt"):
        render_report(_content(artifacts=mixed))


def test_report_poppler_renders_two_nonblank_a4_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(render_report(_content()))

    subprocess.run(
        ["pdftoppm", "-png", "-r", "130", str(pdf_path), str(tmp_path / "page")],
        check=True,
        capture_output=True,
        text=True,
    )

    page_paths = sorted(tmp_path.glob("page-*.png"))
    assert len(page_paths) == 2
    for page_path in page_paths:
        with Image.open(page_path) as page:
            assert 1070 <= page.width <= 1080
            assert 1515 <= page.height <= 1525
            grayscale = page.convert("L")
            minimum, maximum = grayscale.getextrema()
            assert minimum < 60
            assert maximum == 255
            nonwhite_pixels = sum(grayscale.histogram()[:245])
            assert nonwhite_pixels / (page.width * page.height) > 0.01
