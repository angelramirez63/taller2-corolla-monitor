# tests/

Pruebas con `pytest`. Casos clave:
- Procesar dos veces el mismo archivo no duplica registros (idempotencia)
- Estados `sin intentar` / `descargado` / `fallido` y datos faltantes vacíos
- Fallo de descarga ≠ anuncio vendido
- Detección de gangas con pocos comparables y precios inválidos
