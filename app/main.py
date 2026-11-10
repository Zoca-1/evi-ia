"""EVI.IA — Evaluador de Viabilidad de Inversión Inmobiliaria con IA.

Módulos:
    1. Estudio de Mercado   (scraping con pausas + respaldo CSV local)
    2. Rentabilidad          (Cap Rate, inversión total, flujo de caja a 5 años)
    3. Alertas Legales       (base local de trámites y riesgos de saneamiento)

Ejecutar desde la raíz del repo:
    streamlit run app/main.py
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESPALDO_CSV = DATA_DIR / "inmuebles_respaldo.csv"
LEGAL_JSON = DATA_DIR / "legal_distritos.json"

DISTRITOS = ["Lince", "Jesús María", "San Isidro"]

# Búsquedas públicas de avisos de venta por distrito.
FUENTES_SCRAPING = {
    "Lince": "https://urbania.pe/buscar/venta-de-departamentos-en-lince--lima--lima",
    "Jesús María": "https://urbania.pe/buscar/venta-de-departamentos-en-jesus-maria--lima--lima",
    "San Isidro": "https://urbania.pe/buscar/venta-de-departamentos-en-san-isidro--lima--lima",
}
MIN_AVISOS_VALIDOS = 5  # menos que esto -> se usa el respaldo para ese distrito


def _cargar_env(ruta: Path) -> None:
    """Carga un .env simple (KEY=VALUE) sin dependencias extra."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env(ROOT / ".env")
SCRAPER_ENABLED = os.getenv("SCRAPER_ENABLED", "true").lower() == "true"
SCRAPER_DELAY = float(os.getenv("SCRAPER_DELAY_SECONDS", "2"))
SCRAPER_TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT_SECONDS", "6"))
SCRAPER_UA = os.getenv("SCRAPER_USER_AGENT") or "EVI.IA-research-bot/0.1 (proyecto academico)"

# ---------------------------------------------------------------------------
# Módulo 1 — Estudio de mercado
# ---------------------------------------------------------------------------
RE_PRECIO = re.compile(r"(?:US\$|USD|\$)\s?([\d][\d.,]{3,})", re.IGNORECASE)
RE_SOLES = re.compile(r"S/\.?\s?([\d][\d.,]{3,})")
RE_AREA = re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s?m(?:²|2)", re.IGNORECASE)
TIPO_CAMBIO_SCRAPING = float(os.getenv("TIPO_CAMBIO", "3.75"))  # para avisos solo en soles


def _a_numero(texto: str) -> float:
    """'150,000' / '150.000' / '150,000.00' -> 150000.0"""
    texto = re.sub(r"[.,]\d{2}$", "", texto.strip())  # descarta centavos
    return float(re.sub(r"\D", "", texto))


def _robots_permite(url: str) -> bool:
    """Respeta robots.txt; si no se puede leer, no se scrapea."""
    partes = urlparse(url)
    rp = RobotFileParser()
    try:
        r = requests.get(
            f"{partes.scheme}://{partes.netloc}/robots.txt",
            headers={"User-Agent": SCRAPER_UA},
            timeout=SCRAPER_TIMEOUT,
        )
        if r.status_code >= 400:
            return r.status_code == 404  # sin robots.txt = permitido
        rp.parse(r.text.splitlines())
        return rp.can_fetch(SCRAPER_UA, url)
    except requests.RequestException:
        return False


def _precio_usd(texto: str) -> float | None:
    """'S/ 489,600 · USD 144,000' -> 144000; 'S/ 435,000' -> 435000 / tipo de cambio."""
    if m := RE_PRECIO.search(texto):
        return _a_numero(m.group(1))
    if m := RE_SOLES.search(texto):
        return _a_numero(m.group(1)) / TIPO_CAMBIO_SCRAPING
    return None


