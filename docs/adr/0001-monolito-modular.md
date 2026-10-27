# ADR-0001: Monolito modular con procesos separados

- **Estado:** Aceptada
- **Fecha:** 2026-10-27

## Contexto

NexusFlow tiene un solo dominio (automatización de flujos), un equipo pequeño y un volumen
inicial moderado. Las cargas, en cambio, son distintas: peticiones HTTP cortas frente a
ejecuciones largas que dependen de APIs externas lentas o inestables.

## Decisión

Un único código backend organizado en módulos con límites claros (`auth`, `workflows`,
`engine`, `integrations`, `webhooks`), empaquetado en **una sola imagen** que se ejecuta
como varios **procesos**: `api`, `worker` y `beat`.

## Alternativas descartadas

- **Microservicios.** Añaden red, contratos versionados, trazas distribuidas y
  despliegues coordinados sin un beneficio real con un equipo y un dominio. Los límites
  entre módulos permiten extraer un servicio más adelante si aparece la necesidad (por
  ejemplo, si `integrations` necesita aislamiento de red propio).
- **Kubernetes.** Docker Compose cubre desarrollo, CI y un despliegue en una VM. La imagen
  ya es compatible con un orquestador si hiciera falta.

## Consecuencias

- Una sola versión que desplegar y un solo lockfile.
- `api` y `worker` escalan de forma independiente (`docker compose up --scale worker=3`).
- Hay que vigilar que los módulos no se acoplen: la regla es que otros módulos solo
  importan la capa `services` de un módulo, nunca sus repositorios.
