from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    data_dir: Path
    session_db: Path
    ledger_db: Path
    allowlist_file: Path
    leads_dirs: list[Path]
    stt_model: str
    stt_model_dir: Path
    stt_device: str
    stt_compute_type: str
    stt_language: str | None
    max_replies_per_hour: int
    receipt_grace_s: float
    disconnect_exit_s: float
    pace: bool
    max_send_attempts: int

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        e = os.environ.get
        data = Path(e("WA_DATA_DIR", "data/wa"))
        return cls(
            data_dir=data,
            session_db=Path(e("WA_SESSION_DB", str(data / "session.sqlite3"))),
            ledger_db=Path(e("WA_LEDGER_DB", str(data / "ledger.sqlite3"))),
            allowlist_file=Path(e("ALLOWLIST_FILE", "allowlist.txt")),
            leads_dirs=[Path(p) for p in e("LEADS_DIRS", "benchmark;output").split(";") if p],
            stt_model=e("STT_MODEL", "small"),
            stt_model_dir=Path(e("STT_MODEL_DIR", "data/models")),
            stt_device=e("STT_DEVICE", "cpu"),
            stt_compute_type=e("STT_COMPUTE_TYPE", "int8"),
            stt_language=e("STT_LANGUAGE") or None,
            max_replies_per_hour=int(e("MAX_REPLIES_PER_HOUR", "30")),
            receipt_grace_s=float(e("RECEIPT_GRACE_S", "60")),
            disconnect_exit_s=float(e("DISCONNECT_EXIT_S", "90")),
            pace=e("HUMAN_PACING", "1") != "0",
            max_send_attempts=int(e("MAX_SEND_ATTEMPTS", "3")),
        )
