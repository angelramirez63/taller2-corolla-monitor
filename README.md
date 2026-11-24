# Taller 2 — Monitoreo automático de anuncios de Toyota Corolla (Carroya)

Pipeline en Python que cada día consulta los anuncios de **Toyota Corolla 2022–2026** en Carroya, consolida la información en PostgreSQL, registra logs de cada etapa, detecta posibles gangas y envía un correo con los resultados.

- **Periodo evaluado:** 16 al 21 de noviembre de 2026 (6 días). Pruebas: 11 al 15 de noviembre.
- **Recolección:** 8:00 a. m. · **Correo:** 11:00 a. m. (hora de Colombia, `America/Bogota`).
- **Ejecución:** máquina virtual Ubuntu 24.04 en Oracle Cloud (región Bogotá), programada con `cron`.

## Resultados del periodo

| Día | Enlaces | Descargas OK / fallidas | Nuevos | No encontrados | Únicos acumulados | Mediana precio | Gangas nuevas | Correo |
|---|---|---|---|---|---|---|---|---|
| 16 nov | 61 | 61 / 0 | 61 | 0 | 61 | $105.900.000 | 5 | enviado 11:00 |
| 17 nov | 53 | 53 / 0 | 0 | 8 | 61 | $106.900.000 | 0 | enviado 11:00 |
| 18 nov | 57 | 57 / 0 | 1 | 5 | 62 | $106.900.000 | 1 | enviado 11:00 |
| 19 nov | 61 | 61 / 0 | 2 | 3 | 64 | $106.900.000 | 1 | enviado 11:00 |
| 20 nov | 55 | 55 / 0 | 0 | 9 | 64 | $106.900.000 | 0 | enviado 11:00 |
| 21 nov | 57 | 57 / 0 | 0 | 7 | 64 | $106.900.000 | 0 | enviado 11:00 |

