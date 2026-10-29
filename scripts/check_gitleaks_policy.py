"""Comprueba que la política de gitleaks (.gitleaks.toml) sigue siendo la acordada.

Dos garantías, ambas ejecutadas en el job «Escaneo de secretos» del CI:

1. Estática: la lista de rutas y de valores permitidos es EXACTAMENTE la esperada. Ampliar
   la excepción (otra ruta, o una regex más laxa) obliga a editar este archivo y
   docs/security-review.md en el mismo PR, donde un revisor lo verá.
2. Dinámica (canarios): se ejecuta gitleaks con esa misma configuración sobre un árbol
   temporal. Un secreto con formato real FUERA de backend/tests y frontend/src/test debe
   detectarse; el mismo secreto dentro de esas rutas y los tres valores de ejemplo no.

El secreto canario se construye en tiempo de ejecución para que este archivo no contenga
ningún literal con forma de credencial.

Uso: python3 scripts/check_gitleaks_policy.py   (necesita gitleaks en PATH o GITLEAKS_BIN;
sin ninguno de los dos usa la imagen Docker fijada en GITLEAKS_IMAGE).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.24.3"

EXPECTED_PATHS = {"^backend/tests/", "^frontend/src/test/"}
EXPECTED_REGEXES = {"change-me-[a-z0-9-]+", "demo-clave-larga-2026", "ci-only-password"}


def check_static() -> list[str]:
    allowlist = tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("allowlist", {})
    errors = []
    for key, expected in (("paths", EXPECTED_PATHS), ("regexes", EXPECTED_REGEXES)):
        actual = set(allowlist.get(key, []))
        if actual != expected:
            errors.append(
                f"allowlist.{key} cambió: sobran {sorted(actual - expected)}, "
                f"faltan {sorted(expected - actual)}. Actualiza este script y "
                "docs/security-review.md si el cambio es intencionado."
            )
    return errors


def gitleaks_command(source: Path, config: Path) -> list[str]:
    # Modo git, como en el CI: las rutas son relativas a la raíz del repo (con --no-git
    # llevarían el prefijo del directorio y `^backend/tests/` no coincidiría).
    args = ["detect", "--no-banner", "--redact", "--exit-code", "1"]
    binary = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
    if binary:
        return [binary, *args, "-s", str(source), "-c", str(config)]
    return [
        "docker", "run", "--rm",
        "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
        "-e", "GIT_CONFIG_VALUE_0=*",
        "-v", f"{source}:/scan", "-v", f"{config}:/cfg.toml:ro",
        GITLEAKS_IMAGE, *args, "-s", "/scan", "-c", "/cfg.toml",
    ]  # fmt: skip


def scan(files: dict[str, str]) -> int:
    """Devuelve el código de salida de gitleaks sobre un árbol con esos archivos."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git = ["git", "-C", str(source), "-c", "user.name=canary", "-c", "user.email=c@example.com"]
        for step in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "canary"]):
            subprocess.run([*git, *step], check=True, capture_output=True)  # noqa: S603
        result = subprocess.run(  # noqa: S603
            gitleaks_command(source, CONFIG), capture_output=True, text=True, check=False
        )
        if result.returncode not in (0, 1):
            sys.exit(f"gitleaks falló al ejecutarse:\n{result.stdout}\n{result.stderr}")
        return result.returncode


def check_canaries() -> list[str]:
    # Token con el formato de un PAT de GitHub (ghp_ + 36 alfanuméricos), generado al vuelo.
    token = "ghp_" + hashlib.sha256(b"nexusflow-canary").hexdigest()[:36]
    line = f'TOKEN = "{token}"\n'
    cases = [
        ("secreto real en el código de la app", {"backend/src/canary.py": line}, 1),
        ("secreto real en la documentación", {"docs/canary.md": f"{token}\n"}, 1),
        ("secreto ficticio en backend/tests", {"backend/tests/canary.py": line}, 0),
        ("secreto ficticio en frontend/src/test", {"frontend/src/test/canary.ts": line}, 0),
        (
            "los tres valores de ejemplo públicos",
            {
                ".env.example": 'JWT_SECRET="change-me-jwt-secret"\nPASSWORD="demo-clave-larga-2026"\n'
                'DB_PASSWORD="ci-only-password"\n'
            },
            0,
        ),
    ]
    errors = []
    for label, files, expected in cases:
        code = scan(files)
        status = "ok" if code == expected else "FALLA"
        print(f"  [{status}] {label}: gitleaks salió con {code} (esperado {expected})")
        if code != expected:
            errors.append(f"canario «{label}»: salida {code}, esperada {expected}")
    return errors


def main() -> int:
    errors = check_static()
    print("Política estática:", "ok" if not errors else "FALLA")
    print("Canarios de gitleaks:")
    errors += check_canaries()
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
