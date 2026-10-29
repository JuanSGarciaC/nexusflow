"""El dashboard de Grafana y las alertas solo pueden usar métricas y estados que existen.

Si alguien renombra una métrica o un estado en el código, el dashboard y las alertas dejarían
de funcionar sin que nada falle (una consulta sobre una serie inexistente devuelve vacío, no
error). Estas pruebas lo convierten en un fallo del CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from nexusflow.engine.models import RunStatus, StepStatus
from nexusflow.observability.prometheus import AppMetrics
from nexusflow.webhooks.models import DeliveryStatus

ASSETS = Path(__file__).resolve().parents[3] / "deploy" / "observability"
METRIC = re.compile(r"\bnexusflow_[a-z0-9_]+")
SELECTOR = re.compile(r'(nexusflow_[a-z0-9_]+)\{status=~?"([^"]+)"')


def _exposed_names() -> set[str]:
    names: set[str] = set()
    for family in AppMetrics().registry.collect():
        names.add(family.name)
        names.update(f"{family.name}{suffix}" for suffix in ("_total", "_bucket", "_count", "_sum"))
    return names


def _dashboard_exprs() -> list[str]:
    dashboard = json.loads((ASSETS / "grafana/dashboards/nexusflow.json").read_text("utf-8"))
    return [t["expr"] for panel in dashboard["panels"] for t in panel["targets"]]


def _alert_exprs() -> list[str]:
    # Sin dependencia de PyYAML: las expresiones están en bloques `expr:` (una línea o `|`).
    text = (ASSETS / "alerts.yml").read_text("utf-8")
    matches: list[tuple[str, str]] = re.findall(r"expr:\s*(?:\|\n((?:\s{10,}.*\n)+)|(.+))", text)
    return [block or line for block, line in matches]


def test_dashboard_and_alerts_use_only_metrics_the_api_exposes() -> None:
    exposed = _exposed_names()
    exprs = _dashboard_exprs() + _alert_exprs()
    assert exprs, "no se encontraron expresiones: ¿cambió el formato de los archivos?"
    used = {m for expr in exprs for m in METRIC.findall(expr)}
    assert used, "no se encontró ninguna métrica nexusflow_*"
    assert used <= exposed, f"métricas inexistentes en la API: {sorted(used - exposed)}"


def test_status_labels_used_in_queries_are_real_states() -> None:
    allowed = {
        "nexusflow_runs": {s.value for s in RunStatus},
        "nexusflow_steps_active": {s.value for s in StepStatus},
        "nexusflow_webhook_deliveries_open": {s.value for s in DeliveryStatus},
    }
    exprs = _dashboard_exprs() + _alert_exprs()
    checked = 0
    for expr in exprs:
        for metric, values in SELECTOR.findall(expr):
            if metric not in allowed:
                continue  # métricas HTTP: el estado es un código, no un enum
            checked += 1
            unknown = set(values.split("|")) - allowed[metric]
            assert not unknown, f"{metric}: estados inexistentes {sorted(unknown)}"
    assert checked >= 5, "se esperaban varios selectores por estado; ¿cambió el formato?"


def test_every_alert_has_severity_and_annotations() -> None:
    text = (ASSETS / "alerts.yml").read_text("utf-8")
    alerts = text.count("- alert:")
    assert alerts >= 9
    assert text.count("severity:") == alerts
    assert text.count("summary:") == alerts
    assert text.count("description:") == alerts
