"""Local voice-note transcription with faster-whisper (CTranslate2) - runs offline on the CPU.

WhatsApp voice notes are Ogg/Opus; faster-whisper decodes them itself via PyAV, so no ffmpeg
install is needed. The model (~460 MB for "small") is downloaded once into `model_dir` on first
use, then loaded once per process.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path

log = logging.getLogger("wabot.stt")


class Transcriber:
    def __init__(self, model: str, model_dir: Path, device: str = "cpu", compute_type: str = "int8",
                 language: str | None = None):
        self.model_name = model
        self.model_dir = model_dir
        self.device = device
        self.compute_type = compute_type
        self.language = language or None
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self):
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # harmless on Windows without dev mode
        from faster_whisper import WhisperModel  # heavy import: only when needed
        log.info("loading Whisper model %r (first use downloads it to %s)", self.model_name, self.model_dir)
        return WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type,
                            download_root=str(self.model_dir))

    def _run(self, audio: bytes) -> str:
        segments, info = self._model.transcribe(io.BytesIO(audio), language=self.language, beam_size=5,
                                                vad_filter=True)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info("transcribed %.1fs of audio (language=%s): %r", info.duration, info.language, text[:80])
        return text

    async def warm_up(self) -> None:
        """Load (and on first run download) the model in the background at startup."""
        try:
            async with self._lock:
                if self._model is None:
                    self._model = await asyncio.to_thread(self._load)
            log.info("Whisper model ready")
        except Exception:  # noqa: BLE001 - voice notes will retry the load and fail visibly
            log.exception("could not load the Whisper model")

    async def transcribe(self, audio: bytes) -> str:
        async with self._lock:  # one transcription at a time; the model is not cheap to run in parallel
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
            return await asyncio.to_thread(self._run, audio)
