"""Actualiza data/inmuebles_respaldo.csv con avisos públicos reales de venta y alquiler.

Distritos: Lince, Jesús María y San Isidro. Operaciones: venta y alquiler.

Principios de scraping responsable (no negociables):
  * Se respeta robots.txt (incluido Crawl-delay si existe).
  * Pausas aleatorias de cortesía de 3 a 6 s entre requests.
  * User-Agent honesto e identificable. NO se falsifica un navegador ni se ocultan
    flags de automatización: si el sitio responde con un desafío anti-bot (Cloudflare),
    se respeta, se reintenta como máximo 2 veces y luego se conservan los datos previos.
  * Fusión por segmento (distrito × operación): un segmento solo se reemplaza si la
    extracción nueva trae suficientes avisos válidos. El CSV nunca empeora.

Uso (desde la raíz del repo):
    python scripts/actualizar_dataset_real.py                 # motor requests, 2 páginas por búsqueda
    python scripts/actualizar_dataset_real.py --paginas 3
    python scripts/actualizar_dataset_real.py --motor selenium   # requiere: pip install -r requirements-scraper.txt

Salidas:
    data/inmuebles_respaldo.csv   avisos limpios (formato largo, columna `operacion`)
    data/resumen_mercado.csv      medianas por distrito × operación × tipología
    data/mercado_metadata.json    fecha de sincronización y estado de cada segmento
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_DESTINO = DATA_DIR / "inmuebles_respaldo.csv"
CSV_RESUMEN = DATA_DIR / "resumen_mercado.csv"
METADATA = DATA_DIR / "mercado_metadata.json"
LOCK = DATA_DIR / ".actualizacion.lock"

DISTRITOS = {"Lince": "lince", "Jesús María": "jesus-maria", "San Isidro": "san-isidro"}
OPERACIONES = {"venta": "venta", "alquiler": "alquiler"}
BASE_URL = "https://urbania.pe/buscar/{op}-de-departamentos-en-{slug}--lima--lima"

PAUSA_MIN, PAUSA_MAX = 3.0, 6.0
MAX_REINTENTOS = 2
MIN_AVISOS_SEGMENTO = 5

# Rangos de validez por operación (USD). Fuera de esto se descarta el aviso como error de carga.
LIMITES = {
    "venta": {"precio": (30_000, 3_000_000), "m2": (500, 8_000)},
    "alquiler": {"precio": (150, 15_000), "m2": (3, 60)},
}

COLUMNAS = ["id", "operacion", "distrito", "tipo", "tipologia", "dormitorios", "area_m2",
            "precio_usd", "precio_m2_usd", "fuente", "fecha_extraccion"]


def _cargar_env() -> None:
    ruta = ROOT / ".env"
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_cargar_env()
USER_AGENT = os.getenv("SCRAPER_USER_AGENT") or "EVI.IA-research-bot/0.2 (proyecto academico; scraping responsable)"
TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT_SECONDS", "15"))
TIPO_CAMBIO = float(os.getenv("TIPO_CAMBIO", "3.75"))


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------
RE_USD = re.compile(r"(?:US\$|USD)\s?([\d][\d.,]{2,})", re.IGNORECASE)
RE_SOLES = re.compile(r"S/\.?\s?([\d][\d.,]{2,})")
RE_AREA = re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s?m(?:²|2)", re.IGNORECASE)
RE_DORM = re.compile(r"(\d+)\s?dorm", re.IGNORECASE)


def a_numero(texto: str) -> float:
    """'150,000' / '150.000' / '150,000.00' -> 150000.0"""
    texto = re.sub(r"[.,]\d{2}$", "", texto.strip())
    return float(re.sub(r"\D", "", texto))


def precio_en_usd(texto: str) -> float | None:
    """Prefiere el precio en USD del aviso; si solo hay soles, convierte con TIPO_CAMBIO."""
    if m := RE_USD.search(texto):
        return a_numero(m.group(1))
    if m := RE_SOLES.search(texto):
        return a_numero(m.group(1)) / TIPO_CAMBIO
    return None


def tipologia(dorm: int | None) -> str:
    if dorm is None:
        return "Sin dato"
    if dorm == 0:
        return "Monoambiente"
    return f"{dorm} dorm." if dorm < 3 else "3+ dorm."


def parsear_pagina(html: str, operacion: str, distrito: str) -> list[dict]:
    """Avisos individuales de una página de resultados (se omiten proyectos en planos)."""
    soup = BeautifulSoup(html, "html.parser")
    lim = LIMITES[operacion]
    avisos = []
    for card in soup.select('[data-id][data-qa^="posting"]'):
        if "DEVELOPMENT" in card["data-qa"]:  # proyectos: solo rangos "desde…", no sirven
            continue
        el_p = card.select_one('[data-qa="POSTING_CARD_PRICE"]')
        el_f = card.select_one('[data-qa="POSTING_CARD_FEATURES"]')
        if not (el_p and el_f):
            continue
        feats = el_f.get_text(" ", strip=True)
        m_area, m_dorm = RE_AREA.search(feats), RE_DORM.search(feats)
        try:
            precio = precio_en_usd(el_p.get_text(" ", strip=True))
            area = float(m_area.group(1).replace(",", ".")) if m_area else None
        except ValueError:
            continue
        if not (precio and area and 20 <= area <= 500):
            continue
        if not (lim["precio"][0] <= precio <= lim["precio"][1] and lim["m2"][0] <= precio / area <= lim["m2"][1]):
            continue
        dorm = int(m_dorm.group(1)) if m_dorm else None
        avisos.append({
            "id": f"URB-{card['data-id']}", "operacion": operacion, "distrito": distrito,
            "tipo": "Departamento", "tipologia": tipologia(dorm), "dormitorios": dorm,
            "area_m2": area, "precio_usd": round(precio, 0 if operacion == "alquiler" else -2),
        })
    return avisos


# ---------------------------------------------------------------------------
# Motores de descarga
# ---------------------------------------------------------------------------
def es_desafio_antibot(status: int, html: str) -> bool:
    cabeza = html[:3000].lower()
    return status in (403, 429, 503) or "just a moment" in cabeza or "un momento" in cabeza


class MotorRequests:
    nombre = "requests"

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-PE,es;q=0.9"})

    def obtener(self, url: str) -> tuple[int, str]:
        r = self.s.get(url, timeout=TIMEOUT)
        return r.status_code, r.text

    def cerrar(self) -> None:
        self.s.close()


class MotorSelenium:
    """Chrome headless con configuración estándar (sin evasión de detección)."""
    nombre = "selenium"

    def __init__(self) -> None:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1366,900")
        self.d = webdriver.Chrome(options=opts)
        self.d.set_page_load_timeout(TIMEOUT * 3)

    def obtener(self, url: str) -> tuple[int, str]:
        self.d.get(url)
        time.sleep(2)  # deja terminar el render
        return 200, self.d.page_source

    def cerrar(self) -> None:
        self.d.quit()


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
class Cortesia:
    """Pausa aleatoria 3–6 s (o el Crawl-delay de robots.txt si es mayor) entre requests."""

    def __init__(self, crawl_delay: float | None) -> None:
        self.minimo = max(PAUSA_MIN, crawl_delay or 0)
        self.primera = True

    def esperar(self) -> None:
        if not self.primera:
            time.sleep(random.uniform(self.minimo, max(self.minimo, PAUSA_MAX)))
        self.primera = False


def leer_robots() -> RobotFileParser:
    rp = RobotFileParser()
    r = requests.get("https://urbania.pe/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    return rp


def extraer_segmento(motor, cortesia: Cortesia, rp: RobotFileParser, operacion: str,
                     distrito: str, paginas: int) -> tuple[list[dict], str]:
    avisos: dict[str, dict] = {}
    estado = "ok"
    for pagina in range(1, paginas + 1):
        url = BASE_URL.format(op=OPERACIONES[operacion], slug=DISTRITOS[distrito])
        if pagina > 1:
            url += f"-pagina-{pagina}.html"  # formato permitido en robots.txt (páginas 2–5)
        if not rp.can_fetch(USER_AGENT, url):
            estado = "robots.txt no lo permite"
            break
        nuevos = None
        for intento in range(1 + MAX_REINTENTOS):
            cortesia.esperar()
            try:
                status, html = motor.obtener(url)
            except Exception as e:  # red, timeout o navegador
                log(f"  {operacion}/{distrito} p{pagina}: error {type(e).__name__} (intento {intento + 1})")
                continue
            if es_desafio_antibot(status, html):
                log(f"  {operacion}/{distrito} p{pagina}: desafío anti-bot / HTTP {status} (intento {intento + 1})")
                continue
            nuevos = parsear_pagina(html, operacion, distrito)
            log(f"  {operacion}/{distrito} p{pagina}: {len(nuevos)} avisos")
            break
        if nuevos is None:
            estado = "bloqueado por el sitio (se respetó el bloqueo)"
            break
        antes = len(avisos)
        avisos.update({a["id"]: a for a in nuevos})
        if len(avisos) == antes:
            break  # sin avisos nuevos: no hay más páginas útiles
    return list(avisos.values()), estado


def limpiar(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplica y quita outliers de precio/m² (regla 1.5×IQR) por distrito × operación."""
    if df.empty:
        return df
    df = df.drop_duplicates("id").copy()
    df["precio_m2_usd"] = (df["precio_usd"] / df["area_m2"]).round(2)

    def sin_outliers(g: pd.DataFrame) -> pd.DataFrame:
        if len(g) < 8:
            return g
        q1, q3 = g["precio_m2_usd"].quantile([0.25, 0.75])
        iqr = q3 - q1
        return g[g["precio_m2_usd"].between(q1 - 1.5 * iqr, q3 + 1.5 * iqr)]

    return pd.concat([sin_outliers(g) for _, g in df.groupby(["distrito", "operacion"])], ignore_index=True)


