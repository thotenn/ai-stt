from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.error
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from stt_sandbox.api import ServiceConfig, _make_handler


BOUNDARY = "----TestBoundary12345"


def _multipart_body(audio_bytes: bytes, audio_mime: str, **fields: str) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="clip.wav"\r\n'
        f"Content-Type: {audio_mime}\r\n\r\n".encode()
        + audio_bytes
        + b"\r\n"
    )
    parts.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(parts)


@contextmanager
def _serve(config: ServiceConfig, engine):
    handler_cls = _make_handler(config, engine)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_config(stream_enabled: bool = True, max_body: int = 25 * 1024 * 1024 + 1024 * 1024, mode: str = "both") -> ServiceConfig:
    return ServiceConfig(
        host="127.0.0.1",
        port=0,
        mode=mode,
        engine_url="",
        cors_origin="*",
        max_request_body_bytes=max_body,
        stream_enabled=stream_enabled,
        log_transcripts=False,
        models_dir=Path("models/whisper"),
    )


def _post(url: str, data: bytes, content_type: str, expect_status: int | None = None) -> tuple[int, dict | str]:
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8")
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        decoded = body
    if expect_status is not None:
        assert status == expect_status, f"expected {expect_status}, got {status}: {decoded!r}"
    return status, decoded


def _get(url: str) -> tuple[int, dict | str]:
    with urllib.request.urlopen(url) as response:
        status = response.status
        body = response.read().decode("utf-8")
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def test_health_endpoint(shared_engine, short_es_wav):
    config = _make_config()
    with _serve(config, shared_engine) as (host, port):
        status, data = _get(f"http://{host}:{port}/health")
    assert status == 200
    assert data["status"] == "ok"
    assert data["mode"] == "both"
    assert data["engine"] is True
    assert data["language"] == "es"


def test_models_endpoint(shared_engine):
    config = _make_config()
    with _serve(config, shared_engine) as (host, port):
        status, data = _get(f"http://{host}:{port}/models")
    assert status == 200
    assert "default" in data
    assert isinstance(data["models"], list)
    assert any(m["name"] == data["default"] for m in data["models"])


def test_transcribe_multipart_happy_path(shared_engine, short_es_wav):
    config = _make_config()
    body = _multipart_body(short_es_wav.read_bytes(), "audio/wav", model=shared_engine.default_model)
    with _serve(config, shared_engine) as (host, port):
        status, data = _post(
            f"http://{host}:{port}/transcribe",
            body,
            f'multipart/form-data; boundary="{BOUNDARY}"',
            expect_status=200,
        )
    assert data["text"], data
    assert data["language"] == "es"
    assert data["duration_seconds"] > 0
    assert data["model"] == shared_engine.default_model
    assert "planetas" in data["text"].lower()


def test_transcribe_empty_body(shared_engine):
    config = _make_config()
    with _serve(config, shared_engine) as (host, port):
        status, data = _post(
            f"http://{host}:{port}/transcribe",
            b"",
            f'multipart/form-data; boundary="{BOUNDARY}"',
            expect_status=400,
        )
    assert "error" in data


def test_transcribe_missing_audio_field(shared_engine):
    config = _make_config()
    body = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\nrhasspy/foo\r\n'
        f"--{BOUNDARY}--\r\n"
    ).encode()
    with _serve(config, shared_engine) as (host, port):
        status, data = _post(
            f"http://{host}:{port}/transcribe",
            body,
            f'multipart/form-data; boundary="{BOUNDARY}"',
            expect_status=400,
        )
    assert "audio" in data["error"]


def test_transcribe_body_size_cap(shared_engine, short_es_wav):
    config = _make_config(max_body=1024)
    body = _multipart_body(short_es_wav.read_bytes(), "audio/wav")
    with _serve(config, shared_engine) as (host, port):
        status, data = _post(
            f"http://{host}:{port}/transcribe",
            body,
            f'multipart/form-data; boundary="{BOUNDARY}"',
            expect_status=413,
        )
    assert "error" in data


def test_transcribe_stream_returns_not_implemented(shared_engine, short_es_wav):
    config = _make_config()
    body = _multipart_body(short_es_wav.read_bytes(), "audio/wav")
    with _serve(config, shared_engine) as (host, port):
        status, data = _post(
            f"http://{host}:{port}/transcribe/stream",
            body,
            f'multipart/form-data; boundary="{BOUNDARY}"',
            expect_status=501,
        )
    assert "Phase 3" in data["error"]


def test_transcribe_unsupported_mime(shared_engine):
    config = _make_config()
    body = _multipart_body(b"\x1aE\xdf\xa3 not a wav", "audio/webm")
    with _serve(config, shared_engine) as (host, port):
        status, data = _post(
            f"http://{host}:{port}/transcribe",
            body,
            f'multipart/form-data; boundary="{BOUNDARY}"',
            expect_status=400,
        )
    assert "Phase 2" in data["error"] or "unsupported" in data["error"].lower()


def test_engine_mode_no_gui(shared_engine):
    config = _make_config(mode="engine")
    with _serve(config, shared_engine) as (host, port):
        try:
            urllib.request.urlopen(f"http://{host}:{port}/")
            pytest.fail("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