def parsear_avisos(html: str, distrito: str, fuente: str) -> pd.DataFrame:
    """Extrae (precio, área) de las tarjetas de aviso.

    1) Selectores data-qa del portal (tarjetas 'posting PROPERTY'; se descartan los
       proyectos en planos 'DEVELOPMENT' porque solo muestran rangos).
    2) Si el portal cambia su HTML, heurística genérica sobre bloques de texto cortos.
    """
    soup = BeautifulSoup(html, "html.parser")
    vistos: set[tuple[float, float]] = set()

    tarjetas = [c for c in soup.select('[data-id][data-qa^="posting"]') if "DEVELOPMENT" not in c["data-qa"]]
    if tarjetas:
        candidatos = []
        for c in tarjetas:
            el_p = c.select_one('[data-qa="POSTING_CARD_PRICE"]')
            el_f = c.select_one('[data-qa="POSTING_CARD_FEATURES"]')
            if el_p and el_f:
                candidatos.append((el_p.get_text(" ", strip=True), el_f.get_text(" ", strip=True)))
    else:
        candidatos = [(t, t) for t in (tag.get_text(" ", strip=True) for tag in soup.find_all(["article", "div", "li"]))
                      if len(t) <= 400]

    for txt_precio, txt_area in candidatos:
        m_area = RE_AREA.search(txt_area)
        try:
            precio = _precio_usd(txt_precio)
            area = float(m_area.group(1).replace(",", ".")) if m_area else None
        except ValueError:
            continue
        if precio and area and 20 <= area <= 500 and 30_000 <= precio <= 3_000_000 and 500 <= precio / area <= 8_000:
            vistos.add((round(precio, -2), area))

    df = pd.DataFrame(sorted(vistos), columns=["precio_usd", "area_m2"])
    if df.empty:
        return df
    df["distrito"] = distrito
    df["tipo"] = "Departamento"
    df["fuente"] = fuente
    df["precio_m2_usd"] = (df["precio_usd"] / df["area_m2"]).round(0)
    return df


def scrapear_distrito(distrito: str) -> tuple[pd.DataFrame, str]:
    url = FUENTES_SCRAPING[distrito]
    if not _robots_permite(url):
        return pd.DataFrame(), "robots.txt no permite o no responde"
    time.sleep(SCRAPER_DELAY)  # pausa cortés entre robots.txt y la página
    try:
        r = requests.get(
            url,
            headers={"User-Agent": SCRAPER_UA, "Accept-Language": "es-PE,es;q=0.9"},
            timeout=SCRAPER_TIMEOUT,
        )
    except requests.RequestException as e:
        return pd.DataFrame(), f"error de red ({type(e).__name__})"
    if r.status_code != 200:
        return pd.DataFrame(), f"HTTP {r.status_code}"
    df = parsear_avisos(r.text, distrito, f"scraping:{urlparse(url).netloc}")
    if len(df) < MIN_AVISOS_VALIDOS:
        return pd.DataFrame(), f"solo {len(df)} avisos válidos"
    return df, f"en vivo ({len(df)} avisos)"


@st.cache_data(show_spinner=False)
def cargar_respaldo() -> pd.DataFrame:
    return pd.read_csv(RESPALDO_CSV)


@st.cache_data(ttl=3600, show_spinner=False)
def obtener_mercado(intentar_scraping: bool) -> tuple[pd.DataFrame, dict[str, str]]:
    """Scraping por distrito con fallback al CSV. Nunca lanza excepción."""
    respaldo = cargar_respaldo()
    partes, estados = [], {}
    for i, distrito in enumerate(DISTRITOS):
        df_live, estado = pd.DataFrame(), "scraping desactivado"
        if intentar_scraping:
            if i > 0:
                time.sleep(SCRAPER_DELAY)  # pausa entre distritos
            try:
                df_live, estado = scrapear_distrito(distrito)
            except Exception as e:  # el demo nunca debe caerse
                df_live, estado = pd.DataFrame(), f"error inesperado ({type(e).__name__})"
        if not df_live.empty:
            partes.append(df_live)
            estados[distrito] = f"🟢 {estado}"
        else:
            partes.append(respaldo[respaldo["distrito"] == distrito])
            estados[distrito] = f"🟡 respaldo CSV — {estado}"
    return pd.concat(partes, ignore_index=True), estados


def referencias_distrito(mercado: pd.DataFrame, respaldo: pd.DataFrame) -> pd.DataFrame:
    """Medianas por distrito. El alquiler/m² sale del respaldo (los avisos de venta no lo traen)."""
    ref = mercado.groupby("distrito").agg(
        avisos=("precio_usd", "size"),
        precio_m2_mediana=("precio_m2_usd", "median"),
        precio_mediana=("precio_usd", "median"),
    )
    ref["alquiler_m2_mediana"] = respaldo.groupby("distrito")["alquiler_m2_usd"].median()
    ref["rentab_bruta_pct"] = ref["alquiler_m2_mediana"] * 12 / ref["precio_m2_mediana"] * 100
    return ref.reindex(DISTRITOS)


