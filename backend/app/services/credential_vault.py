from __future__ import annotations

import base64
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken

from backend.app.config import settings


class CredentialVault:
    """Short-lived encrypted API keys. Plaintext keys are never persisted or returned."""

    def __init__(self) -> None:
        configured = settings.credential_key
        key = configured.encode() if configured else Fernet.generate_key()
        try:
            self._fernet = Fernet(key)
        except (ValueError, base64.binascii.Error) as exc:
            raise RuntimeError("AUTOREF_CREDENTIAL_KEY must be a valid Fernet key") from exc
        self._entries: dict[str, tuple[bytes, float]] = {}

    def put(self, secret: str) -> str:
        self.cleanup()
        connection_id = uuid.uuid4().hex
        self._entries[connection_id] = (self._fernet.encrypt(secret.encode()), time.time())
        return connection_id

    def get(self, connection_id: str) -> str:
        self.cleanup()
        entry = self._entries.get(connection_id)
        if entry is None:
            raise KeyError(connection_id)
        token, _ = entry
        self._entries[connection_id] = (token, time.time())
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken as exc:
            raise KeyError(connection_id) from exc

    def remove(self, connection_id: str) -> None:
        self._entries.pop(connection_id, None)

    def cleanup(self) -> None:
        cutoff = time.time() - settings.credential_ttl_minutes * 60
        for connection_id, (_, touched_at) in list(self._entries.items()):
            if touched_at < cutoff:
                self._entries.pop(connection_id, None)
