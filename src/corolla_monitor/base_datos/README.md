# base_datos — Paso 2 (15 %)

Diseño de la base consolidada (formato por decidir: SQLite, Parquet, CSV…; se justifica frente a otras dos alternativas en `docs/`).

## Debe permitir consultar
- Anuncios únicos del periodo
- Información más reciente de cada anuncio
- Observaciones diarias y cambios detectados
- Primera y última fecha en que se observó cada anuncio
- Disponibilidad observada y fecha/hora de cada descarga

## Tablas previstas
- `anuncios`: un registro por anuncio único (id estable, primera/última vez visto, estado actual)
- `observaciones`: historial diario por anuncio
- `cambios`: campos modificados entre observaciones

## Identificador
Id estable derivado del identificador de la publicación en Carroya (a documentar). Si se intenta reconocer el mismo vehículo en anuncios distintos, se documenta criterio y limitaciones.
