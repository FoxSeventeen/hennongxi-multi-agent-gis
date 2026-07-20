"""Deterministic Chinese PDF rendering from already verified publication inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from importlib.resources import as_file, files
from io import BytesIO
from threading import Lock
from typing import Final
from uuid import UUID

from hennongxi_contracts import (
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    QualityConclusion,
    QualityMetrics,
)
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.enums import TA_CENTER, TA_LEFT  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import (  # type: ignore[import-untyped]
    ParagraphStyle,
    getSampleStyleSheet,
)
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_FONT_NAME: Final = "NotoSansSC"
_FONT_LOCK = Lock()
_FONT_REGISTERED = False
_REQUIRED_ARTIFACT_TYPES: Final = frozenset(
    {
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
        ArtifactType.AREA_STATISTICS,
        ArtifactType.QUALITY_REPORT,
    }
)
_ARTIFACT_LABELS: Final = {
    ArtifactType.NDVI_BEFORE: "前期 NDVI 栅格",
    ArtifactType.NDVI_AFTER: "后期 NDVI 栅格",
    ArtifactType.NDVI_DIFFERENCE: "NDVI 差值栅格",
    ArtifactType.CHANGE_CLASSIFICATION: "变化分级栅格",
    ArtifactType.AREA_STATISTICS: "面积统计",
    ArtifactType.QUALITY_REPORT: "质量评价报告",
}
_PLAN: Final = (
    ("1", "Data Agent", "准备完整流域边界与双时相红光、近红外数据，并校验网格一致性。"),
    ("2", "Analysis Agent", "计算两期 NDVI、差值、变化分级和投影面积统计。"),
    ("3", "Quality Agent", "独立复核范围覆盖率、有效像元、成果完整性与运行耗时。"),
    ("4", "Publisher Agent", "发布任务绑定地图资源，并生成本中文 PDF 报告。"),
)


class ReportInputError(ValueError):
    """Raised when a report request is incomplete or crosses task boundaries."""


@dataclass(frozen=True, slots=True)
class ReportContent:
    task_id: UUID
    attempt: int
    correlation_id: UUID
    created_at: datetime
    before_date: date
    after_date: date
    attribution: str
    statistics: AreaStatistics
    quality: QualityMetrics
    artifacts: tuple[ArtifactRef, ...]


def render_report(content: ReportContent) -> bytes:
    """Build an extractable, paginated A4 report without accepting a filesystem path."""

    _validate_content(content)
    _register_font()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=23 * mm,
        bottomMargin=20 * mm,
        title="神农溪生态变化监测报告",
        author="神农溪分布式多 Agent GIS 系统",
        subject=f"任务 {content.task_id} 的生态变化监测结果",
        creator="hennongxi-publisher-agent",
        invariant=1,
    )
    styles = _styles()
    story = [
        Paragraph("神农溪生态变化监测报告", styles["title"]),
        Paragraph("双时相 NDVI 变化分析与独立质量评价", styles["subtitle"]),
        Spacer(1, 7 * mm),
        _summary_table(content, styles),
        Spacer(1, 6 * mm),
        _section("报告摘要", styles),
        Paragraph(
            "本报告汇总同一任务下的权威数据准备、双时相 NDVI 变化分析、独立质量评价"
            "和成果发布结果。Master Agent 以固定受约束计划协调四个执行 Agent，质量结论"
            "为 PASS 后才生成报告。",
            styles["body"],
        ),
        Spacer(1, 4 * mm),
        _section("数据与时间范围", styles),
        Paragraph(
            _safe(
                f"前期数据日期 {content.before_date.isoformat()}；后期数据日期 "
                f"{content.after_date.isoformat()}。{content.attribution}。"
            ),
            styles["body"],
        ),
        Spacer(1, 4 * mm),
        _section("执行计划与 Agent 链", styles),
        _plan_table(styles),
        Spacer(1, 5 * mm),
        _section("面积统计", styles),
        _statistics_table(content.statistics, styles),
        Spacer(1, 5 * mm),
        _section("质量评价", styles),
        _quality_table(content.quality, styles),
        Spacer(1, 3 * mm),
        *(
            Paragraph(f"- {_safe(evidence)}", styles["small"])
            for evidence in content.quality.evidence
        ),
        Spacer(1, 5 * mm),
        PageBreak(),
        _section("限制与解释边界", styles),
        Paragraph(
            "本报告仅比较两个观测日期，反映的 NDVI 变化不等同于变化原因归因。云、阴影、"
            "大气残差和季节差异仍可能影响结果；面积统计仅覆盖完整流域裁剪范围内的有效像元。"
            "变化分级用于监测筛查，现场决策前仍需结合地面调查复核。",
            styles["body"],
        ),
        Spacer(1, 5 * mm),
        _section("成果校验和", styles),
        Paragraph(
            "以下 SHA-256 来自生成报告前复核通过的上游成果。它们用于确认报告所依据的"
            "栅格、统计和质量证据未被替换。",
            styles["body"],
        ),
        Spacer(1, 4 * mm),
        _checksum_table(content.artifacts, styles),
        Spacer(1, 7 * mm),
        _section("可追溯标识", styles),
        _traceability_table(content, styles),
    ]
    document.build(
        story,
        onFirstPage=_draw_page_chrome,
        onLaterPages=_draw_page_chrome,
    )
    return buffer.getvalue()


def _validate_content(content: ReportContent) -> None:
    if content.attempt < 1:
        raise ReportInputError("report attempt must be positive")
    if content.created_at.tzinfo is None or content.created_at.utcoffset() is None:
        raise ReportInputError("report created_at must be timezone-aware")
    if content.before_date > content.after_date:
        raise ReportInputError("report source dates are reversed")
    if not content.attribution.strip():
        raise ReportInputError("report attribution is required")
    artifact_types = tuple(artifact.artifact_type for artifact in content.artifacts)
    if len(artifact_types) != len(_REQUIRED_ARTIFACT_TYPES) or set(
        artifact_types
    ) != _REQUIRED_ARTIFACT_TYPES:
        raise ReportInputError("report requires the complete verified artifact set")
    if any(
        artifact.task_id != content.task_id or artifact.attempt != content.attempt
        for artifact in content.artifacts
    ):
        raise ReportInputError("report artifacts must belong to the report task and attempt")
    if any(
        artifact.status is not ArtifactStatus.COMPLETE
        or artifact.checksum_sha256 is None
        or artifact.byte_size is None
        for artifact in content.artifacts
    ):
        raise ReportInputError("report artifacts must be complete and checksum-verified")
    if content.quality.conclusion is not QualityConclusion.PASS or not content.quality.passed:
        raise ReportInputError("report requires a passing quality conclusion")


def _register_font() -> None:
    global _FONT_REGISTERED
    with _FONT_LOCK:
        if _FONT_REGISTERED:
            return
        resource = files("hennongxi_publisher_agent").joinpath(
            "assets/fonts/NotoSansSC-VF.ttf"
        )
        with as_file(resource) as path:
            pdfmetrics.registerFont(TTFont(_FONT_NAME, str(path)))
        _FONT_REGISTERED = True


def _styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=sample["Title"],
            fontName=_FONT_NAME,
            fontSize=23,
            leading=31,
            textColor=colors.HexColor("#143D2A"),
            alignment=TA_CENTER,
            spaceAfter=3 * mm,
            wordWrap="CJK",
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=sample["Normal"],
            fontName=_FONT_NAME,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#52705E"),
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=sample["Heading2"],
            fontName=_FONT_NAME,
            fontSize=14,
            leading=20,
            textColor=colors.HexColor("#143D2A"),
            spaceBefore=2 * mm,
            spaceAfter=3 * mm,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=sample["BodyText"],
            fontName=_FONT_NAME,
            fontSize=9.5,
            leading=16,
            textColor=colors.HexColor("#23352B"),
            alignment=TA_LEFT,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "ReportSmall",
            parent=sample["BodyText"],
            fontName=_FONT_NAME,
            fontSize=8,
            leading=13,
            textColor=colors.HexColor("#405247"),
            wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "ReportTable",
            parent=sample["BodyText"],
            fontName=_FONT_NAME,
            fontSize=8.5,
            leading=13,
            textColor=colors.HexColor("#23352B"),
            wordWrap="CJK",
        ),
        "checksum": ParagraphStyle(
            "ReportChecksum",
            parent=sample["Code"],
            fontName=_FONT_NAME,
            fontSize=6.6,
            leading=10,
            textColor=colors.HexColor("#23352B"),
        ),
    }


def _section(title: str, styles: dict[str, ParagraphStyle]) -> KeepTogether:
    return KeepTogether(
        [
            Table(
                [[Paragraph(_safe(title), styles["section"])]],
                colWidths=[174 * mm],
                style=TableStyle(
                    [
                        ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#9BC3A9")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1 * mm),
                    ]
                ),
            )
        ]
    )


def _summary_table(content: ReportContent, styles: dict[str, ParagraphStyle]) -> Table:
    rows = (
        ("任务 ID", str(content.task_id)),
        ("尝试 / 关联 ID", f"{content.attempt} / {content.correlation_id}"),
        ("观测时相", f"{content.before_date.isoformat()} 至 {content.after_date.isoformat()}"),
        ("质量结论", f"{content.quality.conclusion.value} - 四项门槛全部通过"),
    )
    table = Table(
        [
            [Paragraph(_safe(label), styles["small"]), Paragraph(_safe(value), styles["table"])]
            for label, value in rows
        ],
        colWidths=[35 * mm, 139 * mm],
    )
    table.setStyle(_table_style(header=False))
    return table


def _plan_table(styles: dict[str, ParagraphStyle]) -> Table:
    rows = [["序号", "执行 Agent", "受约束步骤"]]
    rows.extend([order, agent, description] for order, agent, description in _PLAN)
    table = Table(
        [
            [Paragraph(_safe(value), styles["table"]) for value in row]
            for row in rows
        ],
        colWidths=[13 * mm, 37 * mm, 124 * mm],
        repeatRows=1,
    )
    table.setStyle(_table_style(header=True))
    return table


def _statistics_table(
    statistics: AreaStatistics,
    styles: dict[str, ParagraphStyle],
) -> Table:
    values = (
        ("增加", statistics.increase_hectares, "#DDEEDF"),
        ("稳定", statistics.stable_hectares, "#F1F4F1"),
        ("减少", statistics.decrease_hectares, "#F5E2DF"),
        ("有效面积", statistics.valid_hectares, "#E5ECE7"),
    )
    table = Table(
        [
            [
                Paragraph(_safe(f"{label} {value:.2f} 公顷"), styles["body"])
                for label, value, _color in values
            ]
        ],
        colWidths=[43.5 * mm] * 4,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (index, 0), (index, 0), colors.HexColor(color))
                for index, (_label, _value, color) in enumerate(values)
            ]
            + [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D4CB")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D4CB")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]
        )
    )
    return table


def _quality_table(quality: QualityMetrics, styles: dict[str, ParagraphStyle]) -> Table:
    values = (
        f"覆盖率 {quality.coverage_ratio:.2%}",
        f"有效像元率 {quality.valid_pixel_ratio:.2%}",
        f"输出完整 {'是' if quality.output_complete else '否'}",
        f"耗时 {quality.elapsed_ms} 毫秒",
        f"结论 {quality.conclusion.value}",
    )
    table = Table(
        [[Paragraph(_safe(value), styles["table"]) for value in values]],
        colWidths=[34.8 * mm] * 5,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E6F1E9")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#A9BDAF")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#A9BDAF")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]
        )
    )
    return table


def _checksum_table(
    artifacts: tuple[ArtifactRef, ...],
    styles: dict[str, ParagraphStyle],
) -> Table:
    ordered = sorted(artifacts, key=lambda artifact: artifact.artifact_type.value)
    rows = [["成果", "字节数", "SHA-256"]]
    rows.extend(
        [
            _ARTIFACT_LABELS[artifact.artifact_type],
            str(artifact.byte_size),
            artifact.checksum_sha256 or "",
        ]
        for artifact in ordered
    )
    table = Table(
        [
            [
                Paragraph(_safe(value), styles["checksum" if column == 2 else "table"])
                for column, value in enumerate(row)
            ]
            for row in rows
        ],
        colWidths=[40 * mm, 22 * mm, 112 * mm],
        repeatRows=1,
    )
    table.setStyle(_table_style(header=True))
    return table


def _traceability_table(content: ReportContent, styles: dict[str, ParagraphStyle]) -> Table:
    rows = (
        ("任务 ID", str(content.task_id)),
        ("尝试次数", str(content.attempt)),
        ("关联 ID", str(content.correlation_id)),
        ("报告生成时间（UTC）", content.created_at.isoformat()),
    )
    table = Table(
        [
            [Paragraph(_safe(label), styles["small"]), Paragraph(_safe(value), styles["table"])]
            for label, value in rows
        ],
        colWidths=[42 * mm, 132 * mm],
    )
    table.setStyle(_table_style(header=False))
    return table


def _table_style(*, header: bool) -> TableStyle:
    commands: list[tuple[object, ...]] = [
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8C8BD")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D6DFD8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 1.8 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8 * mm),
        (
            "ROWBACKGROUNDS",
            (0, 1 if header else 0),
            (-1, -1),
            [colors.white, colors.HexColor("#F5F8F6")],
        ),
    ]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCEADF")))
    return TableStyle(commands)


def _draw_page_chrome(canvas: Canvas, document: SimpleDocTemplate) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(colors.HexColor("#143D2A"))
    canvas.rect(0, height - 7 * mm, width, 7 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#A9BDAF"))
    canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.setFillColor(colors.HexColor("#52705E"))
    canvas.setFont(_FONT_NAME, 7.5)
    canvas.drawString(18 * mm, 9 * mm, "神农溪分布式多 Agent GIS 系统")
    canvas.drawRightString(width - 18 * mm, 9 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def _safe(value: object) -> str:
    return escape(str(value), quote=True)