# ---------------------------------------------------------------------------
# Módulo 2 — Rentabilidad financiera
# ---------------------------------------------------------------------------
def van(tasa: float, flujos: list[float]) -> float:
    return sum(f / (1 + tasa) ** t for t, f in enumerate(flujos))


def tir(flujos: list[float]) -> float | None:
    """TIR por bisección (evita depender de numpy-financial)."""
    lo, hi = -0.99, 1.0
    if van(lo, flujos) * van(hi, flujos) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if van(lo, flujos) * van(mid, flujos) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def calcular_rentabilidad(p: dict) -> tuple[dict, pd.DataFrame]:
    precio = p["precio_usd"]

    # Inversión total
    base_alcabala_usd = max(0.0, precio - 10 * p["uit_soles"] / p["tipo_cambio"])
    alcabala = 0.0 if p["primera_venta"] else 0.03 * base_alcabala_usd
    gastos_cierre = precio * p["gastos_notariales_pct"] / 100
    inversion_total = precio + alcabala + gastos_cierre + p["remodelacion_usd"]

    # Año 1
    ingreso_bruto = p["alquiler_mensual_usd"] * 12
    ingreso_efectivo = ingreso_bruto * (1 - p["vacancia_pct"] / 100)
    gastos_op = (
        p["arbitrios_predial_usd"]
        + ingreso_efectivo * p["mantenimiento_pct"] / 100
        + ingreso_efectivo * p["administracion_pct"] / 100
    )
    noi_1 = ingreso_efectivo - gastos_op
    cap_rate = noi_1 / precio * 100

    filas = [{"Año": 0, "Ingreso efectivo": 0.0, "Gastos operativos": 0.0, "NOI": 0.0,
              "Impuesto renta": 0.0, "Venta neta": 0.0, "Flujo de caja": -inversion_total}]
    for anio in range(1, 6):
        ing = ingreso_efectivo * (1 + p["crec_alquiler_pct"] / 100) ** (anio - 1)
        gas = gastos_op * (1 + p["inflacion_pct"] / 100) ** (anio - 1)
        noi = ing - gas
        # Renta de 1ra categoría (persona natural): 5 % efectivo sobre la renta bruta
        imp = ing * 0.05 if p["aplicar_ir"] else 0.0
        venta_neta = 0.0
        if anio == 5:
            valor_venta = precio * (1 + p["apreciacion_pct"] / 100) ** 5
            costos_venta = valor_venta * p["costo_venta_pct"] / 100
            ganancia = max(0.0, valor_venta - costos_venta - inversion_total)
            imp_ganancia = ganancia * 0.05 if p["aplicar_ir"] else 0.0
            venta_neta = valor_venta - costos_venta - imp_ganancia
        filas.append({"Año": anio, "Ingreso efectivo": ing, "Gastos operativos": gas, "NOI": noi,
                      "Impuesto renta": imp, "Venta neta": venta_neta,
                      "Flujo de caja": noi - imp + venta_neta})

    flujo = pd.DataFrame(filas)
    flujos = flujo["Flujo de caja"].tolist()
    acumulado = flujo["Flujo de caja"].cumsum()
    payback = next((int(a) for a, c in zip(flujo["Año"], acumulado) if c >= 0 and a > 0), None)
    flujo["Acumulado"] = acumulado

    kpis = {
        "inversion_total": inversion_total,
        "alcabala": alcabala,
        "gastos_cierre": gastos_cierre,
        "noi_1": noi_1,
        "cap_rate": cap_rate,
        "cash_on_cash": (noi_1 - (ingreso_efectivo * 0.05 if p["aplicar_ir"] else 0)) / inversion_total * 100,
        "tir": tir(flujos),
        "van": van(p["tasa_descuento_pct"] / 100, flujos),
        "payback": payback,
    }
    return kpis, flujo


# ---------------------------------------------------------------------------
# Módulo 3 — Alertas legales
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def cargar_legal() -> dict:
    return json.loads(LEGAL_JSON.read_text(encoding="utf-8"))


