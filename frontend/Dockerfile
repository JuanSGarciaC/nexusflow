# syntax=docker/dockerfile:1

# ---- Etapa 1: build de la SPA ----
FROM node:24-alpine AS build
WORKDIR /app
# pnpm se activa con corepack en la versión declarada en package.json (packageManager).
RUN corepack enable
COPY package.json pnpm-lock.yaml .npmrc ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# ---- Etapa 2: nginx sin privilegios (escucha en 8080, usuario no root) ----
FROM nginxinc/nginx-unprivileged:1.28-alpine AS runtime
ENV API_UPSTREAM=api:8000 \
    TRUSTED_PROXY_CIDR=127.0.0.1/32
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY security-headers.conf security-headers-docs.conf /etc/nginx/snippets/
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
