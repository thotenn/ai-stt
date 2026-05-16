from __future__ import annotations

import re
from dataclasses import dataclass


class MultipartError(ValueError):
    pass


@dataclass(frozen=True)
class MultipartPart:
    name: str
    filename: str | None
    content_type: str | None
    content: bytes


_BOUNDARY_RE = re.compile(r"boundary=(?:\"([^\"]+)\"|([^;\s]+))", re.IGNORECASE)
_DISPOSITION_NAME_RE = re.compile(r'name="([^"]+)"', re.IGNORECASE)
_DISPOSITION_FILENAME_RE = re.compile(r'filename="([^"]*)"', re.IGNORECASE)
_HEADER_SPLIT = b"\r\n\r\n"
_LINE_SPLIT = b"\r\n"


def _extract_boundary(content_type: str) -> str:
    match = _BOUNDARY_RE.search(content_type)
    if not match:
        raise MultipartError("Content-Type is missing the boundary parameter")
    return match.group(1) or match.group(2)


def _split_headers(part: bytes) -> tuple[dict[str, str], bytes]:
    header_end = part.find(_HEADER_SPLIT)
    if header_end == -1:
        raise MultipartError("part is missing header/body separator")
    raw_headers = part[:header_end]
    content = part[header_end + len(_HEADER_SPLIT):]

    headers: dict[str, str] = {}
    for line in raw_headers.split(_LINE_SPLIT):
        if not line:
            continue
        try:
            decoded = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MultipartError(f"non-utf8 header bytes: {exc}") from exc
        if ":" not in decoded:
            raise MultipartError(f"malformed header line {decoded!r}")
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers, content


def parse_multipart(body: bytes, content_type: str) -> dict[str, MultipartPart]:
    boundary = _extract_boundary(content_type)
    delimiter = b"--" + boundary.encode("ascii")
    closing = delimiter + b"--"

    if not body.lstrip().startswith(delimiter):
        raise MultipartError("body does not start with the boundary delimiter")

    end_index = body.rfind(closing)
    if end_index == -1:
        raise MultipartError("body is missing the closing boundary")

    raw_parts = body[: end_index].split(delimiter)
    parts: dict[str, MultipartPart] = {}

    for raw in raw_parts:
        chunk = raw.strip(b"\r\n")
        if not chunk:
            continue

        headers, content = _split_headers(chunk)
        if content.endswith(b"\r\n"):
            content = content[:-2]

        disposition = headers.get("content-disposition", "")
        if "form-data" not in disposition.lower():
            continue

        name_match = _DISPOSITION_NAME_RE.search(disposition)
        if not name_match:
            raise MultipartError("part is missing the name parameter")
        name = name_match.group(1)

        filename_match = _DISPOSITION_FILENAME_RE.search(disposition)
        filename = filename_match.group(1) if filename_match else None

        parts[name] = MultipartPart(
            name=name,
            filename=filename or None,
            content_type=headers.get("content-type"),
            content=content,
        )

    return parts
