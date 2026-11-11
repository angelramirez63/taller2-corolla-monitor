"""
Orquestador del workflow diario — Taller 2 (punto 4).

    python pipeline.py recoleccion   # 8:00 a. m.: búsqueda/descarga → consolidación → análisis
    python pipeline.py correo        # 11:00 a. m.: envío del reporte (independiente)

Cada etapa corre como un proceso separado: si una falla o se cuelga, las
siguientes igual se intentan y el correo de las 11:00 reporta lo ocurrido.
La salida de cada etapa se agrega a logs/pipeline_YYYY-MM-DD.log (hora de
Bogotá), complementando la tabla log_ejecucion de la base de datos.

Las ejecuciones fuera del periodo evaluado (16–21 nov 2026) se marcan como PRUEBA.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
BOGOTA   = ZoneInfo("America/Bogota")
PY       = sys.executable

PERIODO_INICIO = date(2026, 11, 16)
PERIODO_FIN    = date(2026, 11, 21)

# Límites de tiempo por etapa (segundos)
TIMEOUT_SCRAPER  = 150 * 60
TIMEOUT_LOADER   = 15 * 60
TIMEOUT_ANALISIS = 15 * 60
TIMEOUT_CORREO   = 10 * 60


def es_prueba(fecha: date) -> bool:
    return not PERIODO_INICIO <= fecha <= PERIODO_FIN


def _ahora() -> datetime:
    return datetime.now(BOGOTA)


class ArchivoLog:
    def __init__(self, fecha: date):
        LOGS_DIR.mkdir(exist_ok=True)
        self.ruta = LOGS_DIR / f"pipeline_{fecha.isoformat()}.log"

    def escribir(self, texto: str):
        marca = _ahora().strftime("%Y-%m-%d %H:%M:%S %z")
        with open(self.ruta, "a", encoding="utf-8") as f:
            for linea in texto.rstrip().splitlines() or [""]:
                f.write(f"[{marca}] {linea}\n")
        print(texto)


def _marcar_timeout(fecha: date, etapa: str):
    """Si un proceso se mató por tiempo límite, su fila del log quedó en 'iniciado'."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from db.logger import RunLogger
        logger = RunLogger(run_id=fecha.isoformat())
        try:
            with logger.conn.cursor() as cur:
                cur.execute("""
                    UPDATE log_ejecucion
                    SET estado = 'error', fecha_fin = %s,
                        duracion_seg = EXTRACT(EPOCH FROM (%s - fecha_inicio)),
                        detalle_error = 'Proceso detenido: excedió el tiempo límite'
                    WHERE run_id = %s AND etapa = %s AND estado = 'iniciado'
                """, (_ahora(), _ahora(), fecha.isoformat(), etapa))
            logger.conn.commit()
        finally:
            logger.close()
    except Exception as exc:
        print(f"[WARN] No se pudo marcar el timeout en la BD: {exc}")


def correr_etapa(log: ArchivoLog, fecha: date, nombre: str, etapa_bd: str,
                 args: list[str], timeout: int) -> bool:
    log.escribir(f"── {nombre}: inicio  ({' '.join(args)})")
    inicio = _ahora()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run([PY, *args], cwd=BASE_DIR, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
        salida = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0
        resultado = f"código de salida {proc.returncode}"
    except subprocess.TimeoutExpired as exc:
        salida = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        ok = False
        resultado = f"tiempo límite de {timeout // 60} min excedido"
        _marcar_timeout(fecha, etapa_bd)
    if salida.strip():
        log.escribir(salida)
    dur = (_ahora() - inicio).total_seconds()
    log.escribir(f"── {nombre}: {'OK' if ok else 'FALLÓ'} ({resultado}, {dur:.0f} s)")
    return ok


def recoleccion(fecha: date, log: ArchivoLog) -> bool:
    ok_scraper = correr_etapa(log, fecha, "Búsqueda y descarga", "scraper",
                              ["scraper_carroya.py"], TIMEOUT_SCRAPER)

    datos = BASE_DIR / "datos" / f"datos_{fecha.isoformat()}.csv"
    if datos.exists():
        ok_loader = correr_etapa(log, fecha, "Consolidación", "loader",
                                 ["db/loader.py", "--fecha", fecha.isoformat()], TIMEOUT_LOADER)
    else:
        log.escribir(f"── Consolidación: OMITIDA, no existe {datos.name}")
        ok_loader = False

    # El análisis corre aunque fallen las etapas anteriores: reporta lo que haya
    ok_analisis = correr_etapa(log, fecha, "Análisis de precios", "analisis",
                               ["analisis/gangas.py", "--fecha", fecha.isoformat()], TIMEOUT_ANALISIS)
    return ok_scraper and ok_loader and ok_analisis


def correo(fecha: date, log: ArchivoLog) -> bool:
    args = ["correo/reporte.py", "--fecha", fecha.isoformat()]
    if es_prueba(fecha):
        args.append("--prueba")
    return correr_etapa(log, fecha, "Correo diario", "correo", args, TIMEOUT_CORREO)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Workflow diario del monitor Corolla")
    ap.add_argument("tarea", choices=["recoleccion", "correo"])
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy en Bogotá)")
    a = ap.parse_args()

    fecha = datetime.strptime(a.fecha, "%Y-%m-%d").date() if a.fecha else _ahora().date()
    log = ArchivoLog(fecha)
    log.escribir(f"═══ {a.tarea.upper()} · {fecha} · {'PRUEBA' if es_prueba(fecha) else 'PERIODO EVALUADO'} "
                 f"· python {PY}")
    ok = recoleccion(fecha, log) if a.tarea == "recoleccion" else correo(fecha, log)
    log.escribir(f"═══ {a.tarea.upper()} terminó: {'OK' if ok else 'CON FALLAS'}\n")
    sys.exit(0 if ok else 1)
