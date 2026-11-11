"""
Scraper de Carroya - Toyota Corolla 2022-2026
Taller 2 - Maestría Uniandes

Genera dos archivos CSV por ejecución:
  - links_YYYY-MM-DD.csv   : todos los enlaces encontrados y su estado
  - datos_YYYY-MM-DD.csv   : datos extraídos de cada anuncio
"""


import asyncio
import csv
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_BOGOTA = ZoneInfo("America/Bogota")

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Configuración ──────────────────────────────────────────────────────────────
BASE_URL = (
    "https://www.carroya.com/automoviles-y-camionetas/toyota/corolla"
    "?page={page}&modelRange=2022-2026"
)
MAX_PAGES = 10          # límite de seguridad; se detiene si no hay más resultados
SCROLL_PAUSE = 1.5      # segundos entre scrolls dentro de la página de listado
REQUEST_PAUSE = 2.0     # segundos entre peticiones de detalle
OUTPUT_DIR = Path(__file__).parent / "datos"

# ── Columnas de los archivos ───────────────────────────────────────────────────
LINKS_COLS = [
    "id", "url", "fecha_descubrimiento", "hora_descubrimiento",
    "estado", "fecha_ultimo_intento", "hora_ultimo_intento", "error"
]
DATOS_COLS = [
    "id", "url",
    "marca", "linea", "año", "version",
    "precio", "moneda", "kilometraje", "transmision",
    "color", "ciudad", "sunroof",
    "vendedor_nombre", "vendedor_departamento",
    "estado", "fecha_descarga", "hora_descarga", "error"
]

# ── Estados ────────────────────────────────────────────────────────────────────
ESTADO_SIN_INTENTAR = "sin intentar"
ESTADO_DESCARGADO   = "descargado"
ESTADO_FALLIDO      = "fallido"


# ── Helpers ────────────────────────────────────────────────────────────────────
def now_str():
    n = datetime.now(_BOGOTA)
    return n.strftime("%Y-%m-%d"), n.strftime("%H:%M:%S")


