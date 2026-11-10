# analisis — Paso 6 (20 %)

## Distribución de precios
Una observación por anuncio (datos válidos descargados ese día). Gráfica + estadísticas: cantidad, mediana, cuartiles.

## Detección de gangas
Método libre; se debe explicar:
- Características consideradas y por qué (año, kilometraje, transmisión, versión, sunroof…)
- Referencia de comparación para cada precio
- Regla/umbral que define una posible ganga
- Manejo de datos faltantes, precios inválidos y pocos comparables
- Limitaciones

Cada alerta incluye una explicación comprensible. Si no hay datos suficientes o no hay gangas, se reporta. En la primera ejecución no se puede afirmar que los precios bajaron.

## Módulos previstos
- `distribucion.py` · `gangas.py` · `explicaciones.py`