def resumir(df: pd.DataFrame) -> pd.DataFrame:
    """Mediana de precio/m², promedio de precio y distribución por tipología."""
    base = df.groupby(["distrito", "operacion"])
    total = base.agg(avisos=("id", "size"), mediana_precio_m2_usd=("precio_m2_usd", "median"),
                     promedio_precio_usd=("precio_usd", "mean"), mediana_area_m2=("area_m2", "median"))
    total["tipologia"] = "Todas"
    por_tipo = df.groupby(["distrito", "operacion", "tipologia"]).agg(
        avisos=("id", "size"), mediana_precio_m2_usd=("precio_m2_usd", "median"),
        promedio_precio_usd=("precio_usd", "mean"), mediana_area_m2=("area_m2", "median"))
    por_tipo["participacion_pct"] = (por_tipo["avisos"] / por_tipo.groupby(level=[0, 1])["avisos"].transform("sum") * 100)
    out = pd.concat([total.reset_index(), por_tipo.reset_index()], ignore_index=True)
    out["participacion_pct"] = out["participacion_pct"].fillna(100.0)
    return out.round(2)[["distrito", "operacion", "tipologia", "avisos", "participacion_pct",
                         "mediana_precio_m2_usd", "promedio_precio_usd", "mediana_area_m2"]]


