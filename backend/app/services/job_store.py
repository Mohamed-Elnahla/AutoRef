from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from backend.app.config import settings

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class JobStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.job_root
        self.root.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        cutoff = time.time() - settings.job_ttl_hours * 3600
        for path in self.root.iterdir():
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)

    def create(self, source_name: str, data: bytes) -> str:
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir(mode=0o700)
        (directory / "source.docx").write_bytes(data)
        self.write_json(job_id, "metadata.json", {"source_name": source_name, "created_at": time.time()})
        return job_id

    def directory(self, job_id: str) -> Path:
        if not JOB_ID_RE.fullmatch(job_id):
            raise FileNotFoundError(job_id)
        directory = self.root / job_id
        if not directory.is_dir():
            raise FileNotFoundError(job_id)
        return directory

    def source(self, job_id: str) -> bytes:
        return (self.directory(job_id) / "source.docx").read_bytes()

    def read_json(self, job_id: str, name: str) -> dict:
        return json.loads((self.directory(job_id) / name).read_text(encoding="utf-8"))

    def write_json(self, job_id: str, name: str, payload: dict) -> Path:
        destination = self.directory(job_id) / name
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return destination

    def artifact(self, job_id: str, name: str) -> Path:
        allowed = {"document", "library", "report"}
        if name not in allowed:
            raise FileNotFoundError(name)
        meta = self.read_json(job_id, "artifacts.json")
        path = self.directory(job_id) / meta[name]
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