SITUACIONES = {
    "fabrica_no_declarada": "El área real/construida no coincide con la inscrita en SUNARP",
    "sin_independizar": "La unidad no está independizada (comparte partida)",
    "cargas_gravamenes": "La partida tiene hipoteca, embargo u otra carga",
    "deuda_arbitrios": "El vendedor tiene deuda de predial o arbitrios",
    "sucesion": "El vendedor heredó el inmueble y la sucesión no está inscrita",
    "cambio_uso": "Quiero usarlo como oficina/comercio (no vivienda)",
}
PESO_RIESGO = {"fabrica_no_declarada": 30, "sin_independizar": 30, "cargas_gravamenes": 25,
               "deuda_arbitrios": 10, "sucesion": 20, "cambio_uso": 15}
BASE_RIESGO = {"Bajo": 5, "Medio": 15, "Alto": 25}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
def fmt_usd(x: float) -> str:
    return f"US$ {x:,.0f}"


def sidebar(ref: pd.DataFrame) -> dict:
    sb = st.sidebar
    sb.header("Inmueble a evaluar")
    distrito = sb.selectbox("Distrito", DISTRITOS)
    area = sb.number_input("Área (m²)", min_value=20.0, max_value=1000.0, value=80.0, step=5.0)
    precio_ref = float(ref.loc[distrito, "precio_m2_mediana"] * area)
    precio = sb.number_input("Precio de compra (USD)", min_value=10_000.0, max_value=5_000_000.0,
                             value=round(precio_ref, -3), step=1_000.0)
    alquiler_ref = float(ref.loc[distrito, "alquiler_m2_mediana"] * area)
    alquiler = sb.number_input("Alquiler mensual esperado (USD)", min_value=0.0,
                               value=round(alquiler_ref, -1), step=10.0,
                               help=f"Sugerido por mediana del distrito: {fmt_usd(alquiler_ref)}")

    legal = cargar_legal()["distritos"][distrito]
    arb_min, arb_max = legal["arbitrios_predial_anual_usd"]

    with sb.expander("Supuestos financieros"):
        tc = st.number_input("Tipo de cambio (S/ por USD)", value=3.75, step=0.01)
        uit = st.number_input("UIT vigente (S/)", value=5_350.0, step=50.0,
                              help="Actualiza al valor de la UIT del año de compra.")
        primera_venta = st.checkbox("Primera venta de constructora (sin alcabala)", value=False)
        gastos_not = st.slider("Gastos notariales y registrales (%)", 0.0, 3.0, 1.0, 0.1)
        remodelacion = st.number_input("Remodelación / amoblado (USD)", value=0.0, step=500.0)
        vacancia = st.slider("Vacancia (%)", 0.0, 30.0, 8.0, 0.5)
        arbitrios = st.number_input("Predial + arbitrios anual (USD)",
                                    value=float((arb_min + arb_max) / 2), step=10.0)
        mant = st.slider("Mantenimiento (% del ingreso)", 0.0, 20.0, 5.0, 0.5)
        admin = st.slider("Administración (% del ingreso)", 0.0, 15.0, 0.0, 0.5)
        crec = st.slider("Crecimiento anual del alquiler (%)", 0.0, 10.0, 2.5, 0.5)
        infl = st.slider("Inflación de gastos (%)", 0.0, 10.0, 2.5, 0.5)
        aprec = st.slider("Apreciación anual del inmueble (%)", -5.0, 10.0, 3.0, 0.5)
        costo_venta = st.slider("Costo de venta al año 5 (comisión, %)", 0.0, 6.0, 3.0, 0.5)
        tasa = st.slider("Tasa de descuento para VAN (%)", 0.0, 20.0, 8.0, 0.5)
        aplicar_ir = st.checkbox("Aplicar impuesto a la renta (persona natural)", value=True)

    return {
        "distrito": distrito, "area_m2": area, "precio_usd": precio,
        "alquiler_mensual_usd": alquiler, "tipo_cambio": tc, "uit_soles": uit,
        "primera_venta": primera_venta, "gastos_notariales_pct": gastos_not,
        "remodelacion_usd": remodelacion, "vacancia_pct": vacancia,
        "arbitrios_predial_usd": arbitrios, "mantenimiento_pct": mant,
        "administracion_pct": admin, "crec_alquiler_pct": crec, "inflacion_pct": infl,
        "apreciacion_pct": aprec, "costo_venta_pct": costo_venta,
        "tasa_descuento_pct": tasa, "aplicar_ir": aplicar_ir,
    }


