"""
Correo diario — Taller 2 (punto 7).

Uso:
    python correo/reporte.py                    # arma y envía el reporte de hoy
    python correo/reporte.py --prueba           # asunto marcado [PRUEBA]
    python correo/reporte.py --no-enviar        # solo guarda la copia (.html/.eml)
    python correo/reporte.py --fecha 2026-11-10 # reporte de otra fecha

El correo se envía siempre, aunque falle parte del proceso: si la base de datos
no responde, falta el análisis o una etapa sigue en curso, el reporte lo dice.

Variables en .env:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_TO (separados por coma)

Cada correo se guarda en correos/enviados/ como .eml (mensaje completo, con
fecha y hora) y .html (legible en el navegador, con la gráfica incrustada).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import smtplib
import ssl
import sys
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

from db.logger import BOGOTA, RunLogger

BASE_DIR     = Path(__file__).parent.parent
REPORTES_DIR = BASE_DIR / "reportes"
ENVIADOS_DIR = BASE_DIR / "correos" / "enviados"
load_dotenv(BASE_DIR / ".env")

ETAPAS = [
    ("scraper",  "Búsqueda y descarga (8:00 a. m.)"),
    ("loader",   "Consolidación en la base"),
    ("analisis", "Análisis de precios y gangas"),
]

# Paleta (guía de visualización, modo claro); estados siempre con ícono + texto
C_TINTA, C_TINTA_2, C_MUTED = "#0b0b0b", "#52514e", "#898781"
C_FONDO, C_SUPERFICIE, C_BORDE = "#f9f9f7", "#fcfcfb", "#e1e0d9"
C_ACENTO = "#eb6834"
ESTADO_GENERAL = {
    "completo":    ("✔", "Proceso completo",    "#0ca30c", "#ffffff"),
    "incompleto":  ("⚠", "Proceso incompleto",  "#fab219", "#0b0b0b"),
    "con_errores": ("✖", "Proceso con errores", "#d03b3b", "#ffffff"),
}
ESTADO_ETAPA = {
    "ok":        ("✔", "Completada"),
    "error":     ("✖", "Falló"),
    "iniciado":  ("⏳", "En curso"),
    None:        ("—", "No ejecutada"),
}


# ── Utilidades ────────────────────────────────────────────────────────────────
def fmt_cop(v) -> str:
    return "—" if v is None else "$" + f"{int(round(v)):,}".replace(",", ".")


def fmt_num(v) -> str:
    return "—" if v is None else f"{int(v):,}".replace(",", ".")


def fmt_hora(ts) -> str:
    return "—" if ts is None else ts.astimezone(BOGOTA).strftime("%H:%M:%S")


# ── Recolección de información ───────────────────────────────────────────────
def leer_etapas(fecha: date) -> tuple[dict, str | None]:
    """Filas de log_ejecucion del día. Si la BD falla, devuelve ({}, error)."""
    try:
        logger = RunLogger(run_id=fecha.isoformat())
    except Exception as exc:
        return {}, f"No se pudo conectar a la base de datos: {exc}"
    try:
        with logger.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM log_ejecucion WHERE run_id = %s", (fecha.isoformat(),))
            return {r["etapa"]: dict(r) for r in cur.fetchall()}, None
    except Exception as exc:
        return {}, f"No se pudo leer el log de ejecución: {exc}"
    finally:
        logger.close()


def leer_analisis(fecha: date) -> tuple[dict | None, Path | None]:
    carpeta = REPORTES_DIR / fecha.isoformat()
    archivo = carpeta / "analisis.json"
    if not archivo.exists():
        return None, None
    analisis = json.loads(archivo.read_text(encoding="utf-8"))
    grafica = carpeta / analisis["grafica"] if analisis.get("grafica") else None
    return analisis, grafica if grafica and grafica.exists() else None


def evaluar_estado(etapas: dict, error_bd: str | None) -> tuple[str, list[str], list[str]]:
    """Devuelve (estado_general, errores, pendientes)."""
    errores, pendientes = [], []
    if error_bd:
        errores.append(error_bd)
    for clave, nombre in ETAPAS:
        fila = etapas.get(clave)
        if fila is None:
            pendientes.append(f"{nombre}: no se ejecutó o no dejó registro.")
        elif fila["estado"] == "iniciado":
            pendientes.append(f"{nombre}: seguía en curso al momento del envío "
                              "(las cifras pueden estar incompletas).")
        elif fila["estado"] == "error":
            errores.append(f"{nombre}: {fila.get('detalle_error') or 'error sin detalle'}")
    scraper = etapas.get("scraper") or {}
    if scraper.get("descargas_fallidas"):
        errores.append(f"{scraper['descargas_fallidas']} anuncio(s) no se pudieron descargar; "
                       "su disponibilidad es desconocida.")
    if scraper.get("descargas_pendientes"):
        pendientes.append(f"{scraper['descargas_pendientes']} anuncio(s) quedaron sin intentar.")

    if any(etapas.get(c, {}).get("estado") == "error" for c, _ in ETAPAS) or error_bd:
        estado = "con_errores"
    elif pendientes:
        estado = "incompleto"
    else:
        estado = "completo"
    return estado, errores, pendientes


# ── Plantilla HTML ────────────────────────────────────────────────────────────
def _seccion(titulo: str, cuerpo: str) -> str:
    return (f'<tr><td style="padding:22px 28px 6px 28px;">'
            f'<h2 style="margin:0 0 10px 0;font-size:17px;color:{C_TINTA};">{titulo}</h2>'
            f'{cuerpo}</td></tr>')


def _tabla(filas: list[tuple[str, str]], ancho_etiqueta: str = "58%") -> str:
    celdas = "".join(
        f'<tr><td style="padding:6px 0;border-bottom:1px solid {C_BORDE};color:{C_TINTA_2};'
        f'width:{ancho_etiqueta};">{escape(k)}</td>'
        f'<td style="padding:6px 0;border-bottom:1px solid {C_BORDE};color:{C_TINTA};'
        f'font-weight:600;text-align:right;">{escape(v)}</td></tr>'
        for k, v in filas
    )
    return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{celdas}</table>'


def _parrafo(texto: str, color: str = C_TINTA_2) -> str:
    return f'<p style="margin:8px 0;color:{color};line-height:1.5;">{texto}</p>'


def _caracteristicas(g: dict) -> str:
    partes = [f"{fmt_num(g.get('kilometraje'))} km"]
    if g.get("hibrido") is not None:
        partes.append("híbrido" if g["hibrido"] else "gasolina")
    if g.get("color"):
        partes.append(g["color"].lower())
    partes.append("con sunroof" if g.get("sunroof") else "sin sunroof mencionado")
    if g.get("blindado"):
        partes.append("blindado")
    if g.get("ciudad"):
        partes.append(g["ciudad"])
    return " · ".join(partes)


def _tarjeta_ganga(g: dict) -> str:
    titulo = f"Toyota Corolla {g.get('año') or ''} {g.get('version_norm') or ''}".strip()
    bajada = ""
    if g.get("bajada_precio"):
        b = g["bajada_precio"]
        bajada = _parrafo(f"Bajó de precio hoy: {fmt_cop(b['anterior'])} → {fmt_cop(b['nuevo'])}.",
                          C_TINTA)
    rango = g.get("rango_tipico") or [None, None]
    return f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="margin:0 0 14px 0;border:1px solid {C_BORDE};border-left:4px solid {C_ACENTO};
                  border-radius:6px;background:{C_SUPERFICIE};">
      <tr><td style="padding:14px 16px;">
        <a href="{escape(g['url'], quote=True)}" style="font-size:15px;font-weight:600;color:#1c5cab;">
          {escape(titulo)}</a>
        <div style="color:{C_TINTA_2};font-size:13px;margin-top:4px;">{escape(_caracteristicas(g))}</div>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:10px;">
          <tr>
            <td style="color:{C_MUTED};font-size:12px;">Precio publicado</td>
            <td style="color:{C_MUTED};font-size:12px;">Referencia del modelo</td>
            <td style="color:{C_MUTED};font-size:12px;">Diferencia</td>
          </tr>
          <tr>
            <td style="font-size:16px;font-weight:700;color:{C_TINTA};">{fmt_cop(g['precio'])}</td>
            <td style="font-size:16px;font-weight:700;color:{C_TINTA};">{fmt_cop(g['referencia'])}</td>
            <td style="font-size:16px;font-weight:700;color:{C_TINTA};">{f"{g['diferencia_pct']:+.1f}".replace(".", ",")} %</td>
          </tr>
          <tr><td></td><td style="color:{C_MUTED};font-size:12px;">
            rango típico {fmt_cop(rango[0])} – {fmt_cop(rango[1])}</td><td></td></tr>
        </table>
        {bajada}
        {_parrafo(escape(g.get('explicacion') or ''))}
      </td></tr>
    </table>"""


