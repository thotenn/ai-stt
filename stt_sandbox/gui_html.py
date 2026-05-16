from __future__ import annotations

import json


INDEX_HTML = """\
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>STT Sandbox</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111827; color: #f9fafb; }
    main { width: min(760px, calc(100vw - 32px)); padding: 28px; border: 1px solid #374151; border-radius: 20px; background: #1f2937; box-shadow: 0 24px 80px #0008; }
    h1 { margin: 0 0 8px; font-size: 28px; }
    p { color: #cbd5e1; margin: 0 0 16px; }
    select, button { font: inherit; }
    .row { display: flex; gap: 12px; align-items: center; margin-top: 14px; flex-wrap: wrap; }
    select { padding: 10px 12px; border-radius: 999px; border: 1px solid #4b5563; background: #111827; color: #f9fafb; }
    button { border: 0; border-radius: 999px; padding: 11px 18px; background: #f97316; color: #111827; font-weight: 800; cursor: pointer; transition: background .2s; }
    button:disabled { opacity: .55; cursor: wait; }
    button.recording { background: #dc2626; color: #f9fafb; }
    #status { margin-left: auto; color: #93c5fd; min-width: 12ch; text-align: right; }
    audio { width: 100%; margin-top: 18px; }
    .transcript { margin-top: 18px; padding: 16px; border-radius: 14px; border: 1px solid #4b5563; background: #0f172a; min-height: 80px; white-space: pre-wrap; color: #f1f5f9; font-size: 17px; line-height: 1.45; }
    .meta { margin-top: 10px; font-size: 13px; color: #94a3b8; }
    .meta span { margin-right: 14px; }
    kbd { border: 1px solid #64748b; border-bottom-width: 3px; padding: 1px 6px; border-radius: 6px; font-size: 12px; }
    .error { color: #fca5a5; }
  </style>
</head>
<body>
  <main>
    <h1>STT Sandbox</h1>
    <p>Presiona <kbd>Espacio</kbd> o el botón para grabar. Vuelve a presionar para enviar y transcribir.</p>
    <div class="row">
      <label for="model">Modelo</label>
      <select id="model"></select>
      <button id="record">Grabar</button>
      <span id="status">Listo</span>
    </div>
    <div id="transcript" class="transcript">El texto aparece aquí.</div>
    <div id="meta" class="meta"></div>
    <audio id="audio" controls></audio>
  </main>
  <script>
    const engineUrl = __ENGINE_URL_JSON__;
    const apiUrl = (path) => `${engineUrl || ''}${path}`;

    const modelSelect = document.querySelector('#model');
    const recordButton = document.querySelector('#record');
    const statusEl = document.querySelector('#status');
    const transcriptEl = document.querySelector('#transcript');
    const metaEl = document.querySelector('#meta');
    const audioEl = document.querySelector('#audio');

    let mediaRecorder = null;
    let recordedChunks = [];
    let recordingStart = 0;
    let timerHandle = null;
    let recording = false;
    let mimeInUse = '';

    function pickMimeType() {
      const candidates = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4',
      ];
      for (const candidate of candidates) {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported(candidate)) {
          return candidate;
        }
      }
      return '';
    }

    function formatElapsed(ms) {
      const total = Math.floor(ms / 1000);
      const m = String(Math.floor(total / 60)).padStart(1, '0');
      const s = String(total % 60).padStart(2, '0');
      return `${m}:${s}`;
    }

    async function loadModels() {
      try {
        const response = await fetch(apiUrl('/models'));
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        for (const item of data.models) {
          const option = document.createElement('option');
          option.value = item.name;
          option.textContent = `${item.name} (${item.size}, ${item.quantization})`;
          modelSelect.appendChild(option);
        }
        modelSelect.value = data.default;
      } catch (err) {
        statusEl.textContent = 'Error /models';
        statusEl.classList.add('error');
        console.error(err);
      }
    }

    async function startRecording() {
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        statusEl.textContent = 'Sin micrófono';
        statusEl.classList.add('error');
        return;
      }
      const mime = pickMimeType();
      if (!mime) {
        statusEl.textContent = 'MIME no soportado';
        statusEl.classList.add('error');
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, sampleRate: 16000, echoCancellation: true, noiseSuppression: true },
        });
        mediaRecorder = new MediaRecorder(stream, { mimeType: mime });
        mimeInUse = mime;
        recordedChunks = [];
        mediaRecorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) recordedChunks.push(event.data);
        };
        mediaRecorder.onstop = () => {
          stream.getTracks().forEach((track) => track.stop());
          handleRecordingComplete();
        };
        mediaRecorder.start();
        recordingStart = performance.now();
        recording = true;
        recordButton.textContent = 'Detener';
        recordButton.classList.add('recording');
        statusEl.classList.remove('error');
        statusEl.textContent = 'Grabando 0:00';
        timerHandle = setInterval(() => {
          statusEl.textContent = `Grabando ${formatElapsed(performance.now() - recordingStart)}`;
        }, 250);
      } catch (err) {
        console.error(err);
        statusEl.textContent = 'Permiso denegado';
        statusEl.classList.add('error');
      }
    }

    function stopRecording() {
      if (!mediaRecorder) return;
      clearInterval(timerHandle);
      timerHandle = null;
      recording = false;
      recordButton.textContent = 'Grabar';
      recordButton.classList.remove('recording');
      mediaRecorder.stop();
      statusEl.textContent = 'Transcribiendo...';
    }

    async function handleRecordingComplete() {
      const blob = new Blob(recordedChunks, { type: mimeInUse });
      audioEl.src = URL.createObjectURL(blob);

      recordButton.disabled = true;
      try {
        const form = new FormData();
        form.append('audio', blob, 'recording.webm');
        form.append('model', modelSelect.value);

        const response = await fetch(apiUrl('/transcribe'), { method: 'POST', body: form });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data && data.error ? data.error : `HTTP ${response.status}`);
        }
        transcriptEl.textContent = data.text || '(transcripción vacía)';
        metaEl.innerHTML = '';
        const add = (label, value) => {
          const span = document.createElement('span');
          span.textContent = `${label}: ${value}`;
          metaEl.appendChild(span);
        };
        add('idioma', data.language || '—');
        add('duración', `${data.duration_seconds}s`);
        add('decode', `${data.decode_seconds}s`);
        add('rtf', data.rtf);
        add('modelo', data.model || '—');
        statusEl.textContent = 'Listo';
        statusEl.classList.remove('error');
      } catch (err) {
        console.error(err);
        transcriptEl.textContent = '';
        metaEl.textContent = '';
        statusEl.textContent = 'Error';
        statusEl.classList.add('error');
        alert(String(err.message || err));
      } finally {
        recordButton.disabled = false;
      }
    }

    function toggleRecording() {
      if (recording) {
        stopRecording();
      } else {
        startRecording();
      }
    }

    recordButton.addEventListener('click', toggleRecording);
    window.addEventListener('keydown', (event) => {
      if (event.code === 'Space' && event.target === document.body) {
        event.preventDefault();
        toggleRecording();
      }
    });

    loadModels();
  </script>
</body>
</html>
"""


def render_index(engine_url: str) -> str:
    safe = json.dumps(engine_url).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return INDEX_HTML.replace("__ENGINE_URL_JSON__", safe)
