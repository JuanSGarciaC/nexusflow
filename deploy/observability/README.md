# Observabilidad de referencia: Prometheus, Alertmanager y Grafana

Un dashboard, nueve alertas y su notificación a Slack sobre las métricas que **ya expone la API** en `/metrics`
([ADR-0010](../../docs/adr/0010-observabilidad.md)). No añade código a la aplicación.

## Puesta en marcha

```bash
# 1. Token de métricas: la API solo sirve /metrics con él (sin token devuelve 404)
echo "NEXUSFLOW_METRICS_TOKEN=$(openssl rand -hex 24)" >> .env
echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)"   >> .env     # obligatoria, sin valor por defecto
mkdir -p deploy/observability/secrets && chmod 700 deploy/observability/secrets
grep ^NEXUSFLOW_METRICS_TOKEN= .env | cut -d= -f2- | tr -d '\n' > deploy/observability/secrets/metrics_token
# URL del webhook entrante de Slack (https://api.slack.com/messaging/webhooks); no se versiona
printf '%s' 'https://hooks.slack.com/services/XXX/YYY/ZZZ' > deploy/observability/secrets/slack_webhook_url

# 2. Levantar el stack con el override de observabilidad
docker compose -f docker-compose.yml -f deploy/docker-compose.observability.yml up -d
# (en producción añade también -f deploy/docker-compose.prod.yml)
```

Grafana queda en `http://127.0.0.1:3001` (usuario `admin`). **Solo escucha en localhost**:
desde fuera, usa un túnel SSH (`ssh -L 3001:127.0.0.1:3001 servidor`) o un proxy con TLS.
Prometheus y Alertmanager no publican ningún puerto. El dashboard y el datasource se aprovisionan solos y el
dashboard es de solo lectura en la interfaz (se edita en `grafana/dashboards/nexusflow.json`).

## Qué contiene

| Archivo | Para qué |
|---|---|
| `prometheus.yml` | Scrape de la API cada 15 s con el token, carga de las reglas y envío a Alertmanager |
| `alertmanager.yml` | Agrupa, inhibe y notifica a Slack (canal `#nexusflow-alertas`; cámbialo en el archivo) |
| `alerts.yml` | 9 reglas de alerta (tabla abajo) |
| `alerts_test.yml` | Tests unitarios de las reglas con `promtool` |
| `grafana/dashboards/nexusflow.json` | Dashboard «NexusFlow · visión general» (13 paneles) |
| `grafana/provisioning/` | Datasource y proveedor del dashboard |

### Alertas

| Alerta | Condición | Gravedad |
|---|---|---|
| `NexusFlowApiCaida` | La API no responde al scrape 2 min | critical |
| `NexusFlowErrores5xx` | > 5 % de respuestas 5xx durante 5 min (con tráfico > 0,1 req/s) | critical |
| `NexusFlowLatenciaAlta` | p95 > 1 s durante 10 min | warning |
| `NexusFlowColaSinVaciar` | Nunca hubo 0 ejecuciones pendientes en 15 min | critical |
| `NexusFlowColaCreciendo` | > 100 ejecuciones pendientes 10 min | warning |
| `NexusFlowEjecucionesFallando` | > 10 ejecuciones failed/timed_out nuevas en 15 min | warning |
| `NexusFlowRevisionManualPendiente` | Pasos en `needs_review` más de 1 h | warning |
| `NexusFlowWebhookDeadLetter` | Entregas en dead-letter 5 min | warning |
| `NexusFlowWebhookAcumulado` | > 100 entregas pendientes/reintentándose 10 min | warning |

Los umbrales son una estimación para una instalación pequeña: **ajústalos a tu tráfico**.

## Cómo se ha verificado

- `promtool check rules` y `promtool test rules` (8 casos: cuándo salta cada alerta y cuándo
  **no**, p. ej. 5xx con poco tráfico o una cola que a veces se vacía). Corre en el CI.
- Stack real con tráfico de k6: el scrape con token queda `up`, las 9 reglas se cargan sanas,
  las 18 consultas del dashboard se ejecutan contra Prometheus y devuelven series (las de
  3xx/4xx/5xx salen vacías hasta que ocurre uno, y los paneles lo muestran como 0), y una
  consulta **a través de Grafana** devuelve datos.
- **Alertmanager de extremo a extremo** con un receptor Slack falso: se paró la API y la
  alerta `NexusFlowApiCaida` llegó con su título, resumen y descripción; el primer intento de
  entrega falló (el receptor aún no existía) y Alertmanager **reintentó solo**; al volver la
  API llegó el aviso de **resuelta**. `amtool` valida la configuración y el enrutado en el CI.
- Un test del backend (`test_observability_assets.py`) falla en el CI si el código renombra
  una métrica o un estado que el dashboard o las alertas usan.

## Límites (léelos antes de fiarte de esto)

- **Contra Slack real no se ha probado**: la entrega se verificó contra un receptor falso con
  el mismo formato de petición. Falta confirmarlo con tu workspace (un `curl` al webhook basta).
- **Un solo canal y sin escalado**: todo va al mismo canal de Slack; no hay rotación de guardia
  ni escalado si nadie responde (PagerDuty u Opsgenie lo cubrirían).
- **La inhibición solo cubre «API caída»**: si cae Redis o el worker, no hay supresión.
- **Worker y beat no exportan métricas**: su caída se detecta de forma indirecta
  (`NexusFlowColaSinVaciar`). Una alerta directa pediría un exporter de Celery.
- **Sin métricas de Redis ni PostgreSQL** (CPU, conexiones, disco). Añade
  `redis_exporter` y `postgres_exporter` si los necesitas.
- **Gauges desde PostgreSQL**: `nexusflow_runs` cuenta cuántas ejecuciones hay en cada
  estado; «nuevas fallidas» se aproxima con `delta()`, que extrapola a los bordes de la
  ventana y bajaría si se borrara histórico.
- La captura de `docs/screenshots/10-grafana.png` es real (Playwright contra Grafana 13.3.0
  con el stack y una carga de k6 de ≈12 ejecuciones/s), pero es una sola captura de una
  máquina de desarrollo: no demuestra el comportamiento con incidentes (todas las alertas
  estaban en reposo).
- **Probado en local con Docker Desktop**, no en un VPS, y no probado con Grafana detrás de
  un proxy.
