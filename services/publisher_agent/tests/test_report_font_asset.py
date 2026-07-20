from __future__ import annotations

import hashlib
from pathlib import Path

from reportlab.pdfbase.ttfonts import TTFont

FONT_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "src" / "hennongxi_publisher_agent" / "assets" / "fonts"
)
FONT_PATH = FONT_DIRECTORY / "NotoSansSC-VF.ttf"
LICENSE_PATH = FONT_DIRECTORY / "LICENSE.txt"
EXPECTED_FONT_SHA256 = "d68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964"
EXPECTED_LICENSE_SHA256 = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bundled_report_font_has_pinned_bytes_license_and_required_chinese_glyphs() -> None:
    assert FONT_PATH.stat().st_size == 17_773_132
    assert _sha256(FONT_PATH) == EXPECTED_FONT_SHA256
    assert _sha256(LICENSE_PATH) == EXPECTED_LICENSE_SHA256
    assert "SIL OPEN FONT LICENSE Version 1.1" in LICENSE_PATH.read_text(encoding="ascii")

    font = TTFont("HennongxiNotoSansSC", FONT_PATH)
    required_text = "神农溪生态变化监测分析质量结论限制校验报告"
    assert all(ord(character) in font.face.charToGlyph for character in required_text)