def tab_mercado(mercado: pd.DataFrame, estados: dict, ref: pd.DataFrame, p: dict) -> None:
    st.subheader("📊 Estudio de mercado — Lince, Jesús María y San Isidro")
    cols = st.columns(len(DISTRITOS))
    for col, d in zip(cols, DISTRITOS):
        col.metric(d, f"US$ {ref.loc[d, 'precio_m2_mediana']:,.0f}/m²",
                   help=f"Mediana precio/m² · {int(ref.loc[d, 'avisos'])} avisos")
        col.caption(estados[d])

    fig = px.box(mercado, x="distrito", y="precio_m2_usd", color="distrito", points="all",
                 category_orders={"distrito": DISTRITOS},
                 labels={"precio_m2_usd": "Precio por m² (USD)", "distrito": ""})
    precio_m2_user = p["precio_usd"] / p["area_m2"]
    fig.add_hline(y=precio_m2_user, line_dash="dash",
                  annotation_text=f"Tu inmueble: US$ {precio_m2_user:,.0f}/m²")
    fig.update_layout(showlegend=False, height=420)
    st.plotly_chart(fig)

    dif = (precio_m2_user / ref.loc[p["distrito"], "precio_m2_mediana"] - 1) * 100
    if dif > 10:
        st.warning(f"El precio/m² está **{dif:.0f}% sobre** la mediana de {p['distrito']}. Hay margen para negociar.")
    elif dif < -10:
        st.info(f"El precio/m² está **{abs(dif):.0f}% bajo** la mediana de {p['distrito']}. "
                "Revisa por qué (antigüedad, saneamiento, piso) en el módulo legal.")
    else:
        st.success(f"El precio/m² está en línea con el mercado de {p['distrito']} ({dif:+.0f}%).")

    tabla = ref[["avisos", "precio_m2_mediana", "alquiler_m2_mediana", "rentab_bruta_pct"]].rename(columns={
        "avisos": "Avisos", "precio_m2_mediana": "Precio/m² (USD)",
        "alquiler_m2_mediana": "Alquiler/m²/mes (USD)", "rentab_bruta_pct": "Rentab. bruta (%)"})
    st.dataframe(tabla.style.format({"Precio/m² (USD)": "{:,.0f}", "Alquiler/m²/mes (USD)": "{:.2f}",
                                     "Rentab. bruta (%)": "{:.1f}"}))
    with st.expander("Ver avisos del distrito seleccionado"):
        st.dataframe(mercado[mercado["distrito"] == p["distrito"]], hide_index=True)
    if (mercado["fuente"] == "respaldo_sintetico").any():
        st.caption("ℹ️ Los datos de respaldo son sintéticos y referenciales (ver `scripts/generar_respaldo.py`). "
                   "El alquiler/m² siempre proviene del respaldo.")


def tab_rentabilidad(p: dict, ref: pd.DataFrame) -> dict:
    st.subheader("💰 Rentabilidad financiera")
    k, flujo = calcular_rentabilidad(p)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Inversión total", fmt_usd(k["inversion_total"]),
              help=f"Precio + alcabala ({fmt_usd(k['alcabala'])}) + gastos de cierre "
                   f"({fmt_usd(k['gastos_cierre'])}) + remodelación")
    bench = ref.loc[p["distrito"], "rentab_bruta_pct"]
    c2.metric("Cap Rate", f"{k['cap_rate']:.2f} %", help="NOI año 1 / precio de compra")
    c3.metric("TIR 5 años", "n/d" if k["tir"] is None else f"{k['tir'] * 100:.2f} %")
    c4.metric(f"VAN @ {p['tasa_descuento_pct']:.1f}%", fmt_usd(k["van"]))

    c5, c6, c7 = st.columns(3)
    c5.metric("NOI año 1", fmt_usd(k["noi_1"]))
    c6.metric("Cash-on-cash año 1", f"{k['cash_on_cash']:.2f} %")
    c7.metric("Payback", f"Año {k['payback']}" if k["payback"] else "> 5 años")

    # Veredicto
    tir_ok = k["tir"] is not None and k["tir"] * 100 >= p["tasa_descuento_pct"]
    if tir_ok and k["cap_rate"] >= 5:
        st.success("✅ **Viable:** la TIR supera tu tasa de descuento y el Cap Rate es sólido (≥ 5 %).")
    elif tir_ok or k["cap_rate"] >= 4:
        st.warning("⚠️ **Viable con reservas:** revisa precio de compra, alquiler esperado o costos.")
    else:
        st.error("❌ **No viable** con estos supuestos: la TIR no cubre tu costo de oportunidad.")
    st.caption(f"Referencia: rentabilidad bruta mediana de {p['distrito']} ≈ {bench:.1f} %.")

    fig = go.Figure()
    fig.add_bar(x=flujo["Año"], y=flujo["Flujo de caja"], name="Flujo de caja",
                marker_color=["#c0392b" if v < 0 else "#2e86c1" for v in flujo["Flujo de caja"]])
    fig.add_scatter(x=flujo["Año"], y=flujo["Acumulado"], name="Acumulado", mode="lines+markers")
    fig.update_layout(height=400, xaxis_title="Año", yaxis_title="USD", legend=dict(orientation="h"))
    st.plotly_chart(fig)

    st.dataframe(flujo.set_index("Año").style.format("{:,.0f}"))
    st.caption("El año 5 incluye la venta del inmueble (apreciación − comisión − 5 % de impuesto a la "
               "ganancia de capital si aplica). No incluye financiamiento hipotecario.")
    return k