Las 24 etapas del periodo (recolección, consolidación, análisis y correo × 6 días) terminaron en estado `ok`. Ver [Incidencias](#incidencias) para el error de detección de cambios encontrado al cierre.

## Estructura

```
taller2-corolla-monitor/
├── scraper_carroya.py          # Paso 1: búsqueda, descarga y archivos diarios
├── db/
│   ├── schema.sql              # Paso 2: esquema PostgreSQL (5 tablas)
│   ├── queries.sql             # consultas pedidas en el paso 2 y log consolidado
│   ├── loader.py               # Paso 3: funciones de actualización (upsert idempotente)
│   ├── recalcular_cambios.py   # reconstruye la tabla cambio desde el historial
│   ├── logger.py               # Paso 5: registro por etapa en log_ejecucion
│   ├── apply_schema.py         # aplica schema.sql sin necesitar psql
│   └── setup_usuario.sql       # crea el usuario y la base (una vez, como superusuario)
├── analisis/gangas.py          # Paso 6: distribución de precios y detección de gangas
├── correo/reporte.py           # Paso 7: plantilla y envío del correo diario
├── pipeline.py                 # Paso 4: orquestador (recoleccion 8:00 / correo 11:00)
├── automatizacion/
│   ├── crontab.txt             # programación en el servidor
│   └── archivar_pruebas.sh     # separa los datos de prueba antes del periodo
├── requirements.txt
└── .env.example                # variables necesarias (el .env real no se versiona)
```

Carpetas generadas al ejecutar (no se versionan): `datos/` (CSV diarios), `reportes/AAAA-MM-DD/` (análisis y gráfica), `logs/`, `correos/enviados/` (copia `.eml`/`.html` de cada correo) y `pruebas/` (corridas de prueba archivadas).

Las carpetas `src/`, `data/`, `config/`, `tests/` y `docs/` vienen de la estructura inicial propuesta para el repositorio y quedaron sin código: la implementación final vive en las rutas de arriba.

## Instalación y ejecución

Requisitos: Python 3.12, PostgreSQL 14+ y acceso a internet.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install --with-deps chromium   # en Windows basta: playwright install chromium
cp .env.example .env                  # completar los valores
```

Variables de `.env`:

| Variable | Uso |
|---|---|
| `DATABASE_URL` | `postgresql://usuario:clave@host:5432/carroya` |
| `SMTP_HOST`, `SMTP_PORT` | servidor de correo (Gmail: `smtp.gmail.com`, `587`) |
| `SMTP_USER`, `SMTP_PASSWORD` | cuenta remitente; con Gmail se usa una contraseña de aplicación |
| `EMAIL_TO` | destinatarios separados por coma |

Base de datos (una vez):

```bash
sudo -u postgres psql -f db/setup_usuario.sql   # o ejecutarlo desde pgAdmin
python db/apply_schema.py
```

Ejecución manual de cada paso:

```bash
python scraper_carroya.py                     # paso 1 → datos/links_AAAA-MM-DD.csv y datos_AAAA-MM-DD.csv
python db/loader.py --fecha 2026-11-16        # paso 3 → actualiza la base con los archivos de ese día
python analisis/gangas.py --fecha 2026-11-16  # paso 6 → reportes/2026-11-16/
python correo/reporte.py --fecha 2026-11-16 --no-enviar   # paso 7 sin enviar (guarda copia)
```

Workflow completo, como lo ejecuta `cron`:

```bash
python pipeline.py recoleccion   # scraper → loader → análisis
python pipeline.py correo        # correo del día
crontab automatizacion/crontab.txt   # programación 8:00 y 11:00
```

## Paso 1 — Recolección de enlaces y datos

- Parte de la búsqueda `carroya.com/automoviles-y-camionetas/toyota/corolla?modelRange=2022-2026` y recorre **todas las páginas**: la página es una aplicación React que carga anuncios al hacer scroll, así que se usa Playwright (Chromium sin interfaz), se hace scroll hasta que no aparecen más enlaces y se avanza mientras exista el botón de la página siguiente.
- Solo se aceptan enlaces cuya ruta es `/detalle/…/toyota/corolla/{año}/…` con año entre 2022 y 2026, y se eliminan duplicados.
- **Archivo de enlaces** `links_AAAA-MM-DD.csv`: id, url, fecha y hora de descubrimiento, estado, fecha y hora del último intento y error.
- **Archivo de datos** `datos_AAAA-MM-DD.csv`: id, url, marca, línea, año, versión, precio, moneda, kilometraje, transmisión, combustible, color, ciudad, sunroof, vendedor, departamento, comentarios del vendedor, estado, fecha y hora de descarga y error.
- Estados: `sin intentar` (hora de intento vacía), `descargado` y `fallido` (con el motivo del error). Ambos archivos se guardan después de cada anuncio; si el proceso se interrumpe, una nueva ejecución el mismo día solo reintenta los pendientes y fallidos.
- Un dato que no aparece queda vacío: nunca se inventa ni se convierte en cero. La ciudad se toma de la URL; el sunroof se detecta si "sunroof", "techo solar" o "techo corredizo" aparece en la ficha o los comentarios.

## Paso 2 — Base consolidada

**Almacenamiento elegido: PostgreSQL**, con 5 tablas ([db/schema.sql](db/schema.sql)):

| Tabla | Una fila por | Contenido |
|---|---|---|
| `anuncio` | anuncio único | URL, marca, línea, año, ciudad, vendedor, `primera_vista`, `ultima_vista` |
| `observacion` | anuncio y día | estado del día, precio, km, versión, transmisión, combustible, color, sunroof, hora, error |
| `cambio` | anuncio, día y campo | valor anterior y nuevo de precio, kilometraje o versión |
| `vendedor` | vendedor | nombre y departamento |
| `log_ejecucion` | día y etapa | métricas y estado de cada etapa (paso 5) |

**Identificador estable.** Cada publicación se identifica por su **URL completa** (`anuncio.url` es `UNIQUE`), que contiene el código propio de Carroya (un UUID como `…/bogota/1461ca6c-e4fe-…` o, en publicaciones antiguas, un número). Ese código no cambia aunque el vendedor edite el precio o el texto, así que volver a encontrar la publicación actualiza el mismo registro en vez de crear otro. `anuncio.id` es un entero interno para las relaciones entre tablas. En los CSV diarios el `id` es un código corto por archivo; la llave entre días es la URL.

**Mismo vehículo en anuncios distintos.** No se intentó unificar: Carroya solo muestra el último dígito de la placa y no publica el VIN. Un criterio posible sería coincidencia de año, versión, color, ciudad y kilometraje cercano (±1 %), pero con unos 60 anuncios casi idénticos (mismo modelo, versiones repetidas) produciría falsos positivos, así que la unidad de análisis es el anuncio único.

Consultas del enunciado en [db/queries.sql](db/queries.sql): anuncios únicos (Q1), información más reciente de cada anuncio (Q2), observaciones diarias y cambios (Q3), primera y última fecha de observación (Q4), disponibilidad y fecha/hora de cada descarga (Q5) y log consolidado (Q6).

**Comparación con otras dos alternativas.**

| | PostgreSQL (elegida) | Archivos CSV/Parquet consolidados | Base de documentos (MongoDB) |
|---|---|---|---|
| Ventajas | Llave única y restricciones garantizan que no se dupliquen anuncios; `ON CONFLICT` hace las cargas idempotentes; las 5 consultas pedidas son SQL directo; transacciones | Sin servidor ni instalación; fáciles de abrir y de entregar; Parquet es compacto y rápido con pandas | Esquema flexible si Carroya agrega campos; un documento por anuncio con su historial anidado |
| Desventajas | Requiere un servidor y credenciales; más pasos de instalación | La unicidad y la idempotencia hay que programarlas a mano; reescribir el archivo completo en cada carga; riesgo de corrupción si una carga se interrumpe | Las uniones y agregaciones (último estado, cambios por día) son más incómodas; sin restricciones de esquema los datos faltantes o mal tipados pasan sin control |

Los datos son tabulares y relacionales (anuncio → observaciones diarias → cambios), y lo que más se evalúa es no duplicar y conservar historial, por eso se eligió una base relacional. Para la entrega, la base se exporta además a CSV.

## Paso 3 — Funciones de actualización

[db/loader.py](db/loader.py) recibe el archivo de datos y el de enlaces de un día y actualiza la base en este orden:

1. `upsert_vendedor` y `upsert_anuncio`: insertan anuncios nuevos; si ya existen, solo actualizan `ultima_vista` (`GREATEST`) y `primera_vista` (`LEAST`).
2. `upsert_observacion`: una fila por anuncio y día (`UNIQUE (anuncio_id, fecha)`); reprocesar el mismo archivo la sobrescribe en vez de duplicarla.
3. `insert_cambios`: compara precio, kilometraje y versión con la observación descargada anterior y registra las diferencias (`ON CONFLICT DO NOTHING`).
4. `marcar_no_encontrados`: los anuncios vistos en días anteriores que no aparecen en el archivo de enlaces del día reciben una observación `no_encontrado`.

Así se distinguen los tres casos que pide el taller:

| Estado | Significado | Qué **no** se concluye |
|---|---|---|
| `descargado` | encontrado en la búsqueda y extraído | — |
| `fallido` | encontrado en la búsqueda, pero la descarga del detalle falló: disponibilidad desconocida | no se marca como fuera de oferta |
| `no_encontrado` | la búsqueda completa terminó y el anuncio no apareció | no se asume que se vendió (puede reaparecer) |

Procesar dos veces el mismo día deja la base igual (verificado: mismas filas en `anuncio`, `observacion` y `cambio`).

## Paso 4 — Automatización

- [pipeline.py](pipeline.py) ejecuta cada etapa como un proceso separado con límite de tiempo: si una falla o se cuelga, las siguientes igual se intentan y el correo informa lo ocurrido. Si una etapa supera su límite, su registro en el log pasa a `error`.
- [automatizacion/crontab.txt](automatizacion/crontab.txt): `pipeline.py recoleccion` a las 8:00 y `pipeline.py correo` a las 11:00, del 12 al 21 de noviembre. La zona horaria del sistema es `America/Bogota`, así que las horas de cron son hora de Colombia.
- **Condiciones para funcionar:** la máquina virtual de Oracle Cloud debe estar encendida (no depende de ningún computador personal); se paga con los créditos de prueba de Oracle, vigentes hasta el 12 de diciembre de 2026. Necesita salida a internet (Carroya y SMTP). `cron` no recupera ejecuciones perdidas: si la máquina estuviera apagada a las 8:00, el correo de las 11:00 lo reportaría como etapa no ejecutada.
- **Pruebas:** del 11 al 15 de noviembre se ejecutó el workflow completo con la misma programación; esos correos llevan `[PRUEBA]` en el asunto y una franja de aviso. El 15 a las 23:00, [automatizacion/archivar_pruebas.sh](automatizacion/archivar_pruebas.sh) movió los archivos de prueba a `pruebas/`, guardó un respaldo de la base y la vació, de modo que el periodo evaluado empezó limpio (el 16 todos los anuncios cuentan como nuevos).

## Paso 5 — Logs

- Tabla `log_ejecucion` ([db/logger.py](db/logger.py)): una fila por día (`run_id`) y etapa (`scraper`, `loader`, `analisis`, `correo`) con inicio, fin, duración, estado, enlaces encontrados, descargas exitosas/fallidas/pendientes, anuncios nuevos, registros actualizados/sin cambio, no encontrados, anuncios únicos, tamaño de la base en MB, estado del correo y errores.
- Archivo `logs/pipeline_AAAA-MM-DD.log`: salida completa de cada etapa con marca de tiempo.
- Todas las horas están en `America/Bogota` con desfase explícito (`-0500`).

## Paso 6 — Análisis de precios y gangas

[analisis/gangas.py](analisis/gangas.py):

- **Distribución diaria:** una observación por anuncio (los descargados ese día con precio válido); histograma con mediana y cuartiles y precio por versión.
- **Características:** año, kilometraje (en logaritmo), versión (XLI, XEI, SEG, GR-S), motor híbrido, blindaje y color blanco. La versión se normaliza desde el texto libre de la ficha y, si es ambiguo ("1.8 Aut"), desde los comentarios del vendedor; híbrido sale del campo `COMBUSTIBLE`; blindaje de los comentarios. No se usan la transmisión (más del 90 % es automática y las manuales son casi todas GR-S) ni el sunroof (viene de serie en el SEG y solo se detecta cuando el vendedor lo menciona, así que medía la redacción del anuncio y no el carro).
- **Referencia:** regresión ridge de `log(precio)` sobre esas características, entrenada con la observación más reciente de cada anuncio único visto hasta la fecha. La referencia de cada anuncio se calcula con el modelo entrenado **sin ese anuncio** (leave-one-out), para que un precio atípico no se compare contra sí mismo.
- **Regla:** posible ganga si el precio publicado está **al menos 10 % por debajo** de su referencia **y** el residuo es de al menos una desviación robusta (MAD) del modelo.
- **Datos faltantes y pocos comparables:** no se evalúa (y se informa el motivo) si el precio falta o está fuera de 30–300 millones de COP, si la versión no se puede identificar, si falta el kilometraje o si hay menos de 3 anuncios de la misma versión o menos de 2 con el mismo atributo especial (híbrido, blindado). Con menos de 25 anuncios completos no se ajusta el modelo.
- **Explicación de cada alerta:** precio publicado, precio de referencia y rango típico, porcentaje de diferencia, ajustes aplicados ("+9 % por versión SEG", "−4 % por kilometraje"…) y medianas de anuncios comparables.
- **Limitaciones:** el modelo explica cerca del 44 % de la variación de precios con unos 60 anuncios; no ve el estado del carro, accidentes, peritaje ni si el vendedor es concesionario; versión, blindaje y sunroof dependen del texto del vendedor; errores de publicación (por ejemplo 1.000 km en un carro de 2023) pueden generar alertas. Una alerta es una invitación a revisar el anuncio, no una garantía.

Efectos estimados con los 61 anuncios del 21 de noviembre: +6 % por cada año más nuevo, −2,6 % cada vez que el kilometraje se duplica, SEG +9 % y XLI −7 % frente a XEI, GR-S +4 %, híbrido +2 %, blindado +9 %, blanco +1 %.

## Paso 7 — Correo diario

[correo/reporte.py](correo/reporte.py) arma un correo HTML (con versión en texto plano) con: estado general del proceso, enlaces encontrados, anuncios nuevos, descargas exitosas y fallidas, estado de cada etapa, errores y etapas pendientes, gráfica y resumen de la distribución de precios, y una tarjeta por cada posible ganga nueva (enlace, características, precio, referencia y explicación). Se envía aunque falle parte del proceso, no haya gangas o la base no responda, e indica si una etapa seguía en curso a las 11:00. Cada envío se guarda en `correos/enviados/` como `.eml` y `.html` y su estado queda en `log_ejecucion`.

## Incidencias

**Detección de cambios (corregida el 23 de noviembre, después del periodo).** Al exportar la base para la entrega se encontró que la tabla `cambio` tenía 840 registros falsos: `insert_cambios` pasaba el nombre de la columna como parámetro (`SELECT %s::text`), por lo que leía el texto "precio", "kilometraje" o "version" en vez del valor y registraba un cambio por campo cada vez que un anuncio volvía a aparecer. Se corrigió la consulta y [db/recalcular_cambios.py](db/recalcular_cambios.py) reconstruyó la tabla comparando cada observación con la anterior; la verificación independiente con pandas sobre `historial_observaciones.csv` confirma que **en el periodo ningún anuncio cambió de precio, kilometraje ni versión** (0 cambios). Afectó la tabla `cambio` y el indicador `registros_actualizados` del log de los días 17 a 21 (que contó como actualizados todos los anuncios repetidos). No afectó las observaciones, los anuncios únicos, la detección de gangas ni los correos. En la entrega se incluyen el respaldo original (`carroya_periodo.sql`), el corregido (`carroya_periodo_corregido.sql`) y `correccion_registros_actualizados.csv` con el valor registrado y el corregido de cada día; la tabla `log_ejecucion` no se modificó.
