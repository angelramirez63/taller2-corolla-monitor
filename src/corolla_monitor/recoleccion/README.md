# recoleccion — Paso 1 (20 %)

Recolecta enlaces y descarga datos de los anuncios.

## Criterios de búsqueda
Marca Toyota · Línea Corolla · Modelos 2022–2026. Se recorren **todas** las páginas de resultados y se verifica que cada anuncio cumpla los criterios.

## Salidas diarias (en `data/diario/`)
1. **Archivo de enlaces** (`enlaces/enlaces_AAAA-MM-DD.csv`): enlaces únicos, id, fecha/hora de descubrimiento, estado, fecha/hora del último intento.
2. **Archivo de datos** (`datos/datos_AAAA-MM-DD.csv`): marca, línea, año, versión, precio, moneda, kilometraje, transmisión, ciudad, sunroof + id, enlace, estado, fecha/hora de descarga.

## Estados
`sin intentar` · `descargado` · `fallido` (con motivo del error). Para `sin intentar` la hora del intento queda vacía. Un dato ausente queda vacío, no se inventa ni se pone en 0.

## Módulos previstos
- `enlaces.py`: paginación y extracción de enlaces
- `descarga.py`: descarga y extracción por anuncio
- `validacion.py`: verificación de criterios
