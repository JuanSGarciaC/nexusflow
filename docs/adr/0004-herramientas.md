# ADR-0004: uv, Ruff y mypy strict

- **Estado:** Aceptada e implementada (Fase 1)
- **Fecha:** 2026-10-27

## Decisión

- **uv** gestiona Python y las dependencias. `uv.lock` es multiplataforma (Windows, Linux,
  macOS) e incluye hashes, y CI usa `uv sync --locked`, que falla si el lockfile no
  coincide con `pyproject.toml`.
- **Ruff** se encarga del lint y del formato, con reglas `S` (Bandit) para seguridad y
  `ASYNC` para errores típicos de asyncio.
- **mypy `strict`** con el plugin de Pydantic.
- **Versiones fijadas tal como las resolvió PyPI** en el momento de la implementación;
  las Actions de GitHub se fijan por SHA de commit para evitar ataques a la cadena de
  suministro mediante etiquetas movidas.

## Consecuencias

- Instalación reproducible con un solo comando (`uv sync`).
- Más estricto al escribir código, pero los errores de tipos aparecen en CI y no en
  producción.

## Nota de implementación

Starlette 1.x construye su `TestClient` sobre `httpx2` y marca `httpx` como obsoleto para
ese uso. Se detectó al ejecutar las pruebas (la configuración convierte los avisos en
errores) y se cambió la dependencia de desarrollo a `httpx2`, el paquete que los propios
metadatos de Starlette referencian.
