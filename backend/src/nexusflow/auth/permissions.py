"""Política de autorización: roles → permisos.

Los endpoints exigen *permisos*, no roles. Así, cambiar qué puede hacer un rol es editar
esta tabla, no buscar comprobaciones dispersas por el código. La matriz incluye los
permisos de fases futuras porque es la política aprobada; cada fase los empieza a usar.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Permission(StrEnum):
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    AUDIT_READ = "audit:read"
    WORKFLOWS_READ = "workflows:read"
    WORKFLOWS_WRITE = "workflows:write"
    WORKFLOWS_EXECUTE = "workflows:execute"
    RUNS_READ = "runs:read"
    CREDENTIALS_MANAGE = "credentials:manage"
    WEBHOOKS_MANAGE = "webhooks:manage"
    SCHEDULES_MANAGE = "schedules:manage"
    METRICS_READ = "metrics:read"


_VIEWER = frozenset({Permission.WORKFLOWS_READ, Permission.RUNS_READ, Permission.METRICS_READ})
_OPERATOR = _VIEWER | {Permission.WORKFLOWS_EXECUTE}
_EDITOR = _OPERATOR | {
    Permission.WORKFLOWS_WRITE,
    Permission.CREDENTIALS_MANAGE,
    Permission.WEBHOOKS_MANAGE,
    Permission.SCHEDULES_MANAGE,
}
_ADMIN = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.OPERATOR: frozenset(_OPERATOR),
    Role.EDITOR: frozenset(_EDITOR),
    Role.ADMIN: _ADMIN,
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
