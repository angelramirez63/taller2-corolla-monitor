"""
Loader — carga los CSVs del día en PostgreSQL.
Taller 2 — Maestría Uniandes

Uso:
    python db/loader.py                      # carga el CSV más reciente
    python db/loader.py --fecha 2026-11-09   # carga un CSV específico

La cadena de conexión se lee de DATABASE_URL en .env o del entorno.
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path

# Asegurar que el directorio raíz del proyecto esté en sys.path
# para que 'from db.logger import ...' funcione al correr el script directamente
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from db.logger import RunLogger, db_size_mb

# ── Configuración ──────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")
DATA_DIR = Path(__file__).parent.parent / "datos"

CAMPOS_CAMBIO = ["precio", "kilometraje", "version"]


# ── Helpers de conversión ──────────────────────────────────────────────────────
def _str(val: str):
    return val.strip() if val and val.strip() else None

def _int(val: str):
    v = _str(val)
    return int(v) if v and v.isdigit() else None

def _bool_sunroof(val: str):
    if val == "Sí":   return True
    if val == "No":   return False
    return None

def _ts(fecha: str, hora: str):
    f, h = _str(fecha), _str(hora)
    if not f or not h:
        return None
    try:
        return datetime.strptime(f"{f} {h}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def _date(val: str):
    v = _str(val)
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None

def _extract_uuid(url: str):
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", url)
    return m.group(0) if m else None


# ── Carga de CSVs ──────────────────────────────────────────────────────────────
def find_csv(prefix: str, fecha: str = None) -> Path:
    if fecha:
        p = DATA_DIR / f"{prefix}_{fecha}.csv"
        if not p.exists():
            raise FileNotFoundError(f"No existe: {p}")
        return p
    # Más reciente
    files = sorted(DATA_DIR.glob(f"{prefix}_*.csv"), reverse=True)
    if not files:
        raise FileNotFoundError(f"No hay archivos {prefix}_*.csv en {DATA_DIR}")
    return files[0]


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Upserts ────────────────────────────────────────────────────────────────────
def upsert_vendedor(cur, nombre: str, departamento: str) -> int | None:
    if not nombre:
        return None
    cur.execute("""
        INSERT INTO vendedor (nombre, departamento)
        VALUES (%s, %s)
        ON CONFLICT (nombre, departamento) DO NOTHING
        RETURNING id
    """, (nombre, departamento))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("SELECT id FROM vendedor WHERE nombre = %s AND departamento IS NOT DISTINCT FROM %s",
                (nombre, departamento))
    return cur.fetchone()[0]


def upsert_anuncio(cur, url: str, marca, linea, año, ciudad,
                   vendedor_id, run_date: date) -> int:
    cur.execute("""
        INSERT INTO anuncio (url, marca, linea, año, ciudad, vendedor_id,
                             primera_vista, ultima_vista)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (url) DO UPDATE SET
            ultima_vista = GREATEST(anuncio.ultima_vista, EXCLUDED.ultima_vista),
            primera_vista = LEAST(anuncio.primera_vista, EXCLUDED.primera_vista),
            vendedor_id = COALESCE(anuncio.vendedor_id, EXCLUDED.vendedor_id)
        RETURNING id
    """, (url, marca, linea, año, ciudad, vendedor_id, run_date, run_date))
    return cur.fetchone()[0]


def upsert_observacion(cur, anuncio_id: int, row: dict, run_date: date):
    cur.execute("""
        INSERT INTO observacion
            (anuncio_id, fecha, hora, estado,
             version, precio, moneda, kilometraje,
             transmision, color, sunroof, error)
        VALUES (%s, %s, %s, %s::estado_tipo,
                %s, %s, %s, %s,
                %s, %s, %s, %s)
        ON CONFLICT (anuncio_id, fecha) DO UPDATE SET
            hora        = EXCLUDED.hora,
            estado      = EXCLUDED.estado,
            version     = EXCLUDED.version,
            precio      = EXCLUDED.precio,
            moneda      = EXCLUDED.moneda,
            kilometraje = EXCLUDED.kilometraje,
            transmision = EXCLUDED.transmision,
            color       = EXCLUDED.color,
            sunroof     = EXCLUDED.sunroof,
            error       = EXCLUDED.error
    """, (
        anuncio_id,
        run_date,
        _str(row.get("hora_descarga")),
        _str(row.get("estado")) or "sin intentar",
        _str(row.get("version")),
        _int(row.get("precio")),
        _str(row.get("moneda")),
        _int(row.get("kilometraje")),
        _str(row.get("transmision")),
        _str(row.get("color")),
        _bool_sunroof(row.get("sunroof", "")),
        _str(row.get("error")),
    ))


def insert_cambios(cur, anuncio_id: int, run_date: date, row: dict):
    """Compara con la observación anterior y registra diferencias."""
    for campo in CAMPOS_CAMBIO:
        valor_nuevo = _str(row.get(campo))
        cur.execute("""
            SELECT %s::text
            FROM observacion
            WHERE anuncio_id = %s
              AND fecha < %s
              AND estado = 'descargado'
            ORDER BY fecha DESC
            LIMIT 1
        """, (campo, anuncio_id, run_date))
        prev = cur.fetchone()
        if prev is None:
            continue
        valor_anterior = prev[0]
        if valor_anterior != valor_nuevo:
            cur.execute("""
                INSERT INTO cambio (anuncio_id, fecha, campo, valor_anterior, valor_nuevo)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (anuncio_id, fecha, campo) DO NOTHING
            """, (anuncio_id, run_date, campo, valor_anterior, valor_nuevo))


def marcar_no_encontrados(cur, links_urls: set, run_date: date) -> int:
    """
    Inserta una observación 'no_encontrado' para cada anuncio que:
      - fue visto en días anteriores (primera_vista < run_date)
      - NO aparece en el conjunto de URLs del día actual (links_urls)
      - aún no tiene observación para run_date

    Semántica y distinción entre estados:
      descargado    → el anuncio estaba en los links Y la descarga fue exitosa
      fallido       → el anuncio estaba en los links PERO la descarga falló
                      (disponibilidad desconocida; el vehículo puede seguir en oferta)
      no_encontrado → la búsqueda se ejecutó completa y el anuncio NO apareció
                      (posiblemente vendido/retirado, pero no hay certeza)

    Un fallo de descarga no prueba que el vehículo salió de oferta.
    Dejar de encontrar un anuncio tampoco prueba que se vendió.
    Idempotente: ON CONFLICT DO NOTHING garantiza que re-procesar el mismo
    archivo no duplica observaciones.
    """
    cur.execute(
        "SELECT id, url FROM anuncio WHERE primera_vista < %s",
        (run_date,),
    )
    previos = cur.fetchall()

    count = 0
    for anuncio_id, url in previos:
        if url not in links_urls:
            cur.execute(
                """
                INSERT INTO observacion (anuncio_id, fecha, estado)
                VALUES (%s, %s, 'no_encontrado')
                ON CONFLICT (anuncio_id, fecha) DO NOTHING
                """,
                (anuncio_id, run_date),
            )
            count += 1
    return count


# ── Flujo principal ────────────────────────────────────────────────────────────
def main(fecha: str = None):
    datos_path = find_csv("datos", fecha)
    links_path = find_csv("links", fecha)

    # Inferir fecha de ejecución del nombre del archivo
    m = re.search(r"(\d{4}-\d{2}-\d{2})", datos_path.name)
    run_date = datetime.strptime(m.group(1), "%Y-%m-%d").date() if m else date.today()

    print(f"\n{'='*60}")
    print(f"Loader Carroya — {run_date}")
    print(f"  datos : {datos_path.name}")
    print(f"  links : {links_path.name}")
    print(f"{'='*60}\n")

    datos_rows = load_csv(datos_path)
    links_rows = load_csv(links_path)
    links_urls = {_str(r.get("url")) for r in links_rows if _str(r.get("url"))}

    print(f"  Filas en datos CSV : {len(datos_rows)}")
    print(f"  URLs en links CSV  : {len(links_urls)}")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL no está definida en .env ni en el entorno.")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    insertados = fallidos = omitidos = no_enc = 0

    logger = RunLogger(conn=conn)
    with logger.etapa("loader") as log:
        with conn:
            with conn.cursor() as cur:
                for row in datos_rows:
                    url = _str(row.get("url"))
                    if not url:
                        omitidos += 1
                        continue

                    try:
                        # 1. Vendedor
                        vid = upsert_vendedor(
                            cur,
                            _str(row.get("vendedor_nombre")),
                            _str(row.get("vendedor_departamento")),
                        )

                        # 2. Anuncio
                        aid = upsert_anuncio(
                            cur, url,
                            _str(row.get("marca")),
                            _str(row.get("linea")),
                            _int(row.get("año")),
                            _str(row.get("ciudad")),
                            vid,
                            run_date,
                        )

                        # 3. Observación
                        upsert_observacion(cur, aid, row, run_date)

                        # 4. Cambios (solo si fue descargado hoy)
                        if _str(row.get("estado")) == "descargado":
                            insert_cambios(cur, aid, run_date, row)

                        insertados += 1

                    except Exception as exc:
                        conn.rollback()
                        print(f"  ✗ Error en {url}: {exc}")
                        fallidos += 1
                        conn.autocommit = False

                # 5. Marcar anuncios de días anteriores que no aparecieron hoy
                no_enc = marcar_no_encontrados(cur, links_urls, run_date)

                # 6. Métricas para el log
                cur.execute(
                    "SELECT COUNT(*) FROM anuncio WHERE primera_vista = %s", (run_date,)
                )
                anuncios_nuevos = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(DISTINCT anuncio_id) FROM cambio WHERE fecha = %s",
                    (run_date,),
                )
                con_cambios = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM anuncio")
                total_anuncios = cur.fetchone()[0]

        log.set(
            anuncios_nuevos        = anuncios_nuevos,
            registros_actualizados = con_cambios,
            registros_sin_cambio   = max(0, insertados - anuncios_nuevos - con_cambios),
            no_encontrados         = no_enc,
            anuncios_unicos_total  = total_anuncios,
            tamanio_bd_mb          = db_size_mb(conn),
        )

    conn.close()

    print(f"\nResumen:")
    print(f"  ✓ Descargados      : {insertados}")
    print(f"  ✗ Fallidos (error) : {fallidos}")
    print(f"  ⊘ No encontrados   : {no_enc}  (ausentes en búsqueda completa — no confirma venta)")
    print(f"  — Omitidos         : {omitidos}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carga CSVs de Carroya en PostgreSQL")
    parser.add_argument("--fecha", help="Fecha YYYY-MM-DD del CSV a cargar (default: más reciente)")
    args = parser.parse_args()
    main(args.fecha)