def escribir_atomico(df: pd.DataFrame, ruta: Path) -> None:
    tmp = ruta.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, ruta)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paginas", type=int, default=2, help="páginas por búsqueda (máx. 5, según robots.txt)")
    ap.add_argument("--motor", choices=["requests", "selenium"], default="requests")
    args = ap.parse_args()
    args.paginas = max(1, min(5, args.paginas))

    LOCK.write_text(json.dumps({"pid": os.getpid(), "inicio": datetime.now().isoformat(timespec="seconds")}))
    ahora = datetime.now().isoformat(timespec="seconds")
    try:
        previo = pd.read_csv(CSV_DESTINO) if CSV_DESTINO.exists() else pd.DataFrame(columns=COLUMNAS)
        if "operacion" not in previo.columns:
            log("CSV previo con formato antiguo: regenera la base con scripts/generar_respaldo.py")
            previo = pd.DataFrame(columns=COLUMNAS)
        meta_prev = json.loads(METADATA.read_text(encoding="utf-8")) if METADATA.exists() else {}

        rp = leer_robots()
        cortesia = Cortesia(rp.crawl_delay(USER_AGENT))
        motor = MotorSelenium() if args.motor == "selenium" else MotorRequests()
        log(f"Motor: {motor.nombre} · páginas/búsqueda: {args.paginas} · pausa {cortesia.minimo:.0f}–{PAUSA_MAX:.0f} s")

        partes, segmentos = [], {}
        try:
            for operacion in OPERACIONES:
                for distrito in DISTRITOS:
                    clave = f"{distrito}|{operacion}"
                    log(f"Segmento {clave}")
                    avisos, estado = extraer_segmento(motor, cortesia, rp, operacion, distrito, args.paginas)
                    nuevo = limpiar(pd.DataFrame(avisos))
                    anterior = previo[(previo["distrito"] == distrito) & (previo["operacion"] == operacion)]
                    if len(nuevo) >= MIN_AVISOS_SEGMENTO:
                        nuevo["fuente"] = f"scraping:urbania.pe ({motor.nombre})"
                        nuevo["fecha_extraccion"] = ahora
                        partes.append(nuevo)
                        segmentos[clave] = {"fuente": "real", "filas": len(nuevo), "estado": estado,
                                            "actualizado": ahora}
                    else:
                        partes.append(anterior)
                        prev = meta_prev.get("segmentos", {}).get(clave, {})
                        fuente_prev = prev.get("fuente", "sintetico")
                        motivo = estado if estado != "ok" else f"solo {len(nuevo)} avisos válidos"
                        segmentos[clave] = {"fuente": "real (anterior)" if fuente_prev.startswith("real") else fuente_prev,
                                            "filas": len(anterior),
                                            "estado": f"se conservan datos previos: {motivo}",
                                            "actualizado": prev.get("actualizado")}
                    log(f"  → {segmentos[clave]['fuente']} ({segmentos[clave]['filas']} filas)")
        finally:
            motor.cerrar()

        no_vacias = [p for p in partes if not p.empty]
        if not no_vacias:
            log("Sin datos nuevos ni previos: no se modifica el CSV.")
            return 1
        final = pd.concat(no_vacias, ignore_index=True).reindex(columns=COLUMNAS)
        escribir_atomico(final, CSV_DESTINO)
        escribir_atomico(resumir(final), CSV_RESUMEN)

        reales = [s["actualizado"] for s in segmentos.values() if s["fuente"] == "real"]
        meta = {
            "ultima_sincronizacion": ahora,
            "ultima_actualizacion_real": max(reales) if reales else meta_prev.get("ultima_actualizacion_real"),
            "motor": motor.nombre,
            "segmentos_reales_en_esta_corrida": len(reales),
            "segmentos": segmentos,
        }
        METADATA.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Listo: {len(final)} filas en {CSV_DESTINO.name} · {len(reales)}/6 segmentos actualizados con datos reales")
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
