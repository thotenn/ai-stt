from __future__ import annotations

import pytest

from stt_sandbox.multipart import MultipartError, parse_multipart


BOUNDARY = "----TestBoundary12345"
CONTENT_TYPE = f'multipart/form-data; boundary="{BOUNDARY}"'


def _build(*parts: bytes) -> bytes:
    delimiter = f"--{BOUNDARY}\r\n".encode()
    closing = f"\r\n--{BOUNDARY}--\r\n".encode()
    body = b""
    for i, part in enumerate(parts):
        body += delimiter + part
        if i < len(parts) - 1:
            body += b"\r\n"
    return body + closing


def test_parse_audio_field():
    audio_bytes = b"RIFF....WAVE...."
    body = _build(
        (
            b'Content-Disposition: form-data; name="audio"; filename="clip.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n"
        ) + audio_bytes,
    )
    parts = parse_multipart(body, CONTENT_TYPE)
    assert "audio" in parts
    audio = parts["audio"]
    assert audio.content == audio_bytes
    assert audio.content_type == "audio/wav"
    assert audio.filename == "clip.wav"


def test_parse_multiple_fields():
    body = _build(
        b'Content-Disposition: form-data; name="audio"; filename="x.wav"\r\n'
        b'Content-Type: audio/wav\r\n\r\nWAV-BYTES',
        b'Content-Disposition: form-data; name="model"\r\n\r\nrhasspy/foo',
        b'Content-Disposition: form-data; name="language"\r\n\r\nes',
    )
    parts = parse_multipart(body, CONTENT_TYPE)
    assert parts["audio"].content == b"WAV-BYTES"
    assert parts["model"].content == b"rhasspy/foo"
    assert parts["language"].content == b"es"


def test_missing_boundary_raises():
    with pytest.raises(MultipartError):
        parse_multipart(b"whatever", "multipart/form-data")


def test_missing_closing_boundary_raises():
    body = (
        f"--{BOUNDARY}\r\n".encode()
        + b'Content-Disposition: form-data; name="audio"\r\n\r\nbytes\r\n'
    )
    with pytest.raises(MultipartError):
        parse_multipart(body, CONTENT_TYPE)


def test_part_without_name_raises():
    body = _build(b'Content-Disposition: form-data\r\n\r\nno-name')
    with pytest.raises(MultipartError):
        parse_multipart(body, CONTENT_TYPE)
