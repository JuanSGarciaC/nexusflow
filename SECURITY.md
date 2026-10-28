# Política de seguridad

NexusFlow es un proyecto de portafolio. Aun así, los problemas de seguridad se tratan en
serio.

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa el formulario privado de GitHub:
*Security → Report a vulnerability* en este repositorio.

Incluye, si puedes:
- versión o commit afectado;
- pasos para reproducirlo (idealmente una petición HTTP o una prueba);
- impacto que observas.

Recibirás una primera respuesta en un plazo razonable y se te avisará cuando haya una
corrección publicada.

## Alcance

El diseño de seguridad, los controles y sus pruebas están en
[docs/security-review.md](docs/security-review.md), junto con las **limitaciones
conocidas y aceptadas** (por ejemplo, un único espacio de trabajo sin aislamiento entre
equipos). Reportar una de esas limitaciones no se considera una vulnerabilidad nueva.

`mock-partner/` es una API simulada para demostraciones y CI: no forma parte del producto.
