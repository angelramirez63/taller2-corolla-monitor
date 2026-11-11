"""
Análisis de precios y detección de posibles gangas — Taller 2 (punto 6).

Uso:
    python analisis/gangas.py                      # analiza la fecha más reciente en la BD
    python analisis/gangas.py --fecha 2026-11-16   # analiza una fecha específica

Salidas en reportes/YYYY-MM-DD/:
    analisis.json  estadísticas, modelo, gangas y anuncios sin datos suficientes
    precios.png    gráfica de la distribución de precios del día
    gangas.csv     tabla de anuncios evaluados con su precio de referencia

Método (resumen; el detalle está en README / documento de entrega):
  1. Distribución diaria: una observación por anuncio, solo 'descargado' ese día
     y con precio válido.
  2. Modelo hedónico: log(precio) ~ año + log(km) + versión + híbrido + blindado
     + color blanco, ajustado con regresión ridge sobre todos los anuncios
     únicos vistos hasta la fecha (su observación válida más reciente).
     El sunroof se reporta pero no entra al modelo: viene de serie en el SEG
     (ya lo captura la versión) y el scraper solo detecta si el vendedor lo
     menciona, así que como variable mide la redacción del anuncio, no el carro.
  3. Referencia de cada anuncio: predicción leave-one-out (modelo entrenado sin
     ese anuncio), para que un precio atípico no se compare contra sí mismo.
  4. Posible ganga: precio ≥ 15 % por debajo de la referencia Y residuo
     ≤ -1.5 desviaciones robustas (MAD) del modelo.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from db.logger import BOGOTA, RunLogger

BASE_DIR     = Path(__file__).parent.parent
REPORTES_DIR = BASE_DIR / "reportes"

# ── Parámetros del método ─────────────────────────────────────────────────────
PRECIO_MIN, PRECIO_MAX = 30_000_000, 300_000_000   # COP; fuera de rango = inválido
KM_MAX                 = 400_000
UMBRAL_PCT             = -0.15     # precio al menos 15 % bajo la referencia
UMBRAL_Z               = -1.5      # y residuo ≤ -1.5 desviaciones robustas
MIN_ENTRENAMIENTO      = 25        # anuncios mínimos para ajustar el modelo
MIN_COMPARABLES        = 3         # anuncios de la misma versión (sin contar el propio)
MIN_ATRIBUTO           = 2         # otros anuncios con el mismo atributo binario
RIDGE_LAMBDA           = 1.0

VERSIONES        = ["XLI", "XEI", "SEG", "GR-S"]   # orden de gama
VERSION_BASE     = "XEI"                            # categoría de referencia del modelo
AÑO_REF          = 2024

# Patrones en orden de prioridad (el primero que aparezca define la versión)
_PATRONES_VERSION = [
    ("GR-S", re.compile(r"\bGR[\s-]?S(PORT)?\b", re.I)),
    ("SEG",  re.compile(r"\bSE[\s-]?G\b", re.I)),
    ("XEI",  re.compile(r"\bXE[\s-]?I\b", re.I)),
    ("XLI",  re.compile(r"\bXL[\s-]?I\b", re.I)),
]
_RE_HIBRIDO   = re.compile(r"h[ií]brid|hybrid|\bhev\b", re.I)
_RE_BLINDADO  = re.compile(r"blindad|blindaje", re.I)
_RE_NO_BLIND  = re.compile(r"(sin|no)\s+blind", re.I)

# Colores (paleta de referencia de la guía de visualización, modo claro)
C_SUPERFICIE = "#fcfcfb"
C_TINTA      = "#0b0b0b"
C_TINTA_2    = "#52514e"
C_MUTED      = "#898781"
C_GRID       = "#e1e0d9"
C_EJE        = "#c3c2b7"
C_SERIE      = "#2a78d6"   # slot 1 (azul): anuncios
C_GANGA      = "#eb6834"   # slot 2 (naranja): posibles gangas


# ── Normalización de variables ────────────────────────────────────────────────
def _texto(x) -> str | None:
    """Devuelve el texto limpio o None (NaN, None y cadenas vacías son faltantes)."""
    return x.strip() or None if isinstance(x, str) else None


def normalizar_version(version, comentarios) -> str | None:
    """
    Mapea el texto libre de versión a XLI / XEI / SEG / GR-S.
    Primero usa el campo versión; si es ambiguo (ej. '1.8', '1.8 Aut'),
    busca en los comentarios del vendedor. Devuelve None si no hay certeza.
    """
    for texto in (_texto(version), _texto(comentarios)):
        if not texto:
            continue
        for nombre, patron in _PATRONES_VERSION:
            if patron.search(texto):
                return nombre
    return None


def detectar_hibrido(combustible, version, comentarios) -> bool | None:
    """True/False según el campo COMBUSTIBLE; si falta, busca la palabra en el texto."""
    combustible = _texto(combustible)
    if combustible:
        return bool(_RE_HIBRIDO.search(combustible))
    texto = " ".join(t for t in (_texto(version), _texto(comentarios)) if t)
    if _RE_HIBRIDO.search(texto):
        return True
    return None   # desconocido: no se inventa


def detectar_blindado(version, comentarios) -> bool:
    """Carroya no tiene campo de blindaje: se infiere de la versión y los comentarios."""
    texto = " ".join(t for t in (_texto(version), _texto(comentarios)) if t)
    if _RE_NO_BLIND.search(texto):
        return False
    return bool(_RE_BLINDADO.search(texto))


def fmt_cop(valor: float | int | None) -> str:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    return "$" + f"{int(round(valor)):,}".replace(",", ".")


def fmt_millones(valor: float) -> str:
    return f"${valor / 1e6:,.1f} M".replace(",", "X").replace(".", ",").replace("X", ".")


# ── Carga de datos ────────────────────────────────────────────────────────────
_SQL_OBS = """
    SELECT a.id AS anuncio_id, a.url, a.año, a.ciudad, a.primera_vista,
           o.fecha, o.version, o.precio, o.moneda, o.kilometraje,
           o.transmision, o.combustible, o.color, o.sunroof, o.comentarios
    FROM observacion o
    JOIN anuncio a ON a.id = o.anuncio_id
    WHERE o.estado = 'descargado'
