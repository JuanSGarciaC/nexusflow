# Guía de despliegue

Despliegue de referencia: **una máquina virtual Linux con Docker Compose**, con Caddy como
proxy TLS. Es suficiente para un equipo o una pyme. La sección [Escalar](#escalar) explica
qué cambiar cuando deje de serlo.

```mermaid
flowchart LR
    u[Usuarios] -->|HTTPS 443| caddy[Caddy · TLS automático]
    ext[Sistemas externos] -->|webhooks HTTPS| caddy
    caddy --> web[web · nginx + SPA]
    web -->|/api| api[api]
    api --> pg[(PostgreSQL)]
    api --> redis[(Redis)]
    redis --> worker[worker ×N]
    beat[beat ×1] --> redis
    worker --> pg
    worker -->|HTTPS anti-SSRF| apis[APIs de terceros]
    prom[Prometheus] -.->|/metrics + token| api
```

## 1. Requisitos

- VM con 2 vCPU y 4 GB de RAM como mínimo (Ubuntu 24.04 LTS o similar).
- Docker Engine con el plugin Compose v2.24 o superior (el override usa `!reset`).
- Un dominio con registro DNS `A`/`AAAA` apuntando a la VM.
- Puertos 80 y 443 abiertos. **Ningún otro**: PostgreSQL, Redis y la API no se publican.

## 2. Configuración

```bash
git clone https://github.com/JuanSGarciaC/nexusflow.git && cd nexusflow
cp .env.example .env
chmod 600 .env          # solo el usuario que despliega puede leerlo
```

Genera **todos** los secretos; en producción la API no arranca con valores de ejemplo:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"     # POSTGRES_PASSWORD, REDIS_PASSWORD, NEXUSFLOW_SECRET_KEY
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS
```

Añade a `.env`:

```dotenv
NEXUSFLOW_DOMAIN=nexusflow.tu-empresa.com
# Opcional: token para que Prometheus lea /metrics
NEXUSFLOW_METRICS_TOKEN=<token_hex(24)>
```

Para un entorno serio, guarda estos valores en un gestor de secretos (Vault, AWS Secrets
Manager, 1Password…) y genera `.env` al desplegar en lugar de dejarlo en el disco.

## 3. Arranque

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml exec api \
  nexusflow create-admin --email admin@tu-empresa.com
```

Comprueba:

```bash
curl -fsS https://nexusflow.tu-empresa.com/health/ready
curl -sI  https://nexusflow.tu-empresa.com/ | grep -iE "strict-transport|content-security"
```

Qué hace el override de producción ([`deploy/docker-compose.prod.yml`](../deploy/docker-compose.prod.yml)):

| Cambio | Motivo |
|---|---|
| Solo Caddy publica puertos (80/443) | Superficie mínima; TLS y HSTS automáticos |
| `NEXUSFLOW_ENV=production` | La API rechaza secretos de ejemplo, CORS `*`, cookies sin `Secure` y salida HTTP a hosts privados |
| `/docs` y registro público desactivados | Los usuarios los crea un administrador |
| `TRUSTED_PROXY_CIDR` = IP fija de Caddy | nginx toma la IP real del cliente solo de Caddy; la API, solo de nginx. Límites de uso y auditoría ven la IP correcta y nadie puede falsificarla |

## 4. Actualizaciones

```bash
git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

- El servicio `migrate` aplica `alembic upgrade head` **antes** de que arranquen la API y el
  worker. Si una migración falla, la versión nueva no arranca y la anterior sigue en marcha.
- El worker tiene `stop_grace_period: 60s`: termina las tareas en curso antes de parar.
  Si una tarea se corta, `acks_late` la devuelve a la cola y el reclamo en PostgreSQL
  impide ejecutarla dos veces (ADR-0007).
- Migraciones con cambios incompatibles: hacerlas en dos pasos (expandir y luego contraer)
  para que la versión anterior siga funcionando mientras se despliega.

## 5. Copias de seguridad

PostgreSQL es la única fuente de verdad. Redis solo guarda la cola y los contadores de
límites; perderlo no pierde datos, porque el barrido periódico reencola lo pendiente.

```bash
# Copia diaria (cron) comprimida y con fecha
docker compose exec -T postgres pg_dump -U nexusflow -Fc nexusflow > backup-$(date +%F).dump

# Restauración (con la API y el worker parados)
docker compose stop api worker beat
docker compose exec -T postgres pg_restore -U nexusflow -d nexusflow --clean < backup-AAAA-MM-DD.dump
docker compose start api worker beat
```

- Cifra las copias y guárdalas fuera de la VM.
- **Las claves de `NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS` se guardan aparte de las copias.**
  Sin ellas, las credenciales de integraciones no se pueden descifrar; si van juntas, quien
  robe la copia tiene también los secretos.
- Prueba la restauración periódicamente: una copia que nunca se restauró no es una copia.

## 6. Rotación de secretos

| Secreto | Procedimiento | Efecto |
|---|---|---|
| `NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS` | Pon la clave nueva **delante** (`nueva,antigua`), reinicia, ejecuta `nexusflow rotate-credentials` y después quita la antigua | Sin interrupción (MultiFernet) |
| `NEXUSFLOW_SECRET_KEY` | Cambiar y reiniciar | Los access tokens en vuelo dejan de valer; las sesiones se recuperan con el refresh token (vive en la BD) |
| Secreto de un webhook entrante | `POST /api/v1/triggers/{id}/rotate-secret` | El anterior deja de valer al instante; avisa antes al emisor |
| `POSTGRES_PASSWORD` / `REDIS_PASSWORD` | Cambiar en el servicio y en `.env`, reiniciar | Interrupción breve |

## 7. Observabilidad

- **Logs**: JSON en stdout, con `request_id` en cada línea. Con `docker compose logs` basta
  para empezar; en producción, envíalos a Loki, CloudWatch o Elastic.
- **Métricas**: Prometheus lee la API **dentro de la red** (nginx devuelve 404 en `/metrics`):

  ```yaml
  scrape_configs:
    - job_name: nexusflow
      metrics_path: /metrics
      authorization: { type: Bearer, credentials_file: /etc/prometheus/nexusflow_token }
      static_configs: [{ targets: ["api:8000"] }]
  ```

  Alertas recomendadas: `nexusflow_webhook_deliveries_open{status="dead"} > 0`,
  `nexusflow_steps_active{status="needs_review"} > 0`, y la tasa de 5xx en
  `nexusflow_http_requests_total`.
- **Programaciones cron**: las dispara `beat` (a través de la tarea de barrido, cada 15 s).
  **Si `beat` está parado, no se ejecuta ninguna programación**; vigílalo igual que al worker.
  Tras una caída, los disparos con más de 5 minutos de retraso
  (`NEXUSFLOW_SCHEDULE_MISFIRE_GRACE_SECONDS`) se omiten y quedan anotados en el último error
  de cada programación.
- **Health checks**: `/health/live` para reinicios, `/health/ready` para balanceadores.

## 8. Lista de comprobación de seguridad

- [ ] Todos los secretos generados; `.env` con permisos `600` y fuera del repositorio.
- [ ] Solo los puertos 80/443 abiertos en el cortafuegos.
- [ ] `NEXUSFLOW_ENV=production` (lo pone el override).
- [ ] Registro público desactivado; usuarios creados por un administrador.
- [ ] Copias de PostgreSQL cifradas, fuera de la VM, y restauración probada.
- [ ] Claves de cifrado de credenciales guardadas aparte de las copias.
- [ ] `NEXUSFLOW_METRICS_TOKEN` definido si se usa Prometheus.
- [ ] Actualizaciones de seguridad de la VM y reconstrucción periódica de las imágenes.
- [ ] Hosts de cada credencial limitados a los necesarios (`allowed_hosts`).

## Escalar

| Síntoma | Medida |
|---|---|
| Cola de pasos creciendo | `docker compose up -d --scale worker=4` (el reclamo en PostgreSQL permite N workers) |
| CPU de la API alta | Varias réplicas de `api` detrás de nginx (`upstream` con varios `server`) |
| Varias máquinas | PostgreSQL y Redis gestionados (RDS / Cloud SQL, ElastiCache / Memorystore); la misma imagen en un orquestador (ECS, Cloud Run, Kubernetes). `beat` sigue siendo **una sola** instancia |
| Millones de ejecuciones | Particionar `workflow_runs` y `step_attempts` por mes; agregados precalculados para las métricas (ADR-0010) |