def csv_path(prefix: str) -> Path:
    fecha = datetime.now(_BOGOTA).strftime("%Y-%m-%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{prefix}_{fecha}.csv"


def load_existing_links(path: Path) -> dict:
    """Carga links ya conocidos {url: row_dict}."""
    known = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                known[row["url"]] = row
    return known


def save_links(path: Path, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LINKS_COLS)
        w.writeheader()
        w.writerows(rows)


def save_datos(path: Path, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DATOS_COLS)
        w.writeheader()
        w.writerows(rows)


# ── Recolección de enlaces ─────────────────────────────────────────────────────
async def collect_links(browser) -> list[str]:
    """Recorre todas las páginas de resultados y devuelve la lista de URLs únicas."""
    page = await browser.new_page()
    await page.set_extra_http_headers({"Accept-Language": "es-CO,es;q=0.9"})

    all_links: set[str] = set()

    for page_num in range(1, MAX_PAGES + 1):
        url = BASE_URL.format(page=page_num)
        print(f"  → Página {page_num}: {url}")

        try:
            await page.goto(url, wait_until="networkidle", timeout=30_000)
        except PlaywrightTimeout:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Scroll hasta el final para disparar lazy-load
        prev_count = 0
        for _ in range(20):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(SCROLL_PAUSE)
            links = await page.eval_on_selector_all(
                'a[href*="/detalle/"]',
                "els => els.map(e => e.href)"
            )
            if len(links) == prev_count:
                break
            prev_count = len(links)

        # Filtrar solo Toyota Corolla 2022-2026 en la URL
        for link in links:
            if "/detalle/" in link and "/toyota/corolla/" in link:
                # Verificar año en URL (ej. /2022/ /2023/ /2024/ /2025/ /2026/)
                m = re.search(r"/toyota/corolla/(\d{4})/", link)
                if m and 2022 <= int(m.group(1)) <= 2026:
                    all_links.add(link.split("?")[0])  # quitar query strings

        # Si esta página no aportó resultados, detenemos
        if not links:
            print(f"  → Sin resultados en página {page_num}. Fin del listado.")
            break

        # Verificar si existe el botón de la página siguiente
        # La paginación usa <button class="page-button"> sin href
        next_btn = await page.query_selector(
            f'button.page-button:not(.active)'
        )
        # Comprobación directa: ¿hay un botón con el número siguiente?
        all_btns = await page.eval_on_selector_all(
            'button.page-button',
            "els => els.map(e => e.innerText.trim())"
        )
        if str(page_num + 1) not in all_btns:
            print(f"  → No hay página {page_num + 1}. Fin del listado.")
            break

    await page.close()
    return sorted(all_links)


# ── Extracción de datos de un anuncio ─────────────────────────────────────────
def _text(el):
    """Devuelve el texto de un ElementHandle o None."""
    return el  # usado con inner_text() de Playwright


async def extract_detail(page, url: str) -> dict:
    """
    Extrae los campos de un anuncio individual.
    Devuelve un dict con los campos requeridos.
    """
    try:
        await page.goto(url, wait_until="networkidle", timeout=20_000)
    except PlaywrightTimeout:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    await asyncio.sleep(1.5)

    async def txt(selector: str) -> str | None:
        try:
            el = await page.query_selector(selector)
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            pass
        return None

    async def feature(label: str) -> str | None:
        """Busca el valor de una característica por su etiqueta."""
        try:
            features = await page.query_selector_all(".feature")
            for feat in features:
                content = (await feat.inner_text()).strip()
                lines = [l.strip() for l in content.split("\n") if l.strip()]
                if len(lines) >= 2 and lines[0].upper() == label.upper():
                    return lines[1]
        except Exception:
            pass
        return None

    # Campos básicos del encabezado
    marca   = "Toyota"
    linea   = "Corolla"
    version = await txt("h3.text.version") or await txt(".version")
    precio_raw = await txt("h1.priceInfo") or await txt("h1.priceInfo.text")
    año     = await txt("h3.yearDetail") or await txt("h3.h3P.yearDetail")

    # Ciudad y kilometraje del contenedor principal
    info_text = ""
    info_el = await page.query_selector(".infoContainer")
    if info_el:
        info_text = await info_el.inner_text()

    # Ciudad: fuente primaria = URL (siempre confiable)
    ciudad = None
    m_url = re.search(r"/toyota/corolla/\d{4}/([^/]+)/", url)
    if m_url:
        ciudad = m_url.group(1).replace("-", " ").title()

    km_raw = None
    m_km = re.search(r"([\d\.,]+)\s*km", info_text, re.IGNORECASE)
    if m_km:
        km_raw = m_km.group(1).replace(".", "").replace(",", "")

    # Características
    transmision = await feature("TIPO DE CAJA")
    color       = await feature("COLOR")

    # Sunroof: buscar en comentarios del vendedor y en el texto completo
    full_text = await page.evaluate("document.body.innerText")
    sunroof = "Sí" if re.search(r"sunroof|techo\s+solar|techo\s+corredizo|moonroof",
                                 full_text, re.IGNORECASE) else "No"

    # Precio y moneda
    precio  = None
    moneda  = None
    if precio_raw:
        # Formato: $108.000.000  o  USD 10,000
        m_cop = re.search(r"\$\s*([\d\.,]+)", precio_raw)
        m_usd = re.search(r"USD?\s*([\d\.,]+)", precio_raw, re.IGNORECASE)
        if m_cop:
            precio = m_cop.group(1).replace(".", "").replace(",", "")
            moneda = "COP"
        elif m_usd:
            precio = m_usd.group(1).replace(".", "").replace(",", "")
            moneda = "USD"

    # Año desde URL como fallback
    if not año:
        m_año = re.search(r"/toyota/corolla/(\d{4})/", url)
        if m_año:
            año = m_año.group(1)

    # Vendedor
    vendedor_nombre = await txt(".cy-seller-detail__name")
    vendedor_dept   = None
    seller_el = await page.query_selector(".cy-seller-detail")
    if seller_el:
        seller_lines = (await seller_el.inner_text()).strip().split("\n")
        if len(seller_lines) >= 2:
            vendedor_dept = seller_lines[1].strip() or None

    return {
        "marca":                  marca,
        "linea":                  linea,
        "año":                    año,
        "version":                version,
        "precio":                 precio,
        "moneda":                 moneda,
        "kilometraje":            km_raw,
        "transmision":            transmision,
        "color":                  color,
        "ciudad":                 ciudad,
        "sunroof":                sunroof,
        "vendedor_nombre":        vendedor_nombre,
        "vendedor_departamento":  vendedor_dept,
    }


# ── Flujo principal ────────────────────────────────────────────────────────────
async def main():
    links_path = csv_path("links")
    datos_path = csv_path("datos")

    print(f"\n{'='*60}")
    print(f"Scraper Carroya — Toyota Corolla 2022-2026")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Archivos: {links_path.name}  |  {datos_path.name}")
    print(f"{'='*60}\n")

    # ── 1. Cargar estado previo ────────────────────────────────────────────────
    known_links = load_existing_links(links_path)
    known_datos: dict[str, dict] = {}
    if datos_path.exists():
        with open(datos_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                known_datos[row["url"]] = row

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        # ── 2. Recolectar enlaces ──────────────────────────────────────────────
        print("[ FASE 1 ] Recolectando enlaces...")
        found_urls = await collect_links(browser)
        print(f"  → {len(found_urls)} URLs únicas encontradas\n")

        fecha_now, hora_now = now_str()

        # Registrar nuevos enlaces
        for url in found_urls:
            if url not in known_links:
                link_id = str(uuid.uuid4())[:8]
                known_links[url] = {
                    "id":                   link_id,
                    "url":                  url,
                    "fecha_descubrimiento": fecha_now,
                    "hora_descubrimiento":  hora_now,
                    "estado":               ESTADO_SIN_INTENTAR,
                    "fecha_ultimo_intento": "",
                    "hora_ultimo_intento":  "",
                    "error":                "",
                }

        # Guardar archivo de enlaces actualizado
        save_links(links_path, list(known_links.values()))
        print(f"  → Archivo de enlaces guardado: {links_path}\n")

        # ── 3. Descargar datos de cada anuncio ────────────────────────────────
        print("[ FASE 2 ] Descargando datos de anuncios...")
        detail_page = await browser.new_page()
        await detail_page.set_extra_http_headers({"Accept-Language": "es-CO,es;q=0.9"})

        pendientes = [
            row for row in known_links.values()
            if row["estado"] in (ESTADO_SIN_INTENTAR, ESTADO_FALLIDO)
        ]
        print(f"  → {len(pendientes)} anuncios pendientes de descarga\n")

        for i, link_row in enumerate(pendientes, 1):
            url = link_row["url"]
            link_id = link_row["id"]
            fecha_intento, hora_intento = now_str()

            print(f"  [{i}/{len(pendientes)}] {url}")

            # Inicializar fila de datos si no existe
            if url not in known_datos:
                known_datos[url] = {col: "" for col in DATOS_COLS}
                known_datos[url]["id"]  = link_id
                known_datos[url]["url"] = url

            try:
                campos = await extract_detail(detail_page, url)

                # Actualizar fila de datos
                known_datos[url].update(campos)
                known_datos[url]["estado"]        = ESTADO_DESCARGADO
                known_datos[url]["fecha_descarga"] = fecha_intento
                known_datos[url]["hora_descarga"]  = hora_intento
                known_datos[url]["error"]          = ""

                # Actualizar enlace
                known_links[url]["estado"]               = ESTADO_DESCARGADO
                known_links[url]["fecha_ultimo_intento"] = fecha_intento
                known_links[url]["hora_ultimo_intento"]  = hora_intento
                known_links[url]["error"]                = ""

                print(f"    ✓ {campos.get('año')} | {campos.get('version')} | "
                      f"{campos.get('precio')} {campos.get('moneda')} | "
                      f"{campos.get('ciudad')}")

            except Exception as exc:
                error_msg = str(exc)[:200]
                print(f"    ✗ Error: {error_msg}")

                known_datos[url]["estado"]        = ESTADO_FALLIDO
                known_datos[url]["fecha_descarga"] = fecha_intento
                known_datos[url]["hora_descarga"]  = hora_intento
                known_datos[url]["error"]          = error_msg

                known_links[url]["estado"]               = ESTADO_FALLIDO
                known_links[url]["fecha_ultimo_intento"] = fecha_intento
                known_links[url]["hora_ultimo_intento"]  = hora_intento
                known_links[url]["error"]                = error_msg

            # Guardar progreso después de cada anuncio
            save_links(links_path, list(known_links.values()))
            save_datos(datos_path, list(known_datos.values()))

            await asyncio.sleep(REQUEST_PAUSE)

        await detail_page.close()
        await browser.close()

    # ── 4. Resumen final ───────────────────────────────────────────────────────
    total      = len(known_links)
    desc       = sum(1 for r in known_links.values() if r["estado"] == ESTADO_DESCARGADO)
    fallidos   = sum(1 for r in known_links.values() if r["estado"] == ESTADO_FALLIDO)
    sin_int    = sum(1 for r in known_links.values() if r["estado"] == ESTADO_SIN_INTENTAR)

    print(f"\n{'='*60}")
    print(f"RESUMEN FINAL")
    print(f"  Total enlaces:    {total}")
    print(f"  Descargados:      {desc}")
    print(f"  Fallidos:         {fallidos}")
    print(f"  Sin intentar:     {sin_int}")
    print(f"\nArchivos guardados en: {OUTPUT_DIR}")
    print(f"  → {links_path.name}")
    print(f"  → {datos_path.name}")
    print(f"{'='*60}\n")

    return {
        "enlaces_encontrados": total,
        "descargas_ok":        desc,
        "descargas_fallidas":  fallidos,
        "descargas_pendientes": sin_int,
    }


if __name__ == "__main__":
    _logger = None
    try:
        from db.logger import RunLogger
        _logger = RunLogger()
    except Exception as _e:
        print(f"[WARN] Logger no disponible, corriendo sin logging: {_e}")

    if _logger:
        with _logger.etapa("scraper") as _etapa:
            _stats = asyncio.run(main())
            _etapa.set(**_stats)
        _logger.close()
    else:
        asyncio.run(main())