def construir_html(fecha: date, etapas: dict, analisis: dict | None, estado: str,
                   errores: list[str], pendientes: list[str], img_src: str | None,
                   prueba: bool) -> str:
    icono, etiqueta, fondo, tinta = ESTADO_GENERAL[estado]
    scraper = etapas.get("scraper") or {}
    loader  = etapas.get("loader") or {}

    # 1. Estado general y cifras del día
    resumen = _tabla([
        ("Enlaces encontrados",          fmt_num(scraper.get("enlaces_encontrados"))),
        ("Anuncios nuevos para el sistema", fmt_num(loader.get("anuncios_nuevos"))),
        ("Descargas exitosas",           fmt_num(scraper.get("descargas_ok"))),
        ("Descargas fallidas",           fmt_num(scraper.get("descargas_fallidas"))),
        ("Anuncios no encontrados hoy (vistos antes)", fmt_num(loader.get("no_encontrados"))),
        ("Anuncios únicos en la base",   fmt_num(loader.get("anuncios_unicos_total"))),
    ])

    # 2. Etapas
    filas_etapas = ""
    for clave, nombre in ETAPAS:
        f = etapas.get(clave)
        ico, txt = ESTADO_ETAPA.get(f["estado"] if f else None, ("?", f["estado"] if f else ""))
        dur = f"{float(f['duracion_seg']):.0f} s" if f and f.get("duracion_seg") is not None else "—"
        filas_etapas += (
            f'<tr><td style="padding:6px 0;border-bottom:1px solid {C_BORDE};color:{C_TINTA};">{escape(nombre)}</td>'
            f'<td style="padding:6px 0;border-bottom:1px solid {C_BORDE};color:{C_TINTA};">{ico} {txt}</td>'
            f'<td style="padding:6px 0;border-bottom:1px solid {C_BORDE};color:{C_TINTA_2};text-align:right;">'
            f'{fmt_hora(f["fecha_inicio"]) if f else "—"} · {dur}</td></tr>'
        )
    tabla_etapas = (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
                    f'{filas_etapas}</table>')
    avisos = ""
    if errores:
        avisos += _parrafo("<strong>Errores relevantes:</strong><br>" +
                           "<br>".join("• " + escape(e) for e in errores), C_TINTA)
    if pendientes:
        avisos += _parrafo("<strong>Etapas pendientes o incompletas:</strong><br>" +
                           "<br>".join("• " + escape(p) for p in pendientes), C_TINTA)
    if not errores and not pendientes:
        avisos = _parrafo("Sin errores ni etapas pendientes.")

    # 3. Distribución de precios
    if analisis is None:
        dist = _parrafo("El análisis de precios de hoy no se generó, por lo que no hay gráfica "
                        "ni detección de gangas. Revise la etapa de análisis arriba.", C_TINTA)
    else:
        s = analisis["estadisticas"]
        if img_src:
            dist = (f'<img src="{img_src}" width="100%" alt="Distribución de precios del día" '
                    f'style="display:block;max-width:100%;border:1px solid {C_BORDE};border-radius:6px;">')
        else:
            dist = _parrafo(f"Sin gráfica: {escape(analisis.get('motivo_sin_grafica') or 'no disponible')}.")
        if s.get("mediana"):
            dist += _parrafo(
                f"Se usó una observación por anuncio: los {s['n_precio_valido']} anuncios descargados hoy "
                f"con precio válido. La mitad cuesta entre <strong>{fmt_cop(s['q1'])}</strong> y "
                f"<strong>{fmt_cop(s['q3'])}</strong>, con mediana <strong>{fmt_cop(s['mediana'])}</strong> "
                f"(mínimo {fmt_cop(s['minimo'])}, máximo {fmt_cop(s['maximo'])}).")
            por_v = s.get("por_version", {})
            orden = [v for v in ["XLI", "XEI", "SEG", "GR-S", "Sin identificar"] if v in por_v]
            dist += _tabla([(f"Mediana {v} ({por_v[v]['n']} anuncios)", fmt_cop(por_v[v]["mediana"]))
                            for v in orden])

    # 4. Gangas
    if analisis is None:
        gangas = _parrafo("No se pudo evaluar gangas porque el análisis no está disponible.")
    else:
        nuevas = analisis.get("gangas_nuevas", [])
        otras  = [g for g in analisis.get("gangas", []) if not g.get("nuevo")]
        n_nuevos = (etapas.get("loader") or {}).get("anuncios_nuevos")
        if nuevas:
            gangas = "".join(_tarjeta_ganga(g) for g in nuevas)
        elif analisis.get("nota_modelo"):
            gangas = _parrafo(escape(analisis["nota_modelo"]))
        else:
            motivo = ("no hubo anuncios nuevos hoy" if n_nuevos == 0
                      else "ninguno de los anuncios nuevos quedó por debajo del umbral")
            gangas = _parrafo(f"No se detectaron posibles gangas entre los anuncios nuevos: {motivo}.")
        insuf_nuevos = [f for f in analisis.get("insuficientes", []) if f.get("nuevo")]
        if insuf_nuevos:
            gangas += _parrafo(
                f"{len(insuf_nuevos)} anuncio(s) nuevo(s) no se pudieron evaluar por falta de datos:<br>" +
                "<br>".join(f'• <a href="{escape(f["url"], quote=True)}">{escape(f["url"].split("/")[-1][:8])}</a>: '
                            f'{escape(f["motivo_insuficiente"])}' for f in insuf_nuevos))
        if otras:
            gangas += _parrafo(f"<strong>Otras {len(otras)} posibles gangas vigentes</strong> "
                               "(anuncios vistos en días anteriores):", C_TINTA)
            gangas += "".join(_tarjeta_ganga(g) for g in otras)

    metodo = ""
    if analisis and analisis.get("parametros"):
        p = analisis["parametros"]
        metodo = (f"Método: cada precio se compara con el que predice un modelo de regresión según año, "
                  f"kilometraje, versión, motor híbrido, blindaje y color, entrenado con los demás anuncios. "
                  f"Se marca posible ganga si está al menos {abs(p['umbral_pct']) * 100:.0f} % por debajo "
                  f"de esa referencia. ")
    generado = datetime.now(BOGOTA).strftime("%Y-%m-%d %H:%M:%S")
    marca_prueba = (f'<div style="background:#fab219;color:#0b0b0b;padding:8px 28px;font-weight:700;">'
                    f'⚠ CORREO DE PRUEBA — no forma parte del periodo evaluado</div>' if prueba else "")

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporte Corolla {fecha}</title></head>
<body style="margin:0;padding:0;background:{C_FONDO};font-family:'Segoe UI',Arial,sans-serif;font-size:14px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{C_FONDO};">
<tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
       style="max-width:680px;background:#ffffff;border:1px solid {C_BORDE};border-radius:8px;">
  <tr><td>{marca_prueba}</td></tr>
  <tr><td style="padding:24px 28px 4px 28px;">
    <div style="color:{C_MUTED};font-size:12px;letter-spacing:.04em;text-transform:uppercase;">
      Monitor Toyota Corolla 2022–2026 · Carroya</div>
    <h1 style="margin:6px 0 12px 0;font-size:22px;color:{C_TINTA};">Reporte del {fecha.strftime('%d/%m/%Y')}</h1>
    <span style="display:inline-block;background:{fondo};color:{tinta};padding:5px 12px;border-radius:14px;
                 font-weight:700;font-size:13px;">{icono} {etiqueta}</span>
  </td></tr>
  {_seccion("Resumen del día", resumen)}
  {_seccion("Estado de las etapas", tabla_etapas + avisos)}
  {_seccion("Distribución de precios", dist)}
  {_seccion("Posibles gangas entre los anuncios nuevos", gangas)}
  <tr><td style="padding:18px 28px 24px 28px;color:{C_MUTED};font-size:12px;line-height:1.5;">
    {escape(metodo)}Una posible ganga es una alerta para revisar el anuncio, no una garantía:
    el precio puede reflejar daños, errores de publicación u otros factores que el modelo no ve.<br>
    Generado el {generado} (hora de Colombia, America/Bogota).
  </td></tr>
