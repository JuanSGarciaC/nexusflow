"""Cifrado de secretos de integraciones.

- Fernet: AES-128-CBC + HMAC-SHA256 (cifrado autenticado). Un texto cifrado manipulado no
  se descifra; falla.
- MultiFernet: la primera clave cifra y todas descifran. Rotar = añadir una clave nueva
  al principio, ejecutar `nexusflow rotate-credentials` y retirar la antigua.
- Vinculación: el id y el nombre de la credencial van *dentro* del texto cifrado y se
  verifican al descifrar. Fernet no admite datos asociados (AAD); sin esto, alguien con
  acceso de escritura a la tabla podría copiar el secreto de una fila a otra.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CredentialDecryptionError(RuntimeError):
    pass


def _keys(raw: str) -> list[bytes]:
    return [k.strip().encode() for k in raw.split(",") if k.strip()]


def key_fingerprint(key: bytes) -> str:
    """Identificador no secreto de una clave (para saber con cuál se cifró cada fila)."""
    return hashlib.sha256(base64.urlsafe_b64decode(key)).hexdigest()[:12]


def generate_key() -> str:
    return Fernet.generate_key().decode()


class CredentialCipher:
    def __init__(self, raw_keys: str) -> None:
        keys = _keys(raw_keys)
        self._fernet = MultiFernet([Fernet(k) for k in keys])
        self.primary_fingerprint = key_fingerprint(keys[0])

    def encrypt(self, credential_id: uuid.UUID, name: str, secret: dict[str, Any]) -> str:
        payload = {"id": str(credential_id), "name": name, "secret": secret}
        return self._fernet.encrypt(json.dumps(payload).encode()).decode()

    def decrypt(self, credential_id: uuid.UUID, name: str, token: str) -> dict[str, Any]:
        try:
            payload = json.loads(self._fernet.decrypt(token.encode()))
        except (InvalidToken, ValueError) as exc:
            raise CredentialDecryptionError("No se pudo descifrar la credencial") from exc
        if payload.get("id") != str(credential_id) or payload.get("name") != name:
            raise CredentialDecryptionError("El secreto no pertenece a esta credencial")
        secret: dict[str, Any] = payload["secret"]
        return secret

    def rotate(self, token: str) -> str:
        """Vuelve a cifrar con la clave principal (sin conocer el contenido)."""
        return self._fernet.rotate(token.encode()).decode()
