"""Certificate rendering: one hard-coded landscape A4 template drawn with ReportLab."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import date
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
NAVY = HexColor("#1F3A5F")
GOLD = HexColor("#B8860B")
GREY = HexColor("#555555")

_font_lock = threading.Lock()


class CertificateGenerationError(ValueError):
    """Raised when a single certificate cannot be rendered."""


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    course_name: str
    issuer: str
    issue_date: date
    certificate_number: str
    achievement: str | None = None
    signatory_name: str | None = None
    signatory_title: str | None = None

    def text_fields(self) -> list[tuple[str, str]]:
        fields = [
            ("recipient name", self.recipient_name),
            ("course name", self.course_name),
            ("issuer", self.issuer),
            ("achievement", self.achievement),
            ("signatory name", self.signatory_name),
            ("signatory title", self.signatory_title),
        ]
        return [(label, text) for label, text in fields if text]


@dataclass(frozen=True)
class _Fonts:
    regular: str
    bold: str
    italic: str


def ensure_renderable(fields: list[tuple[str, str | None]], font_path: str | None) -> None:
    """The built-in PDF fonts only cover Western European text (WinAnsi/cp1252)."""
    if font_path:
        return
    for label, text in fields:
        if not text:
            continue
        try:
            text.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise CertificateGenerationError(
                f"{label} contains a character the built-in certificate font cannot "
                f"render ({text[exc.start]!r}); set CERTGEN_FONT_PATH to a Unicode TTF font"
            ) from exc


def _resolve_fonts(font_path: str | None) -> _Fonts:
    if not font_path:
        return _Fonts("Times-Roman", "Times-Bold", "Times-Italic")
    name = "CertFont-" + hashlib.md5(font_path.encode()).hexdigest()[:8]
    with _font_lock:
        if name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(name, font_path))
            except Exception as exc:  # bad path / corrupt font
                raise CertificateGenerationError(f"cannot load font {font_path!r}: {exc}") from exc
    return _Fonts(name, name, name)


def _fit_size(text: str, font: str, max_size: int, min_size: int, max_width: float) -> int:
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    if stringWidth(text, font, size) > max_width:
        raise CertificateGenerationError("text is too long to fit on the certificate")
    return size


def _centered(c: canvas.Canvas, text: str, font: str, size: float, y: float, color=NAVY) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(PAGE_WIDTH / 2, y, text)


def _format_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def render_certificate(data: CertificateData, font_path: str | None = None) -> bytes:
    """Render a single certificate and return the PDF bytes."""
    ensure_renderable(data.text_fields(), font_path)
    fonts = _resolve_fonts(font_path)
    w, h = PAGE_WIDTH, PAGE_HEIGHT
    text_width = w - 160

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"Certificate - {data.recipient_name}")
    c.setAuthor(data.issuer)
    c.setSubject(data.course_name)

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(4)
    c.rect(25, 25, w - 50, h - 50)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(35, 35, w - 70, h - 70)

    # Heading
    _centered(c, "CERTIFICATE", fonts.bold, 46, h - 110)
    _centered(c, "OF COMPLETION", fonts.regular, 18, h - 138, GOLD)

    # Recipient
    _centered(c, "This is to certify that", fonts.italic, 16, h - 185, GREY)
    name_size = _fit_size(data.recipient_name, fonts.bold, 38, 16, text_width)
    name_y = h - 235
    _centered(c, data.recipient_name, fonts.bold, name_size, name_y)
    name_w = stringWidth(data.recipient_name, fonts.bold, name_size)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(w / 2 - name_w / 2 - 20, name_y - 10, w / 2 + name_w / 2 + 20, name_y - 10)

    # Course (wrapped onto up to two lines, shrinking if needed)
    _centered(c, "has successfully completed", fonts.italic, 16, h - 280, GREY)
    lines: list[str] = []
    course_size = 24
    for course_size in range(24, 13, -2):
        lines = simpleSplit(data.course_name, fonts.bold, course_size, text_width)
        if len(lines) <= 2:
            break
    if len(lines) > 2:
        raise CertificateGenerationError("course name is too long to fit on the certificate")
    y = h - 320
    for line in lines:
        _centered(c, line, fonts.bold, course_size, y)
        y -= course_size * 1.25

    if data.achievement:
        _centered(c, data.achievement, fonts.italic, 16, y - 12, GOLD)

    # Footer: issuer, date, signature, certificate number
    _centered(c, f"Awarded by {data.issuer}", fonts.regular, 14, 165, GREY)

    c.setStrokeColor(GREY)
    c.setLineWidth(0.8)
    c.line(90, 112, 270, 112)
    c.setFont(fonts.regular, 12)
    c.setFillColor(NAVY)
    c.drawCentredString(180, 117, _format_date(data.issue_date))
    c.setFont(fonts.italic, 10)
    c.setFillColor(GREY)
    c.drawCentredString(180, 98, "Date of issue")

    if data.signatory_name:
        sig_x = w - 180
        c.line(sig_x - 90, 112, sig_x + 90, 112)
        c.setFont(fonts.bold, 12)
        c.setFillColor(NAVY)
        c.drawCentredString(sig_x, 98, data.signatory_name)
        if data.signatory_title:
            c.setFont(fonts.italic, 10)
            c.setFillColor(GREY)
            c.drawCentredString(sig_x, 84, data.signatory_title)

    _centered(c, f"Certificate No. {data.certificate_number}", fonts.regular, 9, 52, GREY)

    c.showPage()
    c.save()
    return buf.getvalue()