</table></td></tr></table></body></html>"""


def construir_texto(fecha: date, etapas: dict, analisis: dict | None, estado: str,
                    errores: list[str], pendientes: list[str]) -> str:
    """Versión en texto plano para clientes que no muestran HTML."""
    scraper, loader = etapas.get("scraper") or {}, etapas.get("loader") or {}
    lineas = [f"Reporte Toyota Corolla — {fecha}", f"Estado: {ESTADO_GENERAL[estado][1]}", "",
              f"Enlaces encontrados: {fmt_num(scraper.get('enlaces_encontrados'))}",
              f"Anuncios nuevos: {fmt_num(loader.get('anuncios_nuevos'))}",
              f"Descargas exitosas: {fmt_num(scraper.get('descargas_ok'))}",
              f"Descargas fallidas: {fmt_num(scraper.get('descargas_fallidas'))}", ""]
    lineas += [f"ERROR: {e}" for e in errores] + [f"PENDIENTE: {p}" for p in pendientes]
    if analisis and analisis["estadisticas"].get("mediana"):
        s = analisis["estadisticas"]
        lineas += ["", f"Precios (n={s['n_precio_valido']}): Q1 {fmt_cop(s['q1'])}, "
                       f"mediana {fmt_cop(s['mediana'])}, Q3 {fmt_cop(s['q3'])}"]
        for g in analisis.get("gangas_nuevas", []):
            lineas += ["", f"POSIBLE GANGA: {g['url']}", g.get("explicacion") or ""]
        if not analisis.get("gangas_nuevas"):
            lineas += ["", "No se detectaron posibles gangas entre los anuncios nuevos."]
    else:
        lineas += ["", "Análisis de precios no disponible."]
    return "\n".join(lineas)


# ── Envío y registro ──────────────────────────────────────────────────────────
def construir_mensaje(fecha: date, prueba: bool) -> tuple[EmailMessage, str]:
    etapas, error_bd = leer_etapas(fecha)
    analisis, grafica = leer_analisis(fecha)
    estado, errores, pendientes = evaluar_estado(etapas, error_bd)

    cid = make_msgid(domain="corolla-monitor")
    html_correo = construir_html(fecha, etapas, analisis, estado, errores, pendientes,
                                 f"cid:{cid[1:-1]}" if grafica else None, prueba)
    html_copia = html_correo
    if grafica:
        b64 = base64.b64encode(grafica.read_bytes()).decode()
        html_copia = html_correo.replace(f"cid:{cid[1:-1]}", f"data:image/png;base64,{b64}")

    n_gangas = len(analisis.get("gangas_nuevas", [])) if analisis else 0
    asunto = (f"{'[PRUEBA] ' if prueba else ''}Corolla {fecha.strftime('%d/%m/%Y')} · "
              f"{ESTADO_GENERAL[estado][1]} · {n_gangas} posible(s) ganga(s) nueva(s)")

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"]    = os.environ.get("SMTP_USER", "corolla-monitor@localhost")
    msg["To"]      = os.environ.get("EMAIL_TO", "")
    msg["Date"]    = format_datetime(datetime.now(BOGOTA))
    msg.set_content(construir_texto(fecha, etapas, analisis, estado, errores, pendientes))
    msg.add_alternative(html_correo, subtype="html")
    if grafica:
        msg.get_payload()[1].add_related(grafica.read_bytes(), maintype="image", subtype="png",
                                         cid=cid, filename="precios.png")
    return msg, html_copia


def guardar_copia(msg: EmailMessage, html_copia: str, fecha: date, prueba: bool) -> Path:
    ENVIADOS_DIR.mkdir(parents=True, exist_ok=True)
    base = ENVIADOS_DIR / f"{fecha.isoformat()}{'_prueba' if prueba else ''}"
    base.with_suffix(".eml").write_bytes(msg.as_bytes())
    base.with_suffix(".html").write_text(html_copia, encoding="utf-8")
    return base


def enviar(msg: EmailMessage):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASSWORD")
    destinatarios = [d.strip() for d in os.environ.get("EMAIL_TO", "").split(",") if d.strip()]
    faltan = [n for n, v in [("SMTP_HOST", host), ("SMTP_USER", user),
                             ("SMTP_PASSWORD", pwd), ("EMAIL_TO", destinatarios)] if not v]
    if faltan:
        raise RuntimeError(f"Faltan variables en .env: {', '.join(faltan)}")

    contexto = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=contexto, timeout=30) as s:
            s.login(user, pwd)
            s.send_message(msg, to_addrs=destinatarios)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=contexto)
            s.login(user, pwd)
            s.send_message(msg, to_addrs=destinatarios)


def main(fecha: date, prueba: bool, no_enviar: bool) -> int:
    msg, html_copia = construir_mensaje(fecha, prueba)
    base = guardar_copia(msg, html_copia, fecha, prueba)
    print(f"Asunto: {msg['Subject']}")
    print(f"Copia guardada: {base}.eml / .html")

    if no_enviar:
        estado_correo, error = "simulado (no enviado)", None
    else:
        try:
            enviar(msg)
            estado_correo, error = "enviado", None
        except Exception as exc:
            estado_correo, error = "fallido", str(exc)

    # El registro en el log no debe impedir el envío: va al final y es tolerante a fallos
    try:
        logger = RunLogger(run_id=fecha.isoformat())
        try:
            with logger.etapa("correo") as log:
                log.set(correo_estado=estado_correo,
                        mensaje=f"{'PRUEBA · ' if prueba else ''}{msg['Subject']}",
                        detalle_error=error)
        finally:
            logger.close()
    except Exception as exc:
        print(f"[WARN] No se pudo registrar el envío en el log: {exc}")

    print(f"Estado del correo: {estado_correo}" + (f" — {error}" if error else ""))
    return 1 if error else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Correo diario del monitor Corolla")
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy en Bogotá)")
    ap.add_argument("--prueba", action="store_true", help="Marca el correo como prueba")
    ap.add_argument("--no-enviar", action="store_true", help="Solo genera la copia, no envía")
    a = ap.parse_args()
    f = datetime.strptime(a.fecha, "%Y-%m-%d").date() if a.fecha else datetime.now(BOGOTA).date()
    sys.exit(main(f, a.prueba, a.no_enviar))
