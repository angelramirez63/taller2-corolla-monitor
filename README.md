# Taller 2 — Monitoreo automático de anuncios de Toyota Corolla (Carroya)

Pipeline en Python que cada día consulta los anuncios de **Toyota Corolla 2022–2026** en Carroya, consolida la información, registra logs, detecta posibles gangas y envía un correo con los resultados.

- **Periodo:** 16 al 21 de noviembre de 2026 (6 días)
- **Recolección:** 8:00 a. m. · **Correo:** 11:00 a. m. (`America/Bogota`)
- **Entrega:** 23 de noviembre de 2026, 11:59 p. m., Bloque Neón

## Estructura del repositorio

```
taller2-corolla-monitor/
├── README.md
├── requirements.txt
├── .env.example               # plantilla de credenciales (el .env real no se versiona)
├── .gitignore
├── config/                    # configuración no sensible
├── src/
│   └── corolla_monitor/
│       ├── recoleccion/       # Paso 1: enlaces y datos diarios
│       ├── base_datos/        # Paso 2: base consolidada
│       ├── actualizacion/     # Paso 3: funciones de actualización
│       ├── automatizacion/    # Paso 4: ejecución diaria 8:00 / 11:00
│       ├── logs/              # Paso 5: logs del workflow
│       ├── analisis/          # Paso 6: precios y gangas
│       └── correo/            # Paso 7: plantilla y envío
├── data/
│   ├── diario/
│   │   ├── enlaces/           # un archivo de enlaces por día
│   │   └── datos/             # un archivo de datos por día
│   ├── consolidada/           # base consolidada
│   ├── logs/                  # logs por ejecución y consolidado
│   └── pruebas/               # corridas de prueba (no se versionan)
├── correos/
│   ├── plantillas/            # plantilla del correo diario
│   └── enviados/              # los 6 correos exportados
├── docs/                      # enunciado, arquitectura, decisiones, incidentes
├── tests/                     # pruebas con pytest
└── entregable/                # paquete final para Bloque Neón (Paso 8)
```

### Detalle por carpeta

| Ruta | Paso del taller | Peso | Contenido |
|---|---|---|---|
| `src/corolla_monitor/recoleccion/` | 1. Recolectar enlaces y datos | 20 % | Scraping de todas las páginas, archivos diarios de enlaces y datos |
| `src/corolla_monitor/base_datos/` | 2. Base consolidada | 15 % | Esquema, identificador estable de anuncio, consultas |
| `src/corolla_monitor/actualizacion/` | 3. Funciones de actualización | 10 % | Upsert idempotente, estados encontrado / no encontrado / desconocido |
| `src/corolla_monitor/automatizacion/` | 4. Ejecución diaria | 10 % | Orquestador y programación 8:00 / 11:00 |
| `src/corolla_monitor/logs/` | 5. Logs del workflow | 10 % | Registro por ejecución y etapa |
| `src/corolla_monitor/analisis/` | 6. Precios y gangas | 20 % | Distribución de precios y detección de gangas |
| `src/corolla_monitor/correo/` + `correos/` | 7. Correo diario | 5 % | Plantilla, envío y correos exportados |
| `entregable/` + `docs/` | 8. Entrega | 10 % | Paquete final y documento de una página |
| `data/` | — | — | Archivos diarios, base consolidada, logs y datos de prueba |
| `tests/` | — | — | Pruebas (idempotencia, estados, detección) |
| `config/` | — | — | Configuración sin credenciales |

Cada carpeta tiene su propio `README.md` con detalle.

## Inicio rápido

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # completar credenciales (nunca se versiona)
```

## Convenciones

- Zona horaria única: `America/Bogota` en logs, archivos y correos.
- Datos faltantes se dejan vacíos (nunca se inventan ni se convierten en 0).
- Las pruebas posteriores al periodo se guardan en `data/pruebas/` y no reemplazan las evidencias originales.
- Sin contraseñas ni credenciales en el repositorio.

## Decisiones de diseño

Se documentan en [`docs/`](docs/README.md) (identificación, almacenamiento, actualización, automatización, detección de gangas).
