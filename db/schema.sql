-- ══════════════════════════════════════════════════════════════════════════════
--  CARROYA SCRAPER — Esquema PostgreSQL
--  Taller 2 — Maestría Uniandes
--  Compatible con PostgreSQL 14+, Neon, Supabase
-- ══════════════════════════════════════════════════════════════════════════════

-- ─── ENUM ─────────────────────────────────────────────────────────────────────
-- Valores y su semántica:
--   sin intentar  → link recogido, detalle aún no descargado (estado transitorio del scraper)
--   descargado    → anuncio encontrado en la búsqueda y detalle extraído correctamente
--   fallido       → anuncio encontrado en la búsqueda pero la descarga del detalle falló
--                   (disponibilidad desconocida: el vehículo puede seguir en oferta)
--   no_encontrado → la búsqueda corrió completa y este anuncio no apareció en los resultados
--                   (posiblemente vendido/retirado, pero no hay certeza — puede reaparecer)
DO $$ BEGIN
    CREATE TYPE estado_tipo AS ENUM (
        'sin intentar',
        'descargado',
        'fallido',
        'no_encontrado'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Para instalaciones existentes que no tengan aún 'no_encontrado':
ALTER TYPE estado_tipo ADD VALUE IF NOT EXISTS 'no_encontrado';

-- ─── TABLA 1: vendedor ────────────────────────────────────────────────────────
-- Un registro por vendedor único (nombre + departamento).
-- Un vendedor puede tener múltiples anuncios.
CREATE TABLE IF NOT EXISTS vendedor (
    id              SERIAL          PRIMARY KEY,
    nombre          VARCHAR(200)    NOT NULL,
    departamento    VARCHAR(100),
    UNIQUE (nombre, departamento)
);

-- ─── TABLA 2: anuncio ─────────────────────────────────────────────────────────
-- Un registro por anuncio único (URL como clave natural).
-- Los atributos estáticos (año, ciudad, vendedor) viven aquí.
-- primera_vista / ultima_vista se actualizan en cada carga del loader.
CREATE TABLE IF NOT EXISTS anuncio (
    id              SERIAL          PRIMARY KEY,
    url             TEXT            NOT NULL UNIQUE,
    marca           VARCHAR(50),
    linea           VARCHAR(50),
    año             SMALLINT,
    ciudad          VARCHAR(100),
    vendedor_id     INTEGER         REFERENCES vendedor(id),
    primera_vista   DATE            NOT NULL,
    ultima_vista    DATE            NOT NULL,
    CONSTRAINT chk_vistas CHECK (ultima_vista >= primera_vista)
);

CREATE INDEX IF NOT EXISTS idx_anuncio_año      ON anuncio(año);
CREATE INDEX IF NOT EXISTS idx_anuncio_ciudad   ON anuncio(ciudad);
CREATE INDEX IF NOT EXISTS idx_anuncio_vend     ON anuncio(vendedor_id);

-- ─── TABLA 3: observacion ─────────────────────────────────────────────────────
-- Una fila por anuncio por día de scraping.
-- Todos los campos que pueden cambiar entre días (precio, km, etc.) van aquí.
CREATE TABLE IF NOT EXISTS observacion (
    id              SERIAL          PRIMARY KEY,
    anuncio_id      INTEGER         NOT NULL REFERENCES anuncio(id) ON DELETE CASCADE,
    fecha           DATE            NOT NULL,
    hora            TIME,
    estado          estado_tipo     NOT NULL,
    version         VARCHAR(200),
    precio          BIGINT,                     -- en COP (o USD); NULL si extracción falló
    moneda          CHAR(3),
    kilometraje     INTEGER,                    -- NULL si no disponible
    transmision     VARCHAR(100),
    color           VARCHAR(50),
    sunroof         BOOLEAN,                    -- NULL si desconocido
    error           VARCHAR(500),
    UNIQUE (anuncio_id, fecha)
);

CREATE INDEX IF NOT EXISTS idx_obs_anuncio  ON observacion(anuncio_id);
CREATE INDEX IF NOT EXISTS idx_obs_fecha    ON observacion(fecha DESC);
CREATE INDEX IF NOT EXISTS idx_obs_precio   ON observacion(precio) WHERE precio IS NOT NULL;

-- ─── TABLA 4: cambio ──────────────────────────────────────────────────────────
-- Diferencias detectadas entre observaciones consecutivas de un mismo anuncio.
-- Permite consultar cambios de precio, km, versión sin recalcular con LAG().
CREATE TABLE IF NOT EXISTS cambio (
    id              SERIAL          PRIMARY KEY,
    anuncio_id      INTEGER         NOT NULL REFERENCES anuncio(id) ON DELETE CASCADE,
    fecha           DATE            NOT NULL,   -- fecha en que se detectó el cambio
    campo           VARCHAR(50)     NOT NULL,   -- 'precio', 'kilometraje', 'version'
    valor_anterior  TEXT,
    valor_nuevo     TEXT,
    UNIQUE (anuncio_id, fecha, campo)
);

CREATE INDEX IF NOT EXISTS idx_cambio_anuncio ON cambio(anuncio_id);
CREATE INDEX IF NOT EXISTS idx_cambio_fecha   ON cambio(fecha DESC);

-- ─── TABLA 5: log_ejecucion ───────────────────────────────────────────────────
-- Un registro por etapa del pipeline por día de ejecución.
-- run_id = fecha del día en Bogota (YYYY-MM-DD); agrupa todas las etapas del día.
-- etapas: 'scraper', 'loader', 'analisis', 'correo'
CREATE TABLE IF NOT EXISTS log_ejecucion (
    id                      SERIAL          PRIMARY KEY,
    run_id                  VARCHAR(50)     NOT NULL,       -- ej: '2026-11-16'
    etapa                   VARCHAR(50)     NOT NULL,
    fecha_inicio            TIMESTAMPTZ     NOT NULL,
    fecha_fin               TIMESTAMPTZ,
    duracion_seg            NUMERIC(10,2),
    estado                  VARCHAR(20)     NOT NULL DEFAULT 'iniciado',
    -- Métricas del scraper
    enlaces_encontrados     INTEGER,
    descargas_ok            INTEGER,
    descargas_fallidas      INTEGER,
    descargas_pendientes    INTEGER,
    -- Métricas del loader
    anuncios_nuevos         INTEGER,        -- primera vez que el sistema los ve
    registros_actualizados  INTEGER,        -- anuncios con cambio de precio/km/version
    registros_sin_cambio    INTEGER,
    no_encontrados          INTEGER,
    -- Estado general de la BD
    anuncios_unicos_total   INTEGER,
    tamanio_bd_mb           NUMERIC(10,2),
    -- Correo (punto 7)
    correo_estado           VARCHAR(50),
    -- Texto libre
    mensaje                 TEXT,
    detalle_error           TEXT,
    UNIQUE (run_id, etapa)   -- re-ejecutar el mismo día actualiza la fila, no duplica
);

CREATE INDEX IF NOT EXISTS idx_log_run_id ON log_ejecucion(run_id);
CREATE INDEX IF NOT EXISTS idx_log_fecha  ON log_ejecucion(fecha_inicio DESC);
