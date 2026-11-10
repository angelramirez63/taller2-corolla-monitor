# actualizacion — Paso 3 (10 %)

Funciones que reciben los archivos diarios del paso 1 y actualizan la base consolidada.

## Responsabilidades
- Incorporar anuncios nuevos ("nuevo" = observado por primera vez por el sistema)
- Actualizar anuncios existentes conservando sus cambios
- Registrar observaciones diarias sin duplicar anuncios
- **Idempotencia:** procesar dos veces el mismo archivo no duplica información (hash/registro de archivos procesados)

## Distinciones de disponibilidad
| Situación | Significado |
|---|---|
| encontrado | Apareció en la búsqueda del día |
| no encontrado en búsqueda completa | Búsqueda completa sin el anuncio |
| disponibilidad desconocida por error | Falló la descarga; no implica que se vendió |
