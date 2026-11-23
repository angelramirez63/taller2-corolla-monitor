"""
Reconstruye la tabla `cambio` a partir del historial de observaciones.

Uso:
    python db/recalcular_cambios.py                    # recalcula en la BD
    python db/recalcular_cambios.py --correccion out.csv  # además compara con el log

Hasta el commit que corrigió db/loader.py, insert_cambios() leía el nombre de la
columna como texto ('precio', 'kilometraje', 'version') en vez de su valor, así
que registraba un cambio falso por campo cada vez que un anuncio se volvía a ver.
Como `observacion` guarda el historial completo, los cambios correctos se pueden
recalcular exactamente: para cada anuncio se compara cada observación descargada
con la descargada inmediatamente anterior (misma regla que el loader).

La tabla `log_ejecucion` no se modifica (es la evidencia de lo que pasó); con
--correccion se genera un CSV con el valor registrado y el valor corregido de
`registros_actualizados` por día.
"""

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql

from db.loader import CAMPOS_CAMBIO

load_dotenv(Path(__file__).parent.parent / ".env")

_INSERT = """
    INSERT INTO cambio (anuncio_id, fecha, campo, valor_anterior, valor_nuevo)
    SELECT anuncio_id, fecha, %s, anterior, nuevo
    FROM (
        SELECT anuncio_id, fecha,
               {col}::text AS nuevo,
               LAG({col}::text) OVER w AS anterior,
               ROW_NUMBER() OVER w AS n
        FROM observacion
        WHERE estado = 'descargado'
        WINDOW w AS (PARTITION BY anuncio_id ORDER BY fecha)
    ) t
    WHERE n > 1 AND anterior IS DISTINCT FROM nuevo
"""


def recalcular(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cambio")
        antes = cur.fetchone()[0]
        cur.execute("DELETE FROM cambio")
        for campo in CAMPOS_CAMBIO:
            cur.execute(sql.SQL(_INSERT).format(col=sql.Identifier(campo)), (campo,))
        cur.execute("SELECT campo, COUNT(*) FROM cambio GROUP BY campo ORDER BY campo")
        por_campo = dict(cur.fetchall())
    conn.commit()
    return {"antes": antes, "despues": sum(por_campo.values()), "por_campo": por_campo}


def correccion_log(conn, salida: Path):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT l.run_id, l.registros_actualizados, l.registros_sin_cambio,
                   COALESCE(c.n, 0) AS actualizados_corregido,
                   (SELECT COUNT(*) FROM observacion o
                    WHERE o.fecha = l.run_id::date AND o.estado = 'descargado') AS descargados,
                   (SELECT COUNT(*) FROM anuncio a WHERE a.primera_vista = l.run_id::date) AS nuevos
            FROM log_ejecucion l
            LEFT JOIN (SELECT fecha, COUNT(DISTINCT anuncio_id) AS n FROM cambio GROUP BY fecha) c
                   ON c.fecha = l.run_id::date
            WHERE l.etapa = 'loader'
            ORDER BY l.run_id
        """)
        filas = cur.fetchall()
    with open(salida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "registros_actualizados_log", "registros_sin_cambio_log",
                    "registros_actualizados_corregido", "registros_sin_cambio_corregido"])
        for run_id, act_log, sin_log, act_ok, descargados, nuevos in filas:
            sin_ok = max(0, descargados - nuevos - act_ok)
            w.writerow([run_id, act_log, sin_log, act_ok, sin_ok])
            print(f"  {run_id}: actualizados {act_log} → {act_ok} · sin cambio {sin_log} → {sin_ok}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Recalcula la tabla cambio desde observacion")
    ap.add_argument("--correccion", type=Path, help="CSV con la corrección de registros_actualizados")
    a = ap.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        r = recalcular(conn)
        print(f"Cambios: {r['antes']} registrados → {r['despues']} recalculados {r['por_campo']}")
        if a.correccion:
            correccion_log(conn, a.correccion)
            print(f"Corrección del log guardada en {a.correccion}")
    finally:
        conn.close()
