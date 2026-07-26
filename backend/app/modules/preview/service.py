from __future__ import annotations

import html
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import nh3
from lxml import etree
from lxml import html as lxml_html

from app.storage.assets import safe_display_url

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+)(?:\s+[^)]*)?\)", re.IGNORECASE)
_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "col",
    "colgroup",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
_ALLOWED_ATTRIBUTES = {
    "*": {"class", "title"},
    "a": {"href"},
    "col": {"span"},
    "img": {"alt", "height", "src", "title", "width"},
    "ol": {"start"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}


@dataclass(frozen=True, slots=True)
class ResourceCandidate:
    original_url: str
    normalized_url: str
    safe_url: str
    name: str
    type: str
    mime_type: str | None
    declared_size: int | None
    source_location: str
    position: int


@dataclass(frozen=True, slots=True)
class PreviewResult:
    html: str
    issues: tuple[str, ...]
    missing_resources: tuple[str, ...]


def sanitize_html(
    raw_html: str,
    *,
    local_resources: dict[str, str] | None = None,
) -> PreviewResult:
    local_resources = {normalize_resource_url(key): value for key, value in (local_resources or {}).items()}
    cleaned = nh3.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        clean_content_tags={"applet", "audio", "embed", "iframe", "object", "script", "style", "video"},
        attributes=_ALLOWED_ATTRIBUTES,
        strip_comments=True,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"},
        url_relative="deny",
    )
    try:
        root = lxml_html.fragment_fromstring(cleaned, create_parent="div")
    except (etree.ParserError, ValueError):
        fallback = f"<pre>{html.escape(cleaned)}</pre>"
        return PreviewResult(_document_html(fallback), ("PREVIEW_PARSE_FAILED",), ())

    missing: list[str] = []
    for element in list(root.iter()):
        tag = element.tag.lower() if isinstance(element.tag, str) else ""
        if tag == "img":
            source = element.get("src")
            replacement = local_resources.get(normalize_resource_url(source)) if source else None
            if replacement:
                element.set("src", replacement)
            else:
                if source:
                    missing.append(safe_display_url(source))
                placeholder = etree.Element("span")
                placeholder.set("class", "yb-missing-resource")
                placeholder.set("data-reason", "not-backed-up")
                placeholder.text = element.get("alt") or "[resource unavailable]"
                placeholder.tail = element.tail
                parent = element.getparent()
                if parent is not None:
                    parent.replace(element, placeholder)
        elif tag == "a":
            href = element.get("href")
            if href and urlsplit(href).scheme in {"http", "https"}:
                element.set("rel", "noopener noreferrer")
                element.set("data-external", "true")

    fragment = "".join(etree.tostring(child, encoding="unicode", method="html") for child in root)
    changed = _semantic_html(raw_html) != _semantic_html(fragment)
    issues = ("HTML_SANITIZED",) if changed else ()
    return PreviewResult(_document_html(fragment), issues, tuple(dict.fromkeys(missing)))


def build_document_preview(
    document: dict[str, Any],
    *,
    local_resources: dict[str, str] | None = None,
) -> PreviewResult:
    doc_type = str(document.get("type") or "unknown")
    issues: list[str] = []
    if doc_type == "Sheet":
        fragment, parse_issue = _render_sheet(document.get("body_sheet"))
    elif doc_type == "Table":
        fragment, parse_issue = _render_table(document.get("body_table"))
    else:
        body_html = document.get("body_html")
        if isinstance(body_html, str) and body_html.strip():
            fragment, parse_issue = body_html, None
        else:
            body = _raw_body(document)
            fragment = f"<pre>{html.escape(body)}</pre>"
            parse_issue = "PREVIEW_HTML_UNAVAILABLE" if body else "PREVIEW_NOT_AVAILABLE"
    if parse_issue:
        issues.append(parse_issue)
    result = sanitize_html(fragment, local_resources=local_resources)
    return PreviewResult(
        html=result.html,
        issues=tuple(dict.fromkeys([*issues, *result.issues])),
        missing_resources=result.missing_resources,
    )


def extract_resource_candidates(document: dict[str, Any]) -> list[ResourceCandidate]:
    found: list[tuple[str, str, str | None, int | None]] = []
    for field in (
        "body",
        "body_html",
        "body_lake",
        "body_sheet",
        "body_table",
        "body_table_pages",
    ):
        value = document.get(field)
        if isinstance(value, str):
            markdown_matches = list(_MARKDOWN_LINK_RE.finditer(value))
            for match in markdown_matches:
                url = match.group(1)
                if not match.group(0).startswith("!") and is_explicit_attachment_url(url):
                    found.append((url, field, None, None))
            if field == "body_html":
                found.extend(_urls_from_html(value, field))
            elif field in {"body_sheet", "body_table"}:
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    parsed = None
                if parsed is not None:
                    _walk_structured_resources(parsed, field, found)
            else:
                for match in _URL_RE.finditer(value):
                    if any(_spans_overlap(match.span(), item.span()) for item in markdown_matches):
                        continue
                    url = _trim_bare_url(match.group(0))
                    if is_explicit_attachment_url(url):
                        found.append((url, field, None, None))
        elif value is not None:
            _walk_structured_resources(value, field, found)

    output: list[ResourceCandidate] = []
    seen: set[str] = set()
    for original, location, mime_type, declared_size in found:
        try:
            normalized = normalize_resource_url(original)
        except ValueError:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        path = PurePosixPath(unquote(urlsplit(original).path))
        name = path.name[:512] or f"resource-{len(output) + 1}"
        output.append(
            ResourceCandidate(
                original_url=original,
                normalized_url=normalized,
                safe_url=safe_display_url(original),
                name=name,
                type="attachment",
                mime_type=mime_type,
                declared_size=declared_size,
                source_location=location,
                position=len(output),
            )
        )
    return output


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _trim_bare_url(value: str) -> str:
    value = value.rstrip(".,;:!?")
    delimiter_pairs = {")": "(", "]": "[", "}": "{"}
    while value and value[-1] in delimiter_pairs:
        closing = value[-1]
        opening = delimiter_pairs[closing]
        if value.count(closing) <= value.count(opening):
            break
        value = value[:-1]
    return value


