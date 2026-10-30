#!/usr/bin/env bash
# Sube las copias CIFRADAS de scripts/backup.sh a un almacén remoto con rclone y comprueba
# que lo subido es idéntico. Una copia en el mismo disco no protege del fallo del disco.
#
# Uso:
#   BACKUP_AGE_RECIPIENT=age1... scripts/backup.sh      # copia cifrada con age
#   scripts/backup-offsite.sh <remoto:ruta>             # p. ej. s3:mi-bucket/nexusflow
#
# <remoto:ruta> es cualquier destino de rclone (S3, Backblaze B2, SFTP, Google Drive...) o
# una ruta local. Configúralo antes con `rclone config`.
#
# Variables opcionales:
#   BACKUP_DIR              origen (por defecto ./backups)
#   OFFSITE_RETENTION_DAYS  borra del remoto lo más antiguo que esto (por defecto 30)
#   ALLOW_PLAINTEXT=1       permite subir volcados SIN cifrar (no lo hagas)
#
# Decisiones:
# - Solo se suben archivos *.age. Un volcado .dump en claro contiene los datos de todos los
#   clientes y credenciales cifradas con una clave que quizá viaja con él: se rechaza.
# - `rclone copy` (no `sync`): un fallo o un borrado accidental en el origen NUNCA borra la
#   copia remota. La retención del remoto es una orden explícita y aparte.
# - `rclone check --one-way` compara tamaño y hash tras subir: «subido» no es «íntegro».
# - La clave privada de age NO se sube ni se guarda junto a las copias.
set -euo pipefail

remote="${1:?Uso: backup-offsite.sh <remoto:ruta>}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${OFFSITE_RETENTION_DAYS:-30}"

if ! command -v rclone >/dev/null 2>&1; then
  echo "ERROR: falta rclone (https://rclone.org/install/)." >&2
  exit 1
fi

shopt -s nullglob
plain=("$BACKUP_DIR"/nexusflow-*.dump)
encrypted=("$BACKUP_DIR"/nexusflow-*.dump.age)

if [ "${#plain[@]}" -gt 0 ] && [ "${ALLOW_PLAINTEXT:-0}" != "1" ]; then
  echo "AVISO: se ignoran ${#plain[@]} volcado(s) SIN cifrar en $BACKUP_DIR (no se suben)." >&2
fi
if [ "${ALLOW_PLAINTEXT:-0}" = "1" ]; then
  to_upload=("${plain[@]}" "${encrypted[@]}")
else
  to_upload=("${encrypted[@]}")
fi
if [ "${#to_upload[@]}" -eq 0 ]; then
  echo "ERROR: no hay copias cifradas (*.dump.age) que subir. Ejecuta scripts/backup.sh con BACKUP_AGE_RECIPIENT." >&2
  exit 1
fi

# Se sube solo la lista explícita, nunca el directorio entero.
list="$(mktemp)"
printf '%s\n' "${to_upload[@]##*/}" > "$list"
trap 'rm -f "$list"' EXIT

rclone copy "$BACKUP_DIR" "$remote" --files-from "$list" --checksum
rclone check "$BACKUP_DIR" "$remote" --files-from "$list" --one-way

rclone delete "$remote" --min-age "${RETENTION_DAYS}d" --include 'nexusflow-*.dump*'
echo "Subidas y verificadas ${#to_upload[@]} copia(s) en $remote (retención remota ${RETENTION_DAYS} días)."
