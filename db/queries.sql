-- ══════════════════════════════════════════════════════════════════════════════
--  CARROYA — Consultas del Taller 2 (Punto 2)
-- ══════════════════════════════════════════════════════════════════════════════

-- ─── Q1: Anuncios únicos encontrados durante el periodo ───────────────────────
SELECT
    a.url,
    a.marca,
    a.linea,
    a.año,
    a.ciudad,
    v.nombre        AS vendedor,
    v.departamento  AS vendedor_departamento,
    a.primera_vista,
    a.ultima_vista,
    (a.ultima_vista - a.primera_vista) AS dias_en_mercado
FROM anuncio a
LEFT JOIN vendedor v ON v.id = a.vendedor_id
ORDER BY a.primera_vista;


-- ─── Q2: Información más reciente de cada anuncio ────────────────────────────
SELECT DISTINCT ON (o.anuncio_id)
    a.url,
    a.año,
    a.ciudad,
    o.fecha         AS ultima_fecha,
    o.version,
    o.precio,
    o.moneda,
    o.kilometraje,
    o.transmision,
    o.color,
    o.sunroof,
    o.estado
FROM observacion o
JOIN anuncio a ON a.id = o.anuncio_id
WHERE o.estado = 'descargado'
ORDER BY o.anuncio_id, o.fecha DESC;


-- ─── Q3a: Observaciones diarias por anuncio ───────────────────────────────────
SELECT
    a.url,
    o.fecha,
    o.precio,
    o.kilometraje,
    o.transmision,
    o.color,
    o.version,
    o.estado
FROM observacion o
JOIN anuncio a ON a.id = o.anuncio_id
ORDER BY a.url, o.fecha;


-- ─── Q3b: Cambios detectados entre días ──────────────────────────────────────
SELECT
    a.url,
    c.fecha,
    c.campo,
    c.valor_anterior,
    c.valor_nuevo
FROM cambio c
JOIN anuncio a ON a.id = c.anuncio_id
ORDER BY a.url, c.fecha, c.campo;


-- ─── Q4: Primera y última fecha de observación por anuncio ───────────────────
SELECT
    url,
    primera_vista,
    ultima_vista,
    (ultima_vista - primera_vista) AS dias_en_mercado
FROM anuncio
ORDER BY primera_vista;


-- ─── Q5: Disponibilidad y timestamps de cada descarga ────────────────────────
SELECT
    a.url,
    o.fecha,
    o.hora,
    o.estado,
    o.error
FROM observacion o
JOIN anuncio a ON a.id = o.anuncio_id
ORDER BY a.url, o.fecha;


-- ─── Q6: Log consolidado del periodo ─────────────────────────────────────────
-- Reconstruye qué ocurrió cada día en cada etapa del pipeline.
SELECT
    run_id,
    etapa,
    fecha_inicio AT TIME ZONE 'America/Bogota'   AS inicio_bog,
    fecha_fin    AT TIME ZONE 'America/Bogota'   AS fin_bog,
    duracion_seg,
    estado,
    enlaces_encontrados,
    descargas_ok,
    descargas_fallidas,
    descargas_pendientes,
    anuncios_nuevos,
    registros_actualizados,
    registros_sin_cambio,
    no_encontrados,
    anuncios_unicos_total,
    tamanio_bd_mb,
    correo_estado,
    detalle_error
FROM log_ejecucion
ORDER BY run_id, etapa;