def tab_legal(p: dict) -> None:
    st.subheader("⚖️ Alertas legales y permisos")
    base = cargar_legal()
    info = base["distritos"][p["distrito"]]

    st.markdown(f"**{info['municipalidad']}** · Riesgo base del distrito: **{info['nivel_riesgo_base']}**")
    for r in info["riesgos"]:
        st.markdown(f"- ⚠️ {r}")
    st.info(f"💡 {info['recomendacion']}")

    st.markdown("#### ¿Qué sabes del inmueble?")
    marcadas = [clave for clave, texto in SITUACIONES.items() if st.checkbox(texto, key=f"sit_{clave}")]

    score = min(100, BASE_RIESGO[info["nivel_riesgo_base"]] + sum(PESO_RIESGO[m] for m in marcadas))
    nivel = "🟢 Bajo" if score < 30 else "🟡 Medio" if score < 60 else "🔴 Alto"
    c1, c2 = st.columns(2)
    c1.metric("Riesgo de saneamiento", f"{score}/100", nivel)

    # Los opcionales (p. ej. parámetros urbanísticos) solo aplican si hay cambio de uso.
    tramites = [t for t in base["tramites_generales"] if t["obligatorio"] or "cambio_uso" in marcadas]
    df = pd.DataFrame(tramites)[["tramite", "entidad", "dias_min", "dias_max", "para_que"]]
    extra = pd.DataFrame([base["tramites_saneamiento"][m] for m in marcadas])
    if not extra.empty:
        extra["para_que"] = "Saneamiento previo a la compra"
        df = pd.concat([extra, df], ignore_index=True)

    # Los trámites de saneamiento van en serie antes de la compra; el resto, en su mayoría secuenciales.
    c2.metric("Tiempo estimado hasta inscribir", f"{df['dias_min'].sum()}–{df['dias_max'].sum()} días")

    st.dataframe(df.rename(columns={"tramite": "Trámite", "entidad": "Entidad", "dias_min": "Días mín.",
                                    "dias_max": "Días máx.", "para_que": "Para qué"}), hide_index=True)
    st.caption(f"⚖️ {base['_aviso']}")


def main() -> None:
    st.set_page_config(page_title="EVI.IA", page_icon="🏢", layout="wide")

    st.sidebar.title("🏢 EVI.IA")
    st.sidebar.caption("Evaluador de Viabilidad de Inversión Inmobiliaria")
    intentar = st.sidebar.toggle("Intentar scraping en vivo", value=SCRAPER_ENABLED,
                                 help="Si falla, se usa data/inmuebles_respaldo.csv")
    with st.spinner("Consultando el mercado (con pausas entre requests)..."):
        mercado, estados = obtener_mercado(intentar)
    ref = referencias_distrito(mercado, cargar_respaldo())

    p = sidebar(ref)

    st.title("EVI.IA — Evaluador de Viabilidad de Inversión Inmobiliaria")
    st.caption(f"{p['distrito']} · {p['area_m2']:.0f} m² · {fmt_usd(p['precio_usd'])} · "
               f"alquiler {fmt_usd(p['alquiler_mensual_usd'])}/mes")

    t1, t2, t3 = st.tabs(["📊 Mercado", "💰 Rentabilidad", "⚖️ Legal"])
    with t1:
        tab_mercado(mercado, estados, ref, p)
    with t2:
        tab_rentabilidad(p, ref)
    with t3:
        tab_legal(p)


if __name__ == "__main__":
    main()
