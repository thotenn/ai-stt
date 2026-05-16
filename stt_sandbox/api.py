from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .audio import AudioDecodeError, SUPPORTED_MIME_TYPES, decode_to_pcm
from .config import env_bool, env_float, env_int, load_env
from .engine import SttEngine, SttError
from .gui_html import render_index
from .models import DEFAULT_LANGUAGE, DEFAULT_MODEL, MODELS
from .multipart import MultipartError, parse_multipart


_LOGGER = logging.getLogger("stt_sandbox.api")

VALID_MODES = ("both", "engine", "gui")


class ServiceConfig:
    def __init__(
        self,
        host: str,
        port: int,
        mode: str,
        engine_url: str,
        cors_origin: str,
        max_request_body_bytes: int,
        log_transcripts: bool,
        models_dir: Path,
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"STT_SERVICE_MODE must be one of {VALID_MODES}, got {mode!r}")
        if mode == "gui" and not engine_url:
            raise ValueError("STT_SERVICE_MODE=gui requires STT_ENGINE_URL")

        self.host = host
        self.port = port
        self.mode = mode
        self.engine_url = engine_url
        self.cors_origin = cors_origin
        self.max_request_body_bytes = max_request_body_bytes
        self.log_transcripts = log_transcripts
        self.models_dir = models_dir

    @property
    def engine_enabled(self) -> bool:
        return self.mode in ("both", "engine")

    @property
    def gui_enabled(self) -> bool:
        return self.mode in ("both", "gui")


def _build_engine_from_env(models_dir: Path) -> SttEngine:
    vad_enabled = env_bool("STT_VAD_ENABLED", True)
    vad_parameters: dict[str, Any] | None = None
    if vad_enabled:
        vad_parameters = {
            "threshold": env_float("STT_VAD_THRESHOLD", 0.5),
            "min_speech_duration_ms": env_int("STT_VAD_MIN_SPEECH_MS", 250),
            "min_silence_duration_ms": env_int("STT_VAD_MIN_SILENCE_MS", 2000),
        }

    initial_prompt = os.environ.get("STT_INITIAL_PROMPT") or None

    return SttEngine(
        models_dir=models_dir,
        default_model=DEFAULT_MODEL,
        default_language=DEFAULT_LANGUAGE,
        compute_type=os.environ.get("STT_COMPUTE_TYPE", "int8"),
        cpu_threads=env_int("STT_CPU_THREADS", 4),
        beam_size=env_int("STT_BEAM_SIZE", 1),
        initial_prompt=initial_prompt,
        vad_parameters=vad_parameters,
    )


class SttRequestHandler(BaseHTTPRequestHandler):
    server_version = "stt-sandbox/0.1"

    config: ServiceConfig
    engine: SttEngine | None

    def log_message(self, format: str, *args: Any) -> None:
        _LOGGER.info("%s - %s", self.address_string(), format % args)

    def _set_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.config.cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._set_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self._set_cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._set_cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/health":
            self._handle_health()
            return

        if path == "/models":
            if not self.config.engine_enabled:
                self._send_error_json(HTTPStatus.NOT_FOUND, "engine disabled in this mode")
                return
            self._handle_models()
            return

        if path == "/":
            if not self.config.gui_enabled:
                self._send_error_json(HTTPStatus.NOT_FOUND, "gui disabled in this mode")
                return
            html = render_index(self.config.engine_url)
            self._send_text(HTTPStatus.OK, html, "text/html; charset=utf-8")
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, f"no route for GET {path!r}")

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/transcribe":
            if not self.config.engine_enabled:
                self._send_error_json(HTTPStatus.NOT_FOUND, "engine disabled in this mode")
                return
            self._handle_transcribe()
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, f"no route for POST {path!r}")

    def _handle_health(self) -> None:
        loaded = ""
        if self.engine is not None:
            loaded = self.engine.default_model if self.config.engine_enabled else ""
        payload = {
            "status": "ok",
            "mode": self.config.mode,
            "engine": self.config.engine_enabled,
            "gui": self.config.gui_enabled,
            "model_loaded": loaded,
            "language": DEFAULT_LANGUAGE,
        }
        self._send_json(HTTPStatus.OK, payload)

    def _handle_models(self) -> None:
        assert self.engine is not None
        models_payload = []
        for name, spec in MODELS.items():
            models_payload.append({
                "name": spec.name,
                "size": spec.size,
                "quantization": spec.quantization,
                "language": spec.language,
                "loaded": name in self.engine._cache,
            })
        self._send_json(HTTPStatus.OK, {"default": DEFAULT_MODEL, "models": models_payload})

    def _read_body(self) -> bytes | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid Content-Length header")
            return None

        if content_length <= 0:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "empty request body")
            return None

        if content_length > self.config.max_request_body_bytes:
            self._send_error_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"request body exceeds {self.config.max_request_body_bytes} bytes",
            )
            return None

        return self.rfile.read(content_length)

    def _parse_request(self, body: bytes) -> dict[str, Any] | None:
        content_type = self.headers.get("Content-Type", "") or ""
        ctype_lower = content_type.lower()

        if ctype_lower.startswith("multipart/form-data"):
            return self._parse_multipart(body, content_type)

        if ctype_lower.startswith("application/json"):
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, f"invalid JSON body: {exc}")
                return None
            return self._parse_json_payload(payload)

        if not content_type:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "missing Content-Type header")
            return None

        self._send_error_json(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            f"unsupported Content-Type {content_type!r}; expected multipart/form-data or application/json",
        )
        return None

    def _parse_multipart(self, body: bytes, content_type: str) -> dict[str, Any] | None:
        try:
            parts = parse_multipart(body, content_type)
        except MultipartError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, f"invalid multipart payload: {exc}")
            return None

        audio_part = parts.get("audio")
        if audio_part is None:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "missing 'audio' field in multipart body")
            return None
        if not audio_part.content:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "'audio' field is empty")
            return None

        def _text(name: str) -> str | None:
            part = parts.get(name)
            if part is None:
                return None
            try:
                value = part.content.decode("utf-8").strip()
            except UnicodeDecodeError:
                return None
            return value or None

        return {
            "audio": audio_part.content,
            "mime": audio_part.content_type or None,
            "model": _text("model"),
            "language": _text("language"),
            "initial_prompt": _text("initial_prompt"),
        }

    def _parse_json_payload(self, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
            return None

        audio_b64 = payload.get("audio_base64")
        if not isinstance(audio_b64, str) or not audio_b64:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "missing 'audio_base64' field")
            return None

        try:
            audio_bytes = base64.b64decode(audio_b64, validate=True)
        except binascii.Error as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, f"invalid base64 audio: {exc}")
            return None

        return {
            "audio": audio_bytes,
            "mime": payload.get("mime"),
            "model": payload.get("model"),
            "language": payload.get("language"),
            "initial_prompt": payload.get("initial_prompt"),
        }

    def _handle_transcribe(self) -> None:
        assert self.engine is not None

        body = self._read_body()
        if body is None:
            return

        parsed = self._parse_request(body)
        if parsed is None:
            return

        try:
            wav_path = decode_to_pcm(parsed["audio"], parsed["mime"])
        except AudioDecodeError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        try:
            result = self.engine.transcribe(
                wav_path,
                model=parsed["model"],
                language=parsed["language"],
                initial_prompt=parsed["initial_prompt"],
            )
        except KeyError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc).strip("'\""))
            return
        except SttError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        except Exception as exc:
            _LOGGER.exception("unexpected transcription error: %s", exc)
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "internal transcription error")
            return
        finally:
            wav_path.unlink(missing_ok=True)

        if self.config.log_transcripts:
            _LOGGER.debug("transcript: %s", result.text)

        self._send_json(HTTPStatus.OK, result.to_dict())


