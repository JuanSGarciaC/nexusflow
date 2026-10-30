#!/usr/bin/env bash
# Copia de seguridad de PostgreSQL (única fuente de verdad; Redis se reconstruye).
#
# Uso (desde la raíz del repo, con el stack en marcha):
#   scripts/backup.sh
#   COMPOSE_FILES="-f docker-compose.yml -f deploy/docker-compose.prod.yml" scripts/backup.sh
#
# Variables opcionales:
#   BACKUP_DIR              destino (por defecto ./backups, fuera de git)
#   BACKUP_RETENTION_DAYS   copias más antiguas se borran (por defecto 14)
#   BACKUP_AGE_RECIPIENT    clave pública de `age`: si existe, la copia se cifra con ella
#   POSTGRES_USER/_DB       por defecto nexusflow
#
# Decisiones:
# - Formato custom (-Fc): ya viene comprimido y permite restaurar tablas sueltas.
# - Se escribe en un archivo temporal y se renombra al final: nunca queda una copia a medias
#   con aspecto de válida.
# - Antes de darla por buena se comprueba que pg_restore puede leer su índice.
# - Las claves Fernet NO se incluyen: se guardan aparte (docs/key-rotation.md).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
PG_USER="${POSTGRES_USER:-nexusflow}"
PG_DB="${POSTGRES_DB:-nexusflow}"
# shellcheck disable=SC2206  # se separa a propósito: son varios argumentos de docker compose
COMPOSE=(docker compose ${COMPOSE_FILES:-})

umask 077
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/nexusflow-$stamp.dump"
tmp="$final.partial"
trap 'rm -f "$tmp"' EXIT

"${COMPOSE[@]}" exec -T postgres pg_dump -U "$PG_USER" -Fc "$PG_DB" > "$tmp"

# Un volcado vacío o truncado no puede listarse: se detecta aquí y no el día de la urgencia.
entries="$("${COMPOSE[@]}" exec -T postgres pg_restore --list < "$tmp" | grep -c 'TABLE DATA' || true)"
if [ "$entries" -lt 1 ]; then
  echo "ERROR: la copia no contiene datos de tablas; se descarta." >&2
  exit 1
fi

if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  age -r "$BACKUP_AGE_RECIPIENT" -o "$final.age" "$tmp"
  final="$final.age"
else
  mv "$tmp" "$final"
fi

find "$BACKUP_DIR" -maxdepth 1 -name 'nexusflow-*.dump*' -mtime "+$RETENTION_DAYS" -delete
echo "Copia creada: $final ($entries tablas en el índice, retención $RETENTION_DAYS días)"