"""


def _query_df(conn, sql: str, params=()) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def cargar_datos(conn, fecha: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Devuelve (dia, entrenamiento):
      dia           observaciones descargadas exactamente en `fecha`
      entrenamiento observación descargada más reciente (≤ fecha) de cada anuncio
    """
    dia = _query_df(conn, _SQL_OBS + " AND o.fecha = %s", (fecha,))
    entrenamiento = _query_df(conn, f"""
        SELECT DISTINCT ON (anuncio_id) * FROM ({_SQL_OBS} AND o.fecha <= %s) t
        ORDER BY anuncio_id, fecha DESC
    """, (fecha,))
    return preparar(dia), preparar(entrenamiento)


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Deriva las variables del modelo y marca precios/datos inválidos."""
    if df.empty:
        return df.assign(version_norm=None, hibrido=None, blindado=False,
                         blanco=False, precio_valido=False, motivo_invalido=None)
    df = df.copy()
    df["precio"]      = pd.to_numeric(df["precio"], errors="coerce")
    df["kilometraje"] = pd.to_numeric(df["kilometraje"], errors="coerce")
    df["año"]         = pd.to_numeric(df["año"], errors="coerce")

    df["version_norm"] = [normalizar_version(v, c) for v, c in zip(df["version"], df["comentarios"])]
    df["hibrido"]  = [detectar_hibrido(f, v, c)
                      for f, v, c in zip(df["combustible"], df["version"], df["comentarios"])]
    df["blindado"] = [detectar_blindado(v, c) for v, c in zip(df["version"], df["comentarios"])]
    df["blanco"]   = df["color"].fillna("").str.strip().str.lower().eq("blanco")
    df["sunroof"]  = df["sunroof"].fillna(False).astype(bool)

    motivos = []
    for _, r in df.iterrows():
        if pd.isna(r["precio"]):
            motivos.append("sin precio")
        elif r["moneda"] != "COP":
            motivos.append(f"moneda {r['moneda']} no comparable")
        elif not PRECIO_MIN <= r["precio"] <= PRECIO_MAX:
            motivos.append(f"precio fuera de rango ({fmt_cop(r['precio'])})")
        else:
            motivos.append(None)
    df["motivo_invalido"] = motivos
    df["precio_valido"]   = df["motivo_invalido"].isna()
    return df


# ── Modelo ────────────────────────────────────────────────────────────────────
_FEATURES = ["año_c", "log_km_c", "XLI", "SEG", "GR-S", "hibrido", "blindado", "blanco"]


def _motivo_no_modelable(r) -> str | None:
    if not r["precio_valido"]:
        return r["motivo_invalido"]
    if pd.isna(r["version_norm"]):
        return "versión no identificable (ni en la ficha ni en los comentarios)"
    if pd.isna(r["hibrido"]):
        return "no se sabe si es híbrido o gasolina"
    if pd.isna(r["kilometraje"]) or not 0 <= r["kilometraje"] <= KM_MAX:
        return "kilometraje faltante o inválido"
    if pd.isna(r["año"]):
        return "año faltante"
    return None


def matriz(df: pd.DataFrame, km_media: float) -> np.ndarray:
    X = pd.DataFrame({
        "intercepto": 1.0,
        "año_c":      df["año"].astype(float) - AÑO_REF,
        "log_km_c":   np.log1p(df["kilometraje"].astype(float)) - km_media,
        "XLI":        (df["version_norm"] == "XLI").astype(float),
        "SEG":        (df["version_norm"] == "SEG").astype(float),
        "GR-S":       (df["version_norm"] == "GR-S").astype(float),
        "hibrido":    df["hibrido"].astype(float),
        "blindado":   df["blindado"].astype(float),
        "blanco":     df["blanco"].astype(float),
    })
    return X.to_numpy(dtype=float)


def ajustar_ridge(X: np.ndarray, y: np.ndarray, lam: float = RIDGE_LAMBDA) -> np.ndarray:
    """Ridge con intercepto sin penalizar. Estabiliza coeficientes con pocos datos."""
    P = np.eye(X.shape[1]) * lam
    P[0, 0] = 0.0
    return np.linalg.solve(X.T @ X + P, X.T @ y)


def evaluar(dia: pd.DataFrame, train: pd.DataFrame, fecha: date) -> dict:
    """Ajusta el modelo, calcula referencias leave-one-out y clasifica cada anuncio."""
    train = train.copy()
    train["motivo"] = train.apply(_motivo_no_modelable, axis=1)
    train = train[train["motivo"].isna()].reset_index(drop=True)

    resultado = {"n_entrenamiento": len(train), "modelo": None, "evaluados": [],
                 "insuficientes": [], "nota_modelo": None}

    if len(train) < MIN_ENTRENAMIENTO:
        resultado["nota_modelo"] = (
            f"Solo hay {len(train)} anuncios con datos completos (mínimo {MIN_ENTRENAMIENTO}); "
            "no se ajustó el modelo y no se evalúan gangas."
        )
        for _, r in dia.iterrows():
            resultado["insuficientes"].append(_fila_basica(r, fecha, resultado["nota_modelo"]))
        return resultado

    km_media = float(np.log1p(train["kilometraje"].astype(float)).mean())
    X = matriz(train, km_media)
    y = np.log(train["precio"].to_numpy(dtype=float))
    beta = ajustar_ridge(X, y)

    # Residuos leave-one-out del conjunto de entrenamiento → dispersión robusta
    loo_pred = np.empty(len(train))
    for i in range(len(train)):
        m = np.ones(len(train), dtype=bool); m[i] = False
        loo_pred[i] = X[i] @ ajustar_ridge(X[m], y[m])
    resid = y - loo_pred
    sigma = float(1.4826 * np.median(np.abs(resid - np.median(resid))))
    sigma = max(sigma, 0.03)

    nombres = ["intercepto"] + _FEATURES
    resultado["modelo"] = {
        "coeficientes":   {n: round(float(b), 4) for n, b in zip(nombres, beta)},
        "efectos_pct":    {n: round(100 * (math.exp(b) - 1), 1) for n, b in zip(nombres[1:], beta[1:])},
        "sigma_log":      round(sigma, 4),
        "r2_loo":         round(float(1 - np.sum(resid**2) / np.sum((y - y.mean())**2)), 3),
        "km_media_log":   round(km_media, 4),
    }
    idx_train = {aid: i for i, aid in enumerate(train["anuncio_id"])}

    for _, r in dia.iterrows():
        motivo = _motivo_no_modelable(r)
        if motivo:
            resultado["insuficientes"].append(_fila_basica(r, fecha, motivo))
            continue

        # Excluir el propio anuncio del entrenamiento (leave-one-out)
        mask = np.ones(len(train), dtype=bool)
        if r["anuncio_id"] in idx_train:
            mask[idx_train[r["anuncio_id"]]] = False
        otros = train[mask]

        motivo = _falta_comparables(r, otros)
        if motivo:
            resultado["insuficientes"].append(_fila_basica(r, fecha, motivo))
            continue

        b = ajustar_ridge(X[mask], y[mask])
        x = matriz(pd.DataFrame([r]), km_media)[0]
        ref = float(math.exp(x @ b))
        pct = r["precio"] / ref - 1
        z   = (math.log(r["precio"]) - x @ b) / sigma
        es_ganga = pct <= UMBRAL_PCT and z <= UMBRAL_Z

        fila = _fila_basica(r, fecha, None)
        fila.update({
            "referencia":      round(ref),
            "rango_tipico":    [round(ref * math.exp(-sigma)), round(ref * math.exp(sigma))],
            "diferencia_pct":  round(100 * pct, 1),
            "z_robusto":       round(z, 2),
            "posible_ganga":   bool(es_ganga),
            "comparables":     _comparables(r, otros),
            "ajustes":         _ajustes(x, b),
        })
        fila["explicacion"] = _explicar(fila) if es_ganga else None
        resultado["evaluados"].append(fila)

    return resultado


def _falta_comparables(r, otros: pd.DataFrame) -> str | None:
    n_ver = int((otros["version_norm"] == r["version_norm"]).sum())
    if n_ver < MIN_COMPARABLES:
        return f"solo {n_ver} otro(s) {r['version_norm']} para comparar (mínimo {MIN_COMPARABLES})"
    for attr, nombre in [("hibrido", "híbridos"), ("blindado", "blindados")]:
        if r[attr] and int(otros[attr].astype(bool).sum()) < MIN_ATRIBUTO:
            return f"no hay suficientes {nombre} para estimar su efecto en el precio"
    return None


def _comparables(r, otros: pd.DataFrame) -> dict:
    g = otros[otros["version_norm"] == r["version_norm"]]
    g_año = g[g["año"] == r["año"]]
    return {
        "version":           r["version_norm"],
        "n_version":         len(g),
        "mediana_version":   round(float(g["precio"].median())),
        "n_version_año":     len(g_año),
        "mediana_version_año": round(float(g_año["precio"].median())) if len(g_año) else None,
    }


def _ajustes(x: np.ndarray, b: np.ndarray) -> list[str]:
    """Traduce los términos del modelo a frases legibles (efecto % sobre el precio)."""
    frases = []
    etiquetas = {
        "XLI": "versión XLI (gama de entrada)", "SEG": "versión SEG (tope de gama)",
        "GR-S": "versión GR-S (deportiva)", "hibrido": "motor híbrido",
        "blindado": "blindaje", "blanco": "color blanco",
    }
    for i, nombre in enumerate(_FEATURES, start=1):
        contrib = x[i] * b[i]
        if abs(contrib) < 0.01:
            continue
        pct = 100 * (math.exp(contrib) - 1)
        if nombre == "año_c":
            frases.append(f"{pct:+.0f} % por el año del modelo frente a un {AÑO_REF}")
        elif nombre == "log_km_c":
            frases.append(f"{pct:+.0f} % por su kilometraje frente al promedio")
        else:
            frases.append(f"{pct:+.0f} % por {etiquetas[nombre]}")
    return frases


def _descripcion(f: dict) -> str:
    partes = [str(f["año"] or "")]
    partes.append(f["version_norm"] or "versión sin identificar")
    if f.get("hibrido"):
        partes.append("híbrido")
    if f.get("blindado"):
        partes.append("blindado")
    desc = " ".join(p for p in partes if p)
    if f.get("kilometraje") is not None:
        km = f"{int(f['kilometraje']):,}".replace(",", ".")
        desc += f", {km} km"
    if f.get("color"):
        desc += f", {f['color'].lower()}"
    if f.get("sunroof"):
        desc += ", con sunroof"
    return desc


def _explicar(f: dict) -> str:
    c = f["comparables"]
    txt = (
        f"Toyota Corolla {_descripcion(f)}, publicado en {fmt_cop(f['precio'])}. "
        f"Para un carro con estas características el modelo espera {fmt_cop(f['referencia'])} "
        f"(rango típico {fmt_cop(f['rango_tipico'][0])}–{fmt_cop(f['rango_tipico'][1])}), "
        f"así que está {abs(f['diferencia_pct']):.0f} % por debajo de su referencia. "
    )
    if f["ajustes"]:
        txt += "La referencia ya incluye: " + "; ".join(f["ajustes"]) + ". "
    txt += f"Hay {c['n_version']} {c['version']} más en la base, con mediana {fmt_cop(c['mediana_version'])}"
    if c["n_version_año"]:
        txt += f"; {c['n_version_año']} del mismo año con mediana {fmt_cop(c['mediana_version_año'])}"
    txt += "."
    return txt


def _fila_basica(r, fecha: date, motivo: str | None) -> dict:
    return {
        "anuncio_id":   int(r["anuncio_id"]),
        "url":          r["url"],
        "nuevo":        r["primera_vista"] == fecha,
        "año":          None if pd.isna(r["año"]) else int(r["año"]),
        "version":      _texto(r["version"]),
        "version_norm": None if pd.isna(r["version_norm"]) else r["version_norm"],
        "hibrido":      None if pd.isna(r["hibrido"]) else bool(r["hibrido"]),
        "blindado":     bool(r["blindado"]),
        "kilometraje":  None if pd.isna(r["kilometraje"]) else int(r["kilometraje"]),
        "color":        r["color"],
        "sunroof":      bool(r["sunroof"]),
        "ciudad":       r["ciudad"],
        "precio":       None if pd.isna(r["precio"]) else int(r["precio"]),
        "motivo_insuficiente": motivo,
    }


# ── Distribución diaria ───────────────────────────────────────────────────────
def estadisticas(dia: pd.DataFrame) -> dict:
    validos = dia[dia["precio_valido"]]
    p = validos["precio"]
    stats = {
        "n_descargados":   len(dia),
        "n_precio_valido": len(validos),
        "n_precio_invalido": int((~dia["precio_valido"]).sum()) if len(dia) else 0,
    }
    if len(p):
        stats.update({
            "minimo":  int(p.min()), "q1": int(p.quantile(0.25)),
            "mediana": int(p.median()), "q3": int(p.quantile(0.75)),
            "maximo":  int(p.max()), "promedio": int(p.mean()),
            "por_version": {
                (v or "Sin identificar"): {"n": int(len(g)), "mediana": int(g["precio"].median())}
                for v, g in validos.groupby(validos["version_norm"].fillna("Sin identificar"))
            },
        })
    return stats


def graficar(dia: pd.DataFrame, gangas_ids: set, stats: dict, fecha: date, ruta: Path) -> str | None:
    """Histograma + precio por versión. Devuelve el motivo si no se pudo graficar."""
    validos = dia[dia["precio_valido"]].copy()
    if len(validos) < 5:
        return f"solo {len(validos)} anuncios con precio válido; no alcanza para una distribución"

    validos["precio_m"] = validos["precio"] / 1e6
    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 9.5})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.8),
                                   gridspec_kw={"width_ratios": [1.05, 1]}, facecolor=C_SUPERFICIE)

    for ax in (ax1, ax2):
        ax.set_facecolor(C_SUPERFICIE)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(C_EJE)
        ax.tick_params(colors=C_MUTED, length=0)

    # Panel 1: histograma con mediana y cuartiles
    bins = np.histogram_bin_edges(validos["precio_m"], bins="auto")
    ax1.hist(validos["precio_m"], bins=bins, color=C_SERIE, edgecolor=C_SUPERFICIE, linewidth=2)
    ax1.grid(axis="y", color=C_GRID, linewidth=0.8)
    ax1.set_axisbelow(True)
    ymax = ax1.get_ylim()[1]
    for clave, estilo, ancho, etiqueta in [("q1", "--", 1, "Q1"), ("mediana", "-", 1.8, "Mediana"),
                                           ("q3", "--", 1, "Q3")]:
        v = stats[clave] / 1e6
        ax1.axvline(v, color=C_TINTA if clave == "mediana" else C_TINTA_2, linestyle=estilo, linewidth=ancho)
        ax1.text(v, ymax * 1.01, f"{etiqueta}\n{fmt_millones(stats[clave])}", ha="center", va="bottom",
                 fontsize=8.5, color=C_TINTA if clave == "mediana" else C_TINTA_2)
    ax1.set_xlabel("Precio publicado (millones de COP)", color=C_TINTA_2)
    ax1.set_ylabel("Anuncios", color=C_TINTA_2)
    ax1.set_title(f"Distribución de precios · {fecha} · n = {len(validos)}",
                  color=C_TINTA, loc="left", fontsize=11, pad=34)

    # Panel 2: precio por versión (posición = identidad; color solo distingue gangas)
    orden = [v for v in VERSIONES if (validos["version_norm"] == v).any()]
    if validos["version_norm"].isna().any():
        orden.append("Sin identificar")
    etiqueta_v = validos["version_norm"].fillna("Sin identificar")
    rng = np.random.default_rng(7)
    for i, v in enumerate(orden):
        g = validos[etiqueta_v == v]
        y = i + rng.uniform(-0.18, 0.18, len(g))
        es_g = g["anuncio_id"].isin(gangas_ids).to_numpy()
        ax2.scatter(g["precio_m"][~es_g], y[~es_g], s=38, color=C_SERIE, edgecolor=C_SUPERFICIE,
                    linewidth=1.5, zorder=3, label="Anuncio" if i == 0 else None)
        ax2.scatter(g["precio_m"][es_g], y[es_g], s=70, color=C_GANGA, edgecolor=C_SUPERFICIE,
                    linewidth=1.5, zorder=4, label="Posible ganga" if es_g.any() else None)
        med = g["precio_m"].median()
        ax2.plot([med, med], [i - 0.3, i + 0.3], color=C_TINTA, linewidth=1.8, zorder=5)
    ax2.set_yticks(range(len(orden)))
    ax2.set_yticklabels([f"{v}  (n={int((etiqueta_v == v).sum())})" for v in orden], color=C_TINTA_2)
    ax2.set_ylim(-0.6, len(orden) - 0.4)
    ax2.grid(axis="x", color=C_GRID, linewidth=0.8)
    ax2.set_axisbelow(True)
    ax2.set_xlabel("Precio publicado (millones de COP) · línea negra = mediana", color=C_TINTA_2)
    ax2.set_title("Precio por versión", color=C_TINTA, loc="left", fontsize=11, pad=34)
    handles, labels = ax2.get_legend_handles_labels()
    if len(handles) >= 2:
        ax2.legend(handles, labels, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2,
                   frameon=False, fontsize=8.5, labelcolor=C_TINTA_2, handletextpad=0.3)

    fig.tight_layout()
    fig.savefig(ruta, dpi=150, facecolor=C_SUPERFICIE)
    plt.close(fig)
    return None


# ── Orquestación ──────────────────────────────────────────────────────────────
def _fecha_mas_reciente(conn) -> date | None:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(fecha) FROM observacion WHERE estado = 'descargado'")
        return cur.fetchone()[0]


def _bajadas_precio(conn, fecha: date) -> dict[int, dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT anuncio_id, valor_anterior, valor_nuevo FROM cambio
            WHERE campo = 'precio' AND fecha = %s
        """, (fecha,))
        out = {}
        for aid, ant, nue in cur.fetchall():
            try:
                if int(nue) < int(ant):
                    out[aid] = {"anterior": int(ant), "nuevo": int(nue)}
            except (TypeError, ValueError):
                pass
        return out


