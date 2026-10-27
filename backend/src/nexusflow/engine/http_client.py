"""Cliente HTTP saliente con protección anti-SSRF y clasificación de fallos.

Los editores escriben URLs arbitrarias en los flujos, y el worker las llama desde dentro de
la red. Sin control, alguien podría leer servicios internos (Redis, PostgreSQL, el endpoint
de metadatos cloud 169.254.169.254). Medidas:

1. Solo https (http únicamente si se habilita para desarrollo).
2. Sin credenciales en la URL (`user:pass@host`).
3. Se resuelve el DNS y se rechaza si *alguna* IP no es pública.
4. Se conecta a la IP ya validada (no se vuelve a resolver): evita el *DNS rebinding*, en
   el que el dominio resuelve a una IP pública al validar y a una privada al conectar.
   El nombre original se envía en `Host` y en el SNI de TLS, así el certificado se sigue
   verificando contra el dominio.
5. No se siguen redirecciones: un 3xx podría apuntar a una IP interna.
6. Tamaño máximo de respuesta, leída en streaming.

Clasificación de fallos (decide si un reintento es seguro):
- NOT_SENT: la petición no llegó a enviarse (DNS, conexión rechazada). Siempre reintentable.
- REJECTED: el servidor la rechazó sin procesarla (408, 429, 503). Siempre reintentable.
- UNKNOWN: pudo procesarse (timeout de lectura, conexión cortada, 5xx). Solo se reintenta
  si el paso es idempotente.
- PERMANENT: reintentar no cambiará nada (4xx, URL bloqueada, respuesta demasiado grande).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any

import httpx

from nexusflow.db.types import utcnow

# Cabeceras de respuesta que se guardan en el historial (el resto se descarta).
_KEPT_RESPONSE_HEADERS = frozenset(
    {"content-type", "location", "etag", "retry-after", "x-request-id", "idempotency-key"}
)


class FailureKind(StrEnum):
    NOT_SENT = "not_sent"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    PERMANENT = "permanent"


@dataclass
class OutboundHttpError(Exception):
    kind: FailureKind
    error_class: str
    message: str
    http_status: int | None = None
    retry_after_seconds: float | None = None

    def __str__(self) -> str:
        return f"{self.error_class}: {self.message}"


@dataclass(frozen=True, slots=True)
class HttpResult:
    status_code: int
    headers: dict[str, str]
    body: Any


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    allow_http: bool = False
    # Hosts que pueden resolver a IPs privadas (p. ej. el mock-partner en desarrollo).
    allowed_private_hosts: frozenset[str] = field(default_factory=frozenset)
    max_response_bytes: int = 262_144


def _ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 es 127.0.0.1
    return ip.is_global and not ip.is_multicast


async def resolve_destination(
    url: httpx.URL, policy: EgressPolicy
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if url.scheme not in ("https", "http") or (url.scheme == "http" and not policy.allow_http):
        raise OutboundHttpError(
            FailureKind.PERMANENT, "egress_blocked", "Solo se permiten URLs https"
        )
    if url.userinfo:
        raise OutboundHttpError(
            FailureKind.PERMANENT, "egress_blocked", "La URL no puede contener credenciales"
        )
    host = url.host
    if not host:
        raise OutboundHttpError(FailureKind.PERMANENT, "invalid_url", "La URL no tiene host")
    trusted = host.lower() in policy.allowed_private_hosts

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        candidates = [literal]
    else:
        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except (socket.gaierror, UnicodeError) as exc:
            raise OutboundHttpError(
                FailureKind.NOT_SENT, "dns_error", f"No se pudo resolver {host}"
            ) from exc
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]
        if not candidates:
            raise OutboundHttpError(
                FailureKind.NOT_SENT, "dns_error", f"{host} no tiene direcciones"
            )

    if not trusted and not all(_ip_allowed(ip) for ip in candidates):
        raise OutboundHttpError(
            FailureKind.PERMANENT,
            "egress_blocked",
            f"{host} resuelve a una dirección no pública; destino bloqueado",
        )
    return candidates[0]


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - utcnow()).total_seconds())


def _decode_body(raw: bytes, content_type: str) -> Any:
    if not raw:
        return None
    if "json" in content_type:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw.decode("utf-8", errors="replace")


async def send_request(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    json_body: Any,
    timeout_seconds: float,
    policy: EgressPolicy,
    content: bytes | None = None,
) -> HttpResult:
    """Envía la petición. `content` (bytes exactos) tiene prioridad sobre `json_body`:
    se usa cuando hay que firmar el cuerpo tal cual viaja."""
    try:
        target = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as exc:
        raise OutboundHttpError(
            FailureKind.PERMANENT, "invalid_url", "La URL no es válida"
        ) from exc

    async def _do() -> HttpResult:
        ip = await resolve_destination(target, policy)
        ip_host = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
        pinned = target.copy_with(host=ip_host)
        request_headers = {**headers, "Host": target.netloc.decode("ascii")}
        extensions: dict[str, Any] = {}
        if target.scheme == "https":
            extensions["sni_hostname"] = target.host
        request = client.build_request(
            method,
            pinned,
            headers=request_headers,
            params=params or None,
            json=json_body
            if content is None and method != "GET" and json_body is not None
            else None,
            content=content,
            extensions=extensions,
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0)),
        )
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > policy.max_response_bytes:
                    raise OutboundHttpError(
                        FailureKind.PERMANENT,
                        "response_too_large",
                        f"La respuesta supera {policy.max_response_bytes} bytes",
                        http_status=response.status_code,
                    )
                chunks.append(chunk)
        finally:
            await response.aclose()
        kept = {k: v for k, v in response.headers.items() if k.lower() in _KEPT_RESPONSE_HEADERS}
        return HttpResult(
            status_code=response.status_code,
            headers=kept,
            body=_decode_body(b"".join(chunks), response.headers.get("content-type", "")),
        )

    try:
        # Límite global: también acota la resolución DNS, que httpx no controla aquí.
        return await asyncio.wait_for(_do(), timeout=timeout_seconds + 1)
    except OutboundHttpError:
        raise
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
        raise OutboundHttpError(
            FailureKind.NOT_SENT, "connection_error", str(exc) or type(exc).__name__
        ) from exc
    except (httpx.ReadTimeout, httpx.WriteTimeout, TimeoutError) as exc:
        raise OutboundHttpError(
            FailureKind.UNKNOWN, "timeout", "Sin respuesta dentro del tiempo límite"
        ) from exc
    except (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as exc:
        raise OutboundHttpError(
            FailureKind.UNKNOWN, "connection_lost", "La conexión se cortó durante la petición"
        ) from exc
    except (httpx.UnsupportedProtocol, httpx.InvalidURL, httpx.LocalProtocolError) as exc:
        raise OutboundHttpError(FailureKind.PERMANENT, "invalid_request", str(exc)) from exc


def classify_status(status_code: int, success_status: list[int] | None) -> FailureKind | None:
    """None si es éxito; si no, el tipo de fallo que representa el código."""
    if success_status is not None:
        if status_code in success_status:
            return None
    elif 200 <= status_code < 300:
        return None
    if status_code in (408, 429, 503):
        return FailureKind.REJECTED
    if status_code >= 500:
        return FailureKind.UNKNOWN
    return FailureKind.PERMANENT
