"""
Logger estructurado para el pipeline Carroya.

Registra cada etapa en la tabla log_ejecucion de PostgreSQL.
Zona horaria explícita: America/Bogota en todos los timestamps.

Uso básico:
    from db.logger import RunLogger

    logger = RunLogger()                        # run_id = fecha de hoy en Bogota
    with logger.etapa("scraper") as log:
        resultado = hacer_scraping()
        log.set(enlaces_encontrados=60, descargas_ok=58, descargas_fallidas=2)
    logger.close()
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
from dotenv import load_dotenv

BOGOTA = ZoneInfo("America/Bogota")

# Columnas métricas válidas para log.set()
_COLS = frozenset({
    "enlaces_encontrados", "descargas_ok", "descargas_fallidas",
    "descargas_pendientes", "anuncios_nuevos", "registros_actualizados",
    "registros_sin_cambio", "no_encontrados", "anuncios_unicos_total",
    "tamanio_bd_mb", "correo_estado", "mensaje", "detalle_error",
})


def _now() -> datetime:
    return datetime.now(BOGOTA)


def _connect() -> psycopg2.extensions.connection:
    load_dotenv(Path(__file__).parent.parent / ".env")
    return psycopg2.connect(os.environ["DATABASE_URL"])


def db_size_mb(conn) -> float:
    """Tamaño de la BD en MB."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database()) / 1048576.0")
        return round(float(cur.fetchone()[0]), 3)


class EtapaLog:
    """
    Context manager para registrar una etapa del pipeline.

    Al entrar inserta la fila con estado='iniciado'.
    Al salir actualiza fecha_fin, duración y estado ('ok' o 'error').
    No suprime excepciones: si la etapa falla, el error queda registrado
    y la excepción se propaga normalmente.
    """

    def __init__(self, conn, run_id: str, etapa: str):
        self._conn   = conn
        self._run_id = run_id
        self._etapa  = etapa
        self._log_id: int | None = None
        self._inicio: datetime | None = None
        self._stats: dict = {}

    def __enter__(self) -> "EtapaLog":
        self._inicio = _now()
        with self._conn.cursor() as cur:
            # ON CONFLICT permite re-ejecutar el mismo día sin duplicar filas
            cur.execute("""
                INSERT INTO log_ejecucion (run_id, etapa, fecha_inicio, estado)
                VALUES (%s, %s, %s, 'iniciado')
                ON CONFLICT (run_id, etapa) DO UPDATE
                    SET fecha_inicio  = EXCLUDED.fecha_inicio,
                        estado        = 'iniciado',
                        fecha_fin     = NULL,
                        duracion_seg  = NULL,
                        detalle_error = NULL
                RETURNING id
            """, (self._run_id, self._etapa, self._inicio))
            self._log_id = cur.fetchone()[0]
        self._conn.commit()
        ts = self._inicio.strftime("%Y-%m-%d %H:%M:%S %Z")
        print(f"[LOG {ts}] {self._etapa}: iniciado")
        return self

    def set(self, **kwargs):
        """
        Acumula métricas durante la ejecución de la etapa.
        Se persisten en la BD al salir del context manager.
        Solo acepta nombres de columnas conocidos (ignora el resto silenciosamente).
        """
        for k, v in kwargs.items():
            if k in _COLS:
                self._stats[k] = v

    def __exit__(self, exc_type, exc_val, exc_tb):
        fin    = _now()
        dur    = round((fin - self._inicio).total_seconds(), 2)
        estado = "error" if exc_type else "ok"

        updates: dict = {
            "fecha_fin":    fin,
            "duracion_seg": dur,
            "estado":       estado,
            **self._stats,
        }
        if exc_val:
            updates["detalle_error"] = str(exc_val)[:500]

        sets = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [self._log_id]
        with self._conn.cursor() as cur:
            cur.execute(f"UPDATE log_ejecucion SET {sets} WHERE id = %s", vals)
        self._conn.commit()

        emoji = "✓" if estado == "ok" else "✗"
        ts    = fin.strftime("%H:%M:%S %Z")
        print(f"[LOG {ts}] {self._etapa}: {emoji} ({dur:.1f}s)")
        return False  # no suprime excepciones


class RunLogger:
    """
    Gestor de una ejecución completa del pipeline.

    run_id por defecto: fecha del día en Bogota (YYYY-MM-DD).
    Todas las etapas del mismo día comparten el mismo run_id,
    lo que permite reconstruir la ejecución completa con una sola consulta.

    Si conn=None abre su propia conexión (y la cierra en close()).
    Si se pasa conn externa, close() NO la cierra.
    """

    def __init__(self, run_id: str | None = None, conn=None):
        self._own_conn = conn is None
        self.conn      = conn or _connect()
        self.run_id    = run_id or _now().strftime("%Y-%m-%d")

    def etapa(self, nombre: str) -> EtapaLog:
        """Devuelve un EtapaLog listo para usar como context manager."""
        return EtapaLog(self.conn, self.run_id, nombre)

    def close(self):
        if self._own_conn:
            self.conn.close()
