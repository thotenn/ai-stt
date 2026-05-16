# Python Client

Patterns for calling `stt-sandbox` from Python. Pick whichever fits your stack:

- **`requests`** is the recommended choice; the multipart body is one line.
- **stdlib `urllib`** works if you can't add a dep (RPi minimal image, etc.). It's verbose but zero-install.

All examples target `https://ai.stt.thotenn.com`. Replace with your engine URL.

## Install

```bash
pip install requests
```

(For RPi mic capture, you'll also want `sounddevice` + `numpy` — see [`rpi_recipe.md`](rpi_recipe.md).)

## Pattern A — One-shot transcription from a file path

```python
import requests

ENGINE = "https://ai.stt.thotenn.com"

with open("recording.wav", "rb") as fp:
    response = requests.post(
        f"{ENGINE}/transcribe",
        files={"audio": ("recording.wav", fp, "audio/wav")},
        timeout=30,
    )

response.raise_for_status()
data = response.json()
print(data["text"])
print(f"decode={data['decode_seconds']}s rtf={data['rtf']}")
```

The `files=` argument tells `requests` to use `multipart/form-data` automatically. You don't need to build the boundary yourself.

## Pattern B — Override model and language

```python
response = requests.post(
    f"{ENGINE}/transcribe",
    files={"audio": ("clip.webm", audio_bytes, "audio/webm")},
    data={
        "model": "rhasspy/faster-whisper-small-int8",
        "language": "es",
    },
    timeout=30,
)
```

`data=` adds plain text form fields alongside the binary audio.

## Pattern C — `initial_prompt` for domain biasing

`initial_prompt` is fed to Whisper as a few-shot hint. Use it to bias the decoder toward in-domain vocabulary — names, technical terms, the current lesson topic. Especially useful for proper nouns the base model fumbles ("Mercurio", "Triceratops").

```python
prompt = "Clase de ciencias sobre el sistema solar y los planetas: Mercurio, Venus, Tierra, Marte, Júpiter, Saturno, Urano, Neptuno."

response = requests.post(
    f"{ENGINE}/transcribe",
    files={"audio": ("question.wav", audio_bytes, "audio/wav")},
    data={"initial_prompt": prompt},
    timeout=30,
)
```

Keep prompts short (< 200 chars). Whisper has a 224-token prompt limit and longer prompts get truncated silently.

In a chat-with-voice loop, set the prompt from the **LLM's previous answer** — that gets the next user utterance's named entities biased toward what's actually in conversation.

## Pattern D — JSON + base64 transport

Multipart not playing well with your HTTP client? Send base64-encoded audio in a JSON body:

```python
import base64
import requests

audio_bytes = open("clip.wav", "rb").read()

response = requests.post(
    f"{ENGINE}/transcribe",
    json={
        "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
        "mime": "audio/wav",
        "model": "rhasspy/faster-whisper-tiny-int8",
        "language": "es",
    },
    timeout=30,
)
```

Trade-off: base64 inflates payload size by ~33 %. For a 25 MiB WAV you spend 33 MiB on the wire. Not a problem on a LAN, costs you bandwidth on cellular.

## Pattern E — stdlib only (`urllib`)

For zero-dep environments (minimal Pi image, embedded distro):

```python
import json
import urllib.request

def transcribe_stdlib(engine: str, audio_bytes: bytes, mime: str = "audio/wav") -> dict:
    boundary = "----StdlibSttBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="clip"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        f"{engine}/transcribe",
        data=body,
        headers={"Content-Type": f'multipart/form-data; boundary="{boundary}"'},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())

result = transcribe_stdlib("https://ai.stt.thotenn.com", open("clip.wav", "rb").read())
print(result["text"])
```

## Pattern F — Reusable `SttClient`

Drop-in client class with connection reuse, sane defaults, and error mapping. Use this on the Pi.

```python
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class TranscriptResult:
    text: str
    language: str
    duration_seconds: float
    decode_seconds: float
    rtf: float
    model: str
    segments: list[dict[str, Any]]


class SttError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class SttClient:
    def __init__(self, engine_url: str, default_timeout: float = 30.0) -> None:
        self.engine_url = engine_url.rstrip("/")
        self.default_timeout = default_timeout
        self._session = requests.Session()

    def health(self) -> dict[str, Any]:
        response = self._session.get(f"{self.engine_url}/health", timeout=5)
        response.raise_for_status()
        return response.json()

    def models(self) -> dict[str, Any]:
        response = self._session.get(f"{self.engine_url}/models", timeout=5)
        response.raise_for_status()
        return response.json()

    def transcribe_bytes(
        self,
        audio: bytes,
        *,
        mime: str = "audio/wav",
        model: str | None = None,
        language: str | None = None,
        initial_prompt: str | None = None,
        timeout: float | None = None,
    ) -> TranscriptResult:
        files = {"audio": ("clip", audio, mime)}
        data: dict[str, str] = {}
        if model:
            data["model"] = model
        if language:
            data["language"] = language
        if initial_prompt:
            data["initial_prompt"] = initial_prompt

        response = self._session.post(
            f"{self.engine_url}/transcribe",
            files=files,
            data=data,
            timeout=timeout if timeout is not None else self.default_timeout,
        )
        return self._parse_response(response)

    def transcribe_file(self, path: str | Path, **kwargs) -> TranscriptResult:
        path = Path(path)
        mime = _guess_mime(path)
        return self.transcribe_bytes(path.read_bytes(), mime=mime, **kwargs)

    def transcribe_base64(
        self,
        audio: bytes,
        *,
        mime: str = "audio/wav",
        **kwargs,
    ) -> TranscriptResult:
        payload = {
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "mime": mime,
            **{k: v for k, v in kwargs.items() if v is not None and k != "timeout"},
        }
        response = self._session.post(
            f"{self.engine_url}/transcribe",
            json=payload,
            timeout=kwargs.get("timeout") or self.default_timeout,
        )
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: requests.Response) -> TranscriptResult:
        try:
            body = response.json()
        except ValueError:
            body = {"error": response.text or f"non-JSON HTTP {response.status_code}"}

        if not response.ok:
            raise SttError(response.status_code, body.get("error", "unknown error"))

        return TranscriptResult(
            text=body["text"],
            language=body["language"],
            duration_seconds=body["duration_seconds"],
            decode_seconds=body["decode_seconds"],
            rtf=body["rtf"],
            model=body["model"],
            segments=body.get("segments", []),
        )

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "SttClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return {
        "wav": "audio/wav",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "opus": "audio/opus",
        "mp3": "audio/mpeg",
        "mp4": "audio/mp4",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "flac": "audio/flac",
    }.get(suffix, "application/octet-stream")
```

Usage:

```python
with SttClient("https://ai.stt.thotenn.com") as stt:
    result = stt.transcribe_file("recording.wav", model="rhasspy/faster-whisper-tiny-int8")
    print(result.text)
```

The `Session` reuses the underlying TCP connection across requests — meaningful when you transcribe many short clips in a row.

## Error handling

`stt-sandbox` always responds with `{"error": "..."}` on 4xx/5xx. The `SttClient` above wraps that into a typed `SttError(status, message)`. Common cases:

| Status | When | What to do |
|---|---|---|
| 400 | empty body, missing `audio` field, garbage WAV header, unsupported MIME, body over `STT_MAX_REQUEST_BODY_BYTES` | Fix the request. These are client bugs. |
| 404 | hitting `/transcribe/stream` (cut from v1) or any unknown route | Use `/transcribe`. |
| 413 | body exceeds `STT_MAX_REQUEST_BODY_BYTES` | Split or compress audio (Opus → 8× smaller than PCM for the same content). |
| 415 | missing Content-Type | Always set it. |
| 500 | ffmpeg decode failed mid-pipeline, or the Whisper backend raised | Retriable. Bump timeout if you see intermittent 500s under load. |

## Timeouts

- **Connect timeout**: 5 s is plenty for a healthy engine.
- **Read timeout**: default to 30 s. A 5 s clip on `tiny-int8` decodes in ~0.5 s; a 60 s clip on `small-int8` could go to ~15 s. Pad generously — the request reads the response only after decode finishes.
- **Cold start**: the *very first* request after a fresh container boot blocks until the model downloads from HuggingFace (~10 s for `tiny-int8`, ~30 s for `small-int8`). After that, warm requests are fast. If your Pi script restarts often, hit `/health` once at startup to trigger preload before the user speaks.

## Picking the right pattern

| Situation | Pattern |
|---|---|
| Quick script, you have a WAV on disk | A |
| You want a specific model or biased prompt | B + C |
| Recording in memory (mic buffer, in-RAM tempfile) | A or F with `transcribe_bytes` |
| Constrained transport that hates multipart | D |
| Embedded Python, can't `pip install` | E |
| Production RPi with many requests | **F** (`SttClient`) |