def normalize_resource_url(value: str | None) -> str:
    if not value:
        raise ValueError("resource URL is empty")
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("resource URL must be absolute HTTP(S)")
    scheme = parts.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    port = parts.port
    authority = _normalize_resource_hostname(parts.hostname)
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))


def _normalize_resource_hostname(value: str) -> str:
    hostname = value.rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("resource URL hostname is invalid") from exc
        labels = hostname.split(".")
        if len(hostname) > 253 or any(
            not label
            or len(label) > 63
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
            for label in labels
        ):
            raise ValueError("resource URL hostname is invalid") from None
        return hostname
    if address.version == 6:
        return f"[{address.compressed}]"
    return address.compressed


def _urls_from_html(value: str, location: str) -> list[tuple[str, str, str | None, int | None]]:
    try:
        root = lxml_html.fragment_fromstring(value, create_parent="div")
    except (etree.ParserError, ValueError):
        return []
    result: list[tuple[str, str, str | None, int | None]] = []
    for element in root.iter():
        tag = element.tag.lower() if isinstance(element.tag, str) else ""
        if tag == "a" and element.get("href") and is_explicit_attachment_url(element.get("href")):
            result.append((element.get("href"), location, None, None))
    return result


def _walk_structured_resources(
    value: Any,
    location: str,
    output: list[tuple[str, str, str | None, int | None]],
) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_structured_resources(item, f"{location}[{index}]", output)
    elif isinstance(value, dict):
        mime = value.get("mime_type") or value.get("mime")
        size = value.get("size")
        declared_size = size if isinstance(size, int) and size >= 0 else None
        for key, item in value.items():
            child_location = f"{location}.{key}"
            key_lower = key.lower()
            if key_lower == "attachment_url" and isinstance(item, str):
                if item.lower().startswith(("http://", "https://")):
                    output.append(
                        (item, child_location, mime if isinstance(mime, str) else None, declared_size)
                    )
            elif key_lower in {"url", "download_url"} and isinstance(item, str):
                if is_explicit_attachment_url(item):
                    output.append(
                        (item, child_location, mime if isinstance(mime, str) else None, declared_size)
                    )
            else:
                _walk_structured_resources(item, child_location, output)


def _raw_body(document: dict[str, Any]) -> str:
    for key in ("body", "body_lake", "body_sheet", "body_table"):
        value = document.get(key)
        if isinstance(value, str):
            return value
        if value is not None:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return ""


def _render_sheet(value: Any) -> tuple[str, str | None]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        if not isinstance(parsed, (dict, list)):
            raise ValueError
    except (TypeError, ValueError):
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return f"<pre>{html.escape(raw or '')}</pre>", "SHEET_PARSE_FAILED"
    sheets = parsed.get("sheets") if isinstance(parsed, dict) else parsed
    if not isinstance(sheets, list):
        sheets = [parsed]
    sections: list[str] = []
    for index, sheet in enumerate(sheets):
        name = sheet.get("name") if isinstance(sheet, dict) else None
        rows = sheet.get("rows", sheet.get("data", [])) if isinstance(sheet, dict) else sheet
        sections.append(f"<section><h2>{html.escape(str(name or f'Sheet {index + 1}'))}</h2>")
        sections.append(_rows_table(rows))
        sections.append("</section>")
    return "".join(sections), None


def _render_table(value: Any) -> tuple[str, str | None]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        if not isinstance(parsed, (dict, list)):
            raise ValueError
    except (TypeError, ValueError):
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return f"<pre>{html.escape(raw or '')}</pre>", "TABLE_PARSE_FAILED"
    if isinstance(parsed, dict):
        rows = parsed.get("records", parsed.get("rows", parsed.get("data", [])))
    else:
        rows = parsed
    return _rows_table(rows), None


def _rows_table(rows: Any) -> str:
    if not isinstance(rows, list) or not rows:
        return "<table><tbody></tbody></table>"
    if all(isinstance(row, dict) for row in rows):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        header = "".join(f"<th>{html.escape(str(column))}</th>" for column in columns)
        body = "".join(
            "<tr>"
            + "".join(f"<td>{html.escape(_cell_text(row.get(column)))}</td>" for column in columns)
            + "</tr>"
            for row in rows
        )
        return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(_cell_text(cell))}</td>" for cell in _as_row(row)) + "</tr>"
        for row in rows
    )
    return f"<table><tbody>{body}</tbody></table>"


def _as_row(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def is_explicit_attachment_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return "/attachments/" in path


def _semantic_html(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _document_html(fragment: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer"></head><body>'
        f"{fragment}</body></html>"
    )