def analizar(fecha: date | None = None) -> dict:
    logger = RunLogger()
    conn = logger.conn
    try:
        fecha = fecha or _fecha_mas_reciente(conn)
        if fecha is None:
            raise RuntimeError("No hay observaciones descargadas en la base.")

        with logger.etapa("analisis") as log:
            dia, train = cargar_datos(conn, fecha)
            stats = estadisticas(dia)
            res   = evaluar(dia, train, fecha)

            bajadas = _bajadas_precio(conn, fecha)
            for f in res["evaluados"] + res["insuficientes"]:
                if f["anuncio_id"] in bajadas:
                    f["bajada_precio"] = bajadas[f["anuncio_id"]]

            gangas = sorted([f for f in res["evaluados"] if f["posible_ganga"]],
                            key=lambda f: f["diferencia_pct"])
            carpeta = REPORTES_DIR / fecha.isoformat()
            carpeta.mkdir(parents=True, exist_ok=True)
            motivo_graf = graficar(dia, {g["anuncio_id"] for g in gangas}, stats, fecha,
                                   carpeta / "precios.png")

            reporte = {
                "fecha":            fecha.isoformat(),
                "generado":         datetime.now(BOGOTA).isoformat(timespec="seconds"),
                "parametros": {
                    "umbral_pct": UMBRAL_PCT, "umbral_z": UMBRAL_Z,
                    "precio_valido_cop": [PRECIO_MIN, PRECIO_MAX],
                    "min_entrenamiento": MIN_ENTRENAMIENTO, "min_comparables": MIN_COMPARABLES,
                },
                "estadisticas":     stats,
                "grafica":          None if motivo_graf else "precios.png",
                "motivo_sin_grafica": motivo_graf,
                "modelo":           res["modelo"],
                "nota_modelo":      res["nota_modelo"],
                "n_entrenamiento":  res["n_entrenamiento"],
                "n_evaluados":      len(res["evaluados"]),
                "gangas":           gangas,
                "gangas_nuevas":    [g for g in gangas if g["nuevo"]],
                "insuficientes":    res["insuficientes"],
                "evaluados":        res["evaluados"],
            }
            (carpeta / "analisis.json").write_text(
                json.dumps(reporte, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if res["evaluados"]:
                pd.DataFrame(res["evaluados"]).drop(columns=["ajustes", "comparables"]).to_csv(
                    carpeta / "gangas.csv", index=False, encoding="utf-8")

            log.set(mensaje=(f"fecha={fecha} n_dia={stats['n_precio_valido']} "
                             f"evaluados={len(res['evaluados'])} gangas={len(gangas)} "
                             f"gangas_nuevas={len(reporte['gangas_nuevas'])} "
                             f"insuficientes={len(res['insuficientes'])}"))
        return reporte
    finally:
        logger.close()


def _imprimir(rep: dict):
    s = rep["estadisticas"]
    print(f"\n{'=' * 70}\nAnálisis de precios — {rep['fecha']}\n{'=' * 70}")
    print(f"  Anuncios del día con precio válido: {s['n_precio_valido']} de {s['n_descargados']}")
    if s.get("mediana"):
        print(f"  Q1 {fmt_cop(s['q1'])} · Mediana {fmt_cop(s['mediana'])} · Q3 {fmt_cop(s['q3'])}")
    if rep["modelo"]:
        m = rep["modelo"]
        print(f"  Modelo: n={rep['n_entrenamiento']}  R² LOO={m['r2_loo']}  σ={m['sigma_log']}")
        print("  Efectos estimados sobre el precio:")
        for k, v in m["efectos_pct"].items():
            print(f"    {k:<10} {v:+.1f} %")
    else:
        print(f"  {rep['nota_modelo']}")
    print(f"\n  Evaluados: {rep['n_evaluados']}  ·  Sin datos suficientes: {len(rep['insuficientes'])}")
    for f in rep["insuficientes"]:
        print(f"    - {f['url'][-45:]}: {f['motivo_insuficiente']}")
    print(f"\n  Posibles gangas: {len(rep['gangas'])} (nuevas hoy: {len(rep['gangas_nuevas'])})")
    for g in rep["gangas"]:
        print(f"\n  ★ {g['url']}\n    {g['explicacion']}")
    if not rep["gangas"]:
        print("    No se detectaron posibles gangas con el umbral actual.")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Análisis de precios y gangas Carroya")
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: fecha más reciente en la BD)")
    a = ap.parse_args()
    f = datetime.strptime(a.fecha, "%Y-%m-%d").date() if a.fecha else None
    _imprimir(analizar(f))
