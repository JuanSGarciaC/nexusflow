"""Hash de contraseñas con Argon2id.

Argon2id ganó la Password Hashing Competition y es la primera recomendación de OWASP.
Se usan los parámetros por defecto de argon2-cffi (perfil RFC 9106 de baja memoria).
`needs_rehash` permite endurecer los parámetros más adelante: los hashes antiguos se
actualizan de forma transparente en el siguiente login correcto.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Hash de una contraseña aleatoria. Si el email no existe, se verifica contra él para que
# la respuesta tarde lo mismo que con un email real (evita enumerar cuentas por tiempo).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def burn_verification_time(password: str) -> None:
    """Consume el mismo tiempo que una verificación real, sin resultado útil."""
    verify_password(_DUMMY_HASH, password)
