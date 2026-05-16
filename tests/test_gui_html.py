from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from stt_sandbox.api import ServiceConfig, _make_handler
from stt_sandbox.gui_html import INDEX_HTML, render_index


def _config(mode: str = "both", engine_url: str = "") -> ServiceConfig:
    return ServiceConfig(
        host="127.0.0.1",
        port=0,
        mode=mode,
        engine_url=engine_url,
        cors_origin="*",
        max_request_body_bytes=1024 * 1024,
        log_transcripts=False,
        models_dir=Path("models/whisper"),
    )


@contextmanager
def _serve(config, engine=None):
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


def test_render_replaces_engine_url_token():
    html = render_index("https://example.com")
    assert "__ENGINE_URL_JSON__" not in html
    assert '"https://example.com"' in html


def test_render_empty_engine_url():
    html = render_index("")
    assert "__ENGINE_URL_JSON__" not in html
    assert '""' in html


def test_render_escapes_html_break_attempt():
    payload = '</script><script>alert("xss")</script>'
    html = render_index(payload)
    assert "__ENGINE_URL_JSON__" not in html
    assert payload not in html
    assert "</script><script>" not in html
    assert "\\u003c/script\\u003e" in html or "\\u003c/script>" in html


def test_render_escapes_ampersand_and_angle():
    html = render_index("https://x.com/?a=1&b=2&c=<>")
    assert "__ENGINE_URL_JSON__" not in html
    assert "<>" not in html.split("__ENGINE_URL_JSON__")[0][-200:] if "__ENGINE_URL_JSON__" in html else True
    assert "\\u003c" in html


def test_index_html_template_has_token_marker():
    assert "__ENGINE_URL_JSON__" in INDEX_HTML


def test_root_route_serves_html_in_both_mode():
    with _serve(_config(mode="both")) as (host, port):
        with urllib.request.urlopen(f"http://{host}:{port}/") as response:
            assert response.status == 200
            content_type = response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8")
    assert content_type.startswith("text/html")
    assert "<!doctype html>" in body
    assert "STT Sandbox" in body
    assert "__ENGINE_URL_JSON__" not in body


def test_root_route_404_in_engine_mode():
    with _serve(_config(mode="engine")) as (host, port):
        try:
            urllib.request.urlopen(f"http://{host}:{port}/")
            pytest.fail("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


def test_gui_mode_serves_root_and_health_only():
    with _serve(_config(mode="gui", engine_url="https://remote.example")) as (host, port):
        with urllib.request.urlopen(f"http://{host}:{port}/") as response:
            html = response.read().decode("utf-8")
            assert "STT Sandbox" in html
            assert '"https://remote.example"' in html

        with urllib.request.urlopen(f"http://{host}:{port}/health") as response:
            assert response.status == 200

        try:
            urllib.request.urlopen(f"http://{host}:{port}/models")
            pytest.fail("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


def test_gui_mode_without_engine_url_fails_to_build_config():
    with pytest.raises(ValueError):
        ServiceConfig(
            host="127.0.0.1",
            port=0,
            mode="gui",
            engine_url="",
            cors_origin="*",
            max_request_body_bytes=1024,
            log_transcripts=False,
            models_dir=Path("models/whisper"),
        )
