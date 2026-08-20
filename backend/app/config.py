from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    max_upload_bytes: int = int(os.getenv("AUTOREF_MAX_UPLOAD_BYTES", str(30 * 1024 * 1024)))
    job_root: Path = Path(os.getenv("AUTOREF_JOB_ROOT", "data/jobs"))
    job_ttl_hours: int = int(os.getenv("AUTOREF_JOB_TTL_HOURS", "24"))
    grobid_url: str | None = os.getenv("AUTOREF_GROBID_URL") or None
    zotero_api_url: str = os.getenv("AUTOREF_ZOTERO_API_URL", "https://api.zotero.org")
    credential_ttl_minutes: int = int(os.getenv("AUTOREF_CREDENTIAL_TTL_MINUTES", "30"))
    credential_key: str | None = os.getenv("AUTOREF_CREDENTIAL_KEY") or None
    cors_origins: tuple[str, ...] = tuple(
        part.strip()
        for part in os.getenv("AUTOREF_CORS_ORIGINS", "http://localhost:5173").split(",")
        if part.strip()
    )


settings = Settings()