def _make_handler(config: ServiceConfig, engine: SttEngine | None) -> type[SttRequestHandler]:
    return type(
        "BoundSttRequestHandler",
        (SttRequestHandler,),
        {"config": config, "engine": engine},
    )


def _config_from_env() -> ServiceConfig:
    models_dir = Path(os.environ.get("STT_MODELS_DIR", "models/whisper"))
    return ServiceConfig(
        host=os.environ.get("STT_HOST", "127.0.0.1"),
        port=env_int("STT_PORT", 8000),
        mode=os.environ.get("STT_SERVICE_MODE", "both"),
        engine_url=os.environ.get("STT_ENGINE_URL", ""),
        cors_origin=os.environ.get("STT_CORS_ORIGIN", "*"),
        max_request_body_bytes=env_int("STT_MAX_REQUEST_BODY_BYTES", 25 * 1024 * 1024 + 1024 * 1024),
        log_transcripts=env_bool("STT_LOG_TRANSCRIPTS", False),
        models_dir=models_dir,
    )


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="stt-sandbox-api", description="STT sandbox HTTP server")
    parser.add_argument("--host", default=None, help="bind host (default from STT_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="bind port (default from STT_PORT or 8000)")
    parser.add_argument("--mode", choices=VALID_MODES, default=None, help="service mode override")
    parser.add_argument("--engine-url", default=None, help="remote engine URL (gui mode only)")
    parser.add_argument("--no-preload", action="store_true", help="skip preloading the default model")
    parser.add_argument("--debug", action="store_true", help="enable DEBUG logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.host is not None:
        os.environ["STT_HOST"] = args.host
    if args.port is not None:
        os.environ["STT_PORT"] = str(args.port)
    if args.mode is not None:
        os.environ["STT_SERVICE_MODE"] = args.mode
    if args.engine_url is not None:
        os.environ["STT_ENGINE_URL"] = args.engine_url

    try:
        config = _config_from_env()
    except ValueError as exc:
        _LOGGER.error("configuration error: %s", exc)
        return 2

    engine: SttEngine | None = None
    if config.engine_enabled:
        engine = _build_engine_from_env(config.models_dir)
        if not args.no_preload:
            _LOGGER.info("preloading default model %s ...", DEFAULT_MODEL)
            try:
                engine.preload(DEFAULT_MODEL)
            except SttError as exc:
                _LOGGER.error("preload failed: %s", exc)
                return 3

    handler_cls = _make_handler(config, engine)
    server = ThreadingHTTPServer((config.host, config.port), handler_cls)
    _LOGGER.info(
        "stt-sandbox listening on http://%s:%d (mode=%s, supported MIMEs=%s)",
        config.host, config.port, config.mode, sorted(SUPPORTED_MIME_TYPES),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _LOGGER.info("shutdown requested")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
