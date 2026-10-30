#!/usr/bin/env bash
# Comprobación de un despliegue de PRODUCCIÓN desde fuera, como lo vería un cliente o un
# atacante. Sirve igual contra un VPS real y contra el stack local con Caddy (CI).
#
# Uso:
#   scripts/smoke-prod.sh https://nexusflow.tu-empresa.com
#   CURL_OPTS=-k scripts/smoke-prod.sh https://localhost     # CA interna de Caddy (local/CI)
#
# Variables opcionales:
#   CURL_OPTS               opciones extra de curl. SIN -k, un certificado inválido o caducado
#                           es un fallo: es lo que quieres en un VPS real.
#   SMOKE_EMAIL/_PASSWORD   si se definen, prueba también el inicio de sesión y las cookies.
#   CHECK_PORTS=1           comprueba que PostgreSQL (5432), Redis (6379) y la API (8000) NO
#                           están abiertos en el host. No lo uses si otro servicio tuyo usa
#                           esos puertos.
#
# Qué NO comprueba: que el DNS apunte bien, que Let's Encrypt renueve a tiempo, el firewall
# del proveedor ni nada interno (copias, claves, rotación). Es una prueba de la superficie.
set -uo pipefail

base="${1:?Uso: smoke-prod.sh https://dominio}"
base="${base%/}"
host="${base#*://}"; host="${host%%/*}"; hostname="${host%%:*}"
# shellcheck disable=SC2206
CURL=(curl -sS --max-time 15 ${CURL_OPTS:-})

# Contraseña de relleno aleatoria por ejecución: sin literales con forma de credencial en el repo.
junk="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')-Zz9"
failed=0
ok() { echo "  ok   $1"; }
bad() { echo "  FALLA $1" >&2; failed=1; }
expect() { # descripción, condición (0 = cumple)
  if [ "$2" -eq 0 ]; then ok "$1"; else bad "$1"; fi
}
headers() { "${CURL[@]}" -o /dev/null -D - "$@" 2>/dev/null | tr -d '\r'; }
code() { "${CURL[@]}" -o /dev/null -w '%{http_code}' "$@" 2>/dev/null; }

echo "Comprobando $base"

# 1. TLS y redirección
http_headers="$(headers "http://$hostname/")"
echo "$http_headers" | head -1 | grep -qE ' 30[18] ' && echo "$http_headers" | grep -qi "^location: https://"
expect "HTTP (puerto 80) redirige a HTTPS" $?
[ "$(code "$base/health/ready")" = "200" ]
expect "HTTPS responde y /health/ready es 200 (certificado aceptado por curl)" $?

# 2. Cabeceras de seguridad
h="$(headers "$base/")"
echo "$h" | grep -qiE "^strict-transport-security: max-age=([3-9][0-9]{7}|[0-9]{9,})"
expect "HSTS con max-age >= 1 año" $?
echo "$h" | grep -qi "^content-security-policy: default-src 'self'"
expect "CSP restrictiva (default-src 'self')" $?
echo "$h" | grep -qi "^x-frame-options: DENY"
expect "X-Frame-Options: DENY" $?
echo "$h" | grep -qi "^x-content-type-options: nosniff"
expect "X-Content-Type-Options: nosniff" $?
echo "$h" | grep -qiE "^server: .*[0-9]+\.[0-9]+"
[ $? -ne 0 ]
expect "la cabecera Server no revela la versión" $?

# 3. Superficie que debe estar cerrada
for path in /docs /openapi.json /api/docs /metrics; do
  [ "$(code "$base$path")" = "404" ]
  expect "$path -> 404 (no expuesto)" $?
done
[ "$(code "$base/api/v1/users/me")" = "401" ]
expect "API sin token -> 401" $?
reg="$(code -X POST "$base/api/v1/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"smoke@example.com\",\"password\":\"$junk\"}")"
{ [ "$reg" = "403" ] || [ "$reg" = "404" ]; }
expect "el registro público está desactivado ($reg)" $?

# 4. Puertos internos no publicados
if [ "${CHECK_PORTS:-0}" = "1" ]; then
  for port in 5432 6379 8000; do
    if (exec 3<>"/dev/tcp/$hostname/$port") 2>/dev/null; then bad "el puerto $port está ABIERTO"; else ok "puerto $port cerrado"; fi
  done
fi

# 5. Sesión y cookies (opcional)
if [ -n "${SMOKE_EMAIL:-}" ] && [ -n "${SMOKE_PASSWORD:-}" ]; then
  body="{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$SMOKE_PASSWORD\"}"
  login="$("${CURL[@]}" -D - -X POST "$base/api/v1/auth/login" -H 'Content-Type: application/json' -d "$body" | tr -d '\r')"
  echo "$login" | head -1 | grep -q " 200"
  expect "login correcto -> 200" $?
  cookie="$(echo "$login" | grep -i '^set-cookie:' | head -1)"
  echo "$cookie" | grep -qi "httponly" && echo "$cookie" | grep -qi "secure" && echo "$cookie" | grep -qi "samesite=strict"
  expect "cookie de sesión HttpOnly; Secure; SameSite=Strict" $?
  echo "$cookie" | grep -qi "path=/api/v1/auth"
  expect "cookie limitada a /api/v1/auth" $?
  token="$(echo "$login" | tail -1 | sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p')"
  [ -n "$token" ] && [ "$(code "$base/api/v1/users/me" -H "Authorization: Bearer $token")" = "200" ]
  expect "el access token permite /users/me" $?
  # Mismo cuerpo salvo los campos que varían por petición (request_id, instance). Se exige que
  # ambas respuestas sean un 401 NO vacío: una comparación «vacío == vacío» aprobaría en falso.
  wrong="$("${CURL[@]}" -w '|%{http_code}' -X POST "$base/api/v1/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$junk\"}")"
  ghost="$("${CURL[@]}" -w '|%{http_code}' -X POST "$base/api/v1/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"no-existe-zz@example.com\",\"password\":\"$junk\"}")"
  norm() { sed -E 's/"(request_id|instance)" *: *"[^"]*",? *//g'; }
  [ "${wrong##*|}" = "401" ] && [ "${ghost##*|}" = "401" ] && [ -n "${wrong%|*}" ] && [ "$(echo "$wrong" | norm)" = "$(echo "$ghost" | norm)" ]
  expect "usuario inexistente y contraseña errónea dan el mismo 401 (sin enumeración)" $?
fi

if [ "$failed" -ne 0 ]; then echo "RESULTADO: hay fallos." >&2; exit 1; fi
echo "RESULTADO: todo correcto."
