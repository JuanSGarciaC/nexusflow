#!/usr/bin/env bash
# Prueba de restauración: restaura una copia en una base TEMPORAL y comprueba que los datos
# son los del origen. No toca la base real ni para ningún servicio, así que es seguro
# ejecutarla en producción y se puede automatizar (el CI la ejecuta en cada cambio).
#
# Uso:
#   scripts/restore-check.sh backups/nexusflow-20261029T120000Z.dump
#   STRICT_COUNTS=1 scripts/restore-check.sh <copia>   # exige filas idénticas (CI, sin tráfico)
#
# Qué comprueba:
# 1. pg_restore termina sin errores (--exit-on-error).
# 2. Existen las mismas tablas que en el origen.
# 3. La versión de migración (alembic_version) coincide.
# 4. Filas por tabla: iguales con STRICT_COUNTS=1; en caliente, la copia nunca puede tener
#    MÁS filas que el origen (si las tiene, algo va mal). Si la base crece entre la copia y la
#    prueba, tener menos es normal, por eso no es un fallo salvo en modo estricto.
# Las copias cifradas con age se descifran antes: AGE_IDENTITY=ruta/a/clave-privada.
set -euo pipefail

dump="${1:?Uso: restore-check.sh <copia.dump[.age]>}"
PG_USER="${POSTGRES_USER:-nexusflow}"
PG_DB="${POSTGRES_DB:-nexusflow}"
SCRATCH="${RESTORE_CHECK_DB:-nexusflow_restore_check}"
# shellcheck disable=SC2206
COMPOSE=(docker compose ${COMPOSE_FILES:-})

psql_in() { "${COMPOSE[@]}" exec -T postgres psql -U "$PG_USER" -d "$1" -v ON_ERROR_STOP=1 -At -c "$2" </dev/null; }

plain="$dump"
cleanup() {
  psql_in postgres "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true
  if [ "$plain" != "$dump" ]; then rm -f "$plain"; fi
}
trap cleanup EXIT

if [[ "$dump" == *.age ]]; then
  : "${AGE_IDENTITY:?Copia cifrada: define AGE_IDENTITY con la clave privada de age}"
  plain="$(mktemp)"
  age -d -i "$AGE_IDENTITY" -o "$plain" "$dump"
fi

psql_in postgres "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null
psql_in postgres "CREATE DATABASE \"$SCRATCH\"" >/dev/null
# --no-owner: la prueba no depende de que existan los mismos roles que en el origen.
"${COMPOSE[@]}" exec -T postgres pg_restore -U "$PG_USER" -d "$SCRATCH" \
  --no-owner --exit-on-error < "$plain"

tables_sql="SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1"
src_tables="$(psql_in "$PG_DB" "$tables_sql")"
dst_tables="$(psql_in "$SCRATCH" "$tables_sql")"
if [ "$src_tables" != "$dst_tables" ]; then
  echo "ERROR: las tablas restauradas no coinciden con las del origen." >&2
  diff <(echo "$src_tables") <(echo "$dst_tables") >&2 || true
  exit 1
fi

src_ver="$(psql_in "$PG_DB" 'SELECT version_num FROM alembic_version')"
dst_ver="$(psql_in "$SCRATCH" 'SELECT version_num FROM alembic_version')"
if [ -z "$dst_ver" ] || [ "$src_ver" != "$dst_ver" ]; then
  echo "ERROR: versión de migración distinta (origen '$src_ver', copia '$dst_ver')." >&2
  exit 1
fi

failed=0
total=0
while read -r table; do
  src="$(psql_in "$PG_DB" "SELECT count(*) FROM \"$table\"")"
  dst="$(psql_in "$SCRATCH" "SELECT count(*) FROM \"$table\"")"
  total=$((total + dst))
  if [ "$dst" -gt "$src" ] || { [ "${STRICT_COUNTS:-0}" = "1" ] && [ "$dst" -ne "$src" ]; }; then
    echo "ERROR: $table tiene $dst filas en la copia y $src en el origen." >&2
    failed=1
  else
    printf '  %-24s origen %-6s copia %s\n' "$table" "$src" "$dst"
  fi
done <<< "$src_tables"

if [ "$failed" -ne 0 ]; then exit 1; fi
if [ "$total" -lt 1 ]; then
  echo "ERROR: la copia restaurada está vacía." >&2
  exit 1
fi
echo "Restauración verificada: $(echo "$src_tables" | wc -l | tr -d ' ') tablas, migración $dst_ver, $total filas."
