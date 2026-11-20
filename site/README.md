# Landing de NexusFlow

Página estática que presenta el proyecto. HTML, CSS y JS puro: sin dependencias, sin build,
sin CDN y con rutas relativas, lista para servirse desde cualquier hosting estático
(GitHub Pages incluido). No forma parte del panel (`frontend/`).

## Probar en local

```powershell
cd site
python -m http.server 8765 --bind 127.0.0.1
# http://127.0.0.1:8765/
```

Abrirla con doble clic (`file://`) también funciona.

## Efectos (catálogo de efectos web)

| Sección | Técnica |
|---|---|
| Hero | B1 split text sobre E3 rejilla de puntos |
| El fallo | **C4 + H5** (efecto estrella): diagrama fijo del flujo *Cobro a cliente VIP*; el scroll avanza por los pasos 503 → 503 → 201 con la misma `Idempotency-Key` |
| Webhook | H3 terminal con la salida real del guion de [docs/demo.md](../docs/demo.md) |
| Calidad | A7 count-up |
| Capturas | A2 stagger + A1 reveal |

- `prefers-reduced-motion`: sin animaciones; el diagrama cambia de estado sin paquete viajero.
- Sin JS: todo el texto visible y el diagrama en su estado final.
- Móvil (< 900 px): el diagrama queda fijo arriba, compacto, y los pasos pasan por debajo.
- CSP en `<meta>`: solo recursos propios, sin scripts ni estilos inline.

## Capturas

`img/` son copias de `docs/screenshots/` para que la carpeta sea autónoma. Si se regeneran:

```powershell
foreach ($f in '01-panel','03-editor-flujo','06-detalle-ejecucion','10-grafana','15-entregas') {
  Copy-Item "docs/screenshots/$f.png" "site/img/$f.png"
}
```
