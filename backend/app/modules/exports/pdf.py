from __future__ import annotations

import html
import os
from io import BytesIO
from pathlib import Path
from typing import Any

from lxml import etree
from lxml import html as lxml_html
from markdown_it import MarkdownIt

_PDF_STYLES = """
@page { size: A4; margin: 18mm 17mm 20mm;
  @bottom-center { content: counter(page); color: #667085; font-size: 9pt; } }
body { color: #1f2937; font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
  font-size: 10.5pt; line-height: 1.75; }
h1, h2, h3, h4 { color: #111827; font-weight: 650; line-height: 1.35; break-after: avoid; }
h1 { border-bottom: 1px solid #d0d5dd; font-size: 24pt; margin: 0 0 18pt; padding-bottom: 8pt; }
h2 { border-bottom: 1px solid #eaecf0; font-size: 17pt; margin: 22pt 0 10pt; padding-bottom: 5pt; }
h3 { font-size: 13pt; margin: 18pt 0 8pt; }
p, ul, ol, blockquote, pre, table { margin: 0 0 10pt; }
a { color: #175cd3; text-decoration: none; overflow-wrap: anywhere; }
blockquote { border-left: 3px solid #98a2b3; color: #475467; margin-left: 0; padding: 5pt 10pt; }
code { background: #f2f4f7; border-radius: 2px; font-family: "Noto Sans Mono CJK SC", monospace;
  font-size: 9pt; padding: 1px 3px; }
pre { background: #101828; border-radius: 4px; color: #f2f4f7; line-height: 1.55;
  padding: 10pt; white-space: pre-wrap; word-break: break-word; }
pre code { background: transparent; color: inherit; padding: 0; }
table { border-collapse: collapse; font-size: 9.5pt; width: 100%; }
th, td { border: 1px solid #d0d5dd; padding: 5pt 6pt; text-align: left; vertical-align: top; }
th { background: #f9fafb; font-weight: 650; }
.remote-image { border: 1px solid #d0d5dd; color: #667085; font-size: 9pt; padding: 6pt 8pt; }
"""


def render_markdown_pdf(markdown: str) -> bytes:
    renderer = MarkdownIt("commonmark", {"html": False, "breaks": False})
    renderer.enable("table")
    body = renderer.render(markdown)
    body = _replace_remote_images(body)
    document = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<style>{_PDF_STYLES}</style></head><body>{body}</body></html>"
    )
    if os.name == "nt":
        return _render_bitmap_pdf(markdown)
    try:
        from weasyprint import HTML  # type: ignore[import-untyped]

        result = HTML(string=document).write_pdf()
        if not isinstance(result, bytes):
            raise RuntimeError("PDF renderer did not return bytes")
        return result
    except OSError:
        return _render_bitmap_pdf(markdown)


def _replace_remote_images(fragment: str) -> str:
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, ValueError):
        return fragment
    for image in list(root.iter("img")):
        source = image.get("src") or ""
        label = image.get("alt") or "文章图片"
        placeholder = etree.Element("p")
        placeholder.set("class", "remote-image")
        placeholder.text = f"{label} (remote image is not embedded in PDF)"
        if source:
            link = etree.SubElement(placeholder, "a")
            link.set("href", source)
            link.text = f" {source}"
        placeholder.tail = image.tail
        parent = image.getparent()
        if parent is not None:
            parent.replace(image, placeholder)
    return "".join(
        etree.tostring(child, encoding="unicode", method="html") for child in root
    ) or f"<p>{html.escape(root.text or '')}</p>"


def _render_bitmap_pdf(markdown: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1240, 1754
    margin = 96
    available_width = width - margin * 2
    regular_path = _font_path(bold=False)
    bold_path = _font_path(bold=True) or regular_path
    font_cache: dict[tuple[int, bool], Any] = {}

    def font(size: int, *, bold: bool = False) -> Any:
        key = (size, bold)
        if key not in font_cache:
            path = bold_path if bold else regular_path
            font_cache[key] = ImageFont.truetype(str(path), size) if path else ImageFont.load_default()
        return font_cache[key]

    pages: list[Any] = []
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    y = margin

    def new_page() -> None:
        nonlocal page, draw, y
        pages.append(page)
        page = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(page)
        y = margin

    for text, size, bold, spacing in _pdf_text_blocks(markdown):
        selected_font = font(size, bold=bold)
        lines = _wrap_text(draw, text, selected_font, available_width)
        line_height = max(size + 10, int(size * 1.55))
        required = max(line_height, len(lines) * line_height) + spacing
        if y + required > height - margin and y > margin:
            new_page()
        for line in lines or [""]:
            draw.text((margin, y), line, fill="#1f2937", font=selected_font)
            y += line_height
        y += spacing
    pages.append(page)
    output = BytesIO()
    pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return output.getvalue()


def _pdf_text_blocks(markdown: str) -> list[tuple[str, int, bool, int]]:
    renderer = MarkdownIt("commonmark", {"html": False})
    renderer.enable("table")
    tokens = renderer.parse(markdown)
    output: list[tuple[str, int, bool, int]] = []
    heading_level: int | None = None
    for token in tokens:
        if token.type == "heading_open":
            heading_level = int(token.tag[1:])
        elif token.type == "heading_close":
            heading_level = None
        elif token.type == "inline" and token.content.strip():
            size = {1: 32, 2: 26, 3: 22}.get(heading_level or 0, 17)
            output.append((token.content.strip(), size, heading_level is not None, 14))
        elif token.type in {"fence", "code_block"}:
            output.append((token.content.rstrip(), 15, False, 14))
        elif token.type == "hr":
            output.append(("-" * 72, 14, False, 12))
    return output or [(markdown, 17, False, 14)]


def _wrap_text(draw: Any, value: str, font: Any, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in value.splitlines() or [""]:
        current = ""
        for character in paragraph:
            candidate = f"{current}{character}"
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = character
            else:
                current = candidate
        lines.append(current)
    return lines


def _font_path(*, bold: bool) -> Path | None:
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf" if bold else "C:/Windows/Fonts/simsun.ttc"),
        Path(
            "/usr/share/fonts/opentype/noto/"
            + ("NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc")
        ),
    )
    return next((path for path in candidates if path.is_file()), None)
