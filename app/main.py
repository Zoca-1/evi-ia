"""EVI.IA — Evaluador de Viabilidad de Inversión Inmobiliaria con IA.

Módulos:
    1. Estudio de Mercado   (dataset local data/inmuebles_respaldo.csv, actualizable en segundo plano)
    2. Rentabilidad          (Cap Rate, inversión total, flujo de caja a 5 años)
    3. Alertas Legales       (base local de trámites y riesgos de saneamiento)

La app NUNCA hace scraping en vivo: lee el CSV local (respuesta instantánea, sin riesgo
de HTTP 403 durante el demo). El refresco lo hace scripts/actualizar_dataset_real.py,
lanzado desde la barra lateral como proceso independiente.

Ejecutar desde la raíz del repo:
    streamlit run app/main.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESPALDO_CSV = DATA_DIR / "inmuebles_respaldo.csv"
METADATA_JSON = DATA_DIR / "mercado_metadata.json"
LEGAL_JSON = DATA_DIR / "legal_distritos.json"
SCRIPT_ACTUALIZAR = ROOT / "scripts" / "actualizar_dataset_real.py"
LOCK = DATA_DIR / ".actualizacion.lock"
LOG_ACTUALIZACION = DATA_DIR / "actualizacion.log"
LOCK_VENCE_MIN = 20  # un lock más viejo que esto se considera huérfano

DISTRITOS = ["Lince", "Jesús María", "San Isidro"]


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
PERMITIR_ACTUALIZAR = os.getenv("PERMITIR_ACTUALIZAR_DATASET", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Módulo 1 — Estudio de mercado (lectura local + actualización en segundo plano)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _leer_csv(ruta: str, mtime: float) -> pd.DataFrame:  # mtime en la firma = caché se invalida al cambiar el archivo
    return pd.read_csv(ruta)


def cargar_mercado() -> pd.DataFrame:
    return _leer_csv(str(RESPALDO_CSV), RESPALDO_CSV.stat().st_mtime)


def cargar_metadata() -> dict:
    try:
        return json.loads(METADATA_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def actualizacion_en_curso() -> bool:
    if not LOCK.exists():
        return False
    edad_min = (datetime.now().timestamp() - LOCK.stat().st_mtime) / 60
    return edad_min < LOCK_VENCE_MIN


def lanzar_actualizacion() -> None:
    """Ejecuta el script de scraping como proceso independiente (no bloquea la app)."""
    # El lock se crea aquí (no solo en el script) para que un doble clic no lance dos procesos.
    LOCK.write_text(json.dumps({"lanzado_por": "app", "inicio": datetime.now().isoformat(timespec="seconds")}))
    log = open(LOG_ACTUALIZACION, "w", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen([sys.executable, str(SCRIPT_ACTUALIZAR)], cwd=ROOT, stdout=log,
                     stderr=subprocess.STDOUT, creationflags=flags,
                     env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _fmt_fecha(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M") if iso else "—"


def referencias_distrito(df: pd.DataFrame) -> pd.DataFrame:
    """Medianas por distrito: precio/m² de los avisos de venta y alquiler/m² de los de alquiler."""
    venta, alquiler = df[df["operacion"] == "venta"], df[df["operacion"] == "alquiler"]
    ref = venta.groupby("distrito").agg(
        avisos=("precio_usd", "size"),
        precio_m2_mediana=("precio_m2_usd", "median"),
        precio_mediana=("precio_usd", "median"),
    )
    ref["avisos_alquiler"] = alquiler.groupby("distrito").size()
    ref["alquiler_m2_mediana"] = alquiler.groupby("distrito")["precio_m2_usd"].median()
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


ENTIDADES = ["Notaría", "SUNARP", "Municipalidad"]
OPCIONES_INDEP = ["Independizada (partida propia)", "En trámite", "No independizada (partida matriz)", "No sé"]
TRIESTADO = ["No", "Sí", "No sé"]


def diagnosticar(d: dict, distrito: str, base: dict) -> tuple[int, pd.DataFrame, list[str]]:
    """Score de Riesgo Burocrático (0-100) a partir de los parámetros del inmueble.

    d: antiguedad, independizacion, uso_inscrito, uso_previsto, pisos_construidos,
       pisos_declarados, cargas_gravamenes, deuda_arbitrios, sucesion.
    Devuelve (score, desglose por factor, condiciones que disparan trámites).
    """
    w, info = base["pesos_riesgo"], base["distritos"][distrito]
    factores: list[tuple[str, int, str]] = []
    condiciones: list[str] = []

    factores.append(("Distrito", info["riesgo_base_puntos"], f"Riesgo base {info['nivel_riesgo_base']}"))

    pts_ant = next(p for tope, p in w["antiguedad"] if d["antiguedad"] < tope)
    factores.append(("Antigüedad", pts_ant, f"{d['antiguedad']} años"))

    factores.append(("Independización SUNARP", w["independizacion"][d["independizacion"]], d["independizacion"]))
    if d["independizacion"] == "No independizada (partida matriz)":
        condiciones.append("sin_independizar")
    elif d["independizacion"] == "En trámite":
        condiciones.append("independizacion_en_tramite")

    extra_pisos = max(0, d["pisos_construidos"] - d["pisos_declarados"])
    pts_pisos = 0
    if extra_pisos:
        pts_pisos = min(w["pisos_no_declarados_max"],
                        w["pisos_no_declarados_base"] + w["pisos_no_declarados_por_piso"] * (extra_pisos - 1))
        condiciones.append("fabrica_no_declarada")
    factores.append(("Pisos no declarados", pts_pisos,
                     f"{d['pisos_construidos']} construidos vs {d['pisos_declarados']} en SUNARP"))

    clave_uso = f"{d['uso_inscrito']}→{d['uso_previsto']}"
    pts_uso = w["uso"][clave_uso]
    if d["uso_previsto"] == "Comercial":
        pts_uso += info["recargo_uso_comercial"]
        condiciones.append("uso_comercial")
    if d["uso_inscrito"] != d["uso_previsto"]:
        condiciones.append("cambio_uso")
    factores.append(("Uso inscrito → previsto", pts_uso, clave_uso))

    etiquetas = {"cargas_gravamenes": "Cargas / gravámenes", "deuda_arbitrios": "Deuda municipal",
                 "sucesion": "Sucesión no inscrita"}
    for clave, etiqueta in etiquetas.items():
        factores.append((etiqueta, w["triestado"][clave][d[clave]], d[clave]))
        if d[clave] == "Sí":
            condiciones.append(clave)

    desglose = pd.DataFrame(factores, columns=["Factor", "Puntos", "Detalle"])
    score = int(min(100, desglose["Puntos"].sum()))
    return score, desglose, condiciones


def ruta_critica(condiciones: list[str], base: dict, escenario: str = "dias_max") -> pd.DataFrame:
    """Cronograma de trámites con inicio/fin por fase.

    Fase 1: las entidades avanzan en paralelo (cada entidad encadena sus trámites);
    la fase dura lo que tarde la entidad más lenta. Fases 2-4: secuenciales.
    Fase 5 (post-compra) no forma parte de la ruta crítica hasta la inscripción.
    """
    tramites = list(base["tramites_generales"])
    # Orden lógico del saneamiento: titulares correctos → fábrica → independización → uso → cargas → deudas
    orden = ["sucesion", "fabrica_no_declarada", "sin_independizar", "independizacion_en_tramite",
             "cambio_uso", "uso_comercial", "cargas_gravamenes", "deuda_arbitrios"]
    for c in sorted(condiciones, key=lambda x: orden.index(x) if x in orden else len(orden)):
        tramites += base["tramites_condicionales"].get(c, [])

    filas, t0 = [], 0
    for fase in sorted(base["_fases"]):
        de_fase = [t for t in tramites if t["fase"] == fase]
        if not de_fase:
            continue
        paralelo = fase == "1_due_diligence"
        fin_fase = t0
        cursor = {e: t0 for e in ENTIDADES}
        for t in de_fase:
            inicio = cursor[t["entidad"]] if paralelo else fin_fase
            fin = inicio + t[escenario]
            if paralelo:
                cursor[t["entidad"]] = fin
            filas.append({**t, "inicio": inicio, "fin": fin, "fase_nombre": base["_fases"][fase]})
            fin_fase = max(fin_fase, fin)
        if fase != "5_post_compra":
            t0 = fin_fase

    df = pd.DataFrame(filas)
    df["duracion"] = df["fin"] - df["inicio"]
    # En la fase paralela solo es crítica la cadena de la entidad que termina última.
    fase1 = df["fase"] == "1_due_diligence"
    if fase1.any():
        fin1 = df.loc[fase1, "fin"].max()
        ent_critica = df.loc[fase1 & (df["fin"] == fin1), "entidad"].iloc[0]
        df["critico"] = ~fase1 | (df["entidad"] == ent_critica)
    else:
        df["critico"] = True
    df.loc[df["fase"] == "5_post_compra", "critico"] = False
    return df


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
def fmt_usd(x: float) -> str:
    return f"US$ {x:,.0f}"


def fmt_pen(x: float) -> str:
    return f"S/ {x:,.0f}"


def metrica_dual(col, etiqueta: str, usd: float, tc: float, help: str | None = None) -> None:
    """Métrica en USD con su equivalente en soles debajo."""
    col.metric(etiqueta, fmt_usd(usd), help=help)
    col.caption(f"≈ **{fmt_pen(usd * tc)}**")


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


ICONO_FUENTE = {"real": "🟢", "real (anterior)": "🟢", "sintetico": "🟡"}


def tab_mercado(mercado: pd.DataFrame, meta: dict, ref: pd.DataFrame, p: dict) -> None:
    st.subheader("📊 Estudio de mercado — Lince, Jesús María y San Isidro")
    segs = meta.get("segmentos", {})
    cols = st.columns(len(DISTRITOS))
    for col, d in zip(cols, DISTRITOS):
        col.metric(d, f"US$ {ref.loc[d, 'precio_m2_mediana']:,.0f}/m²",
                   help=f"Mediana precio/m² de venta · {int(ref.loc[d, 'avisos'])} avisos")
        for op in ("venta", "alquiler"):
            s = segs.get(f"{d}|{op}", {})
            fuente = s.get("fuente", "sintetico")
            col.caption(f"{ICONO_FUENTE.get(fuente, '⚪')} {op}: {s.get('filas', '?')} avisos · "
                        f"{'real' if fuente.startswith('real') else 'sintético'} · {_fmt_fecha(s.get('actualizado'))}")

    venta = mercado[mercado["operacion"] == "venta"]
    fig = px.box(venta, x="distrito", y="precio_m2_usd", color="distrito", points="all",
                 category_orders={"distrito": DISTRITOS}, hover_data=["area_m2", "precio_usd", "tipologia"],
                 labels={"precio_m2_usd": "Precio de venta por m² (USD)", "distrito": ""})
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

    tabla = ref[["avisos", "precio_m2_mediana", "avisos_alquiler", "alquiler_m2_mediana", "rentab_bruta_pct"]].rename(
        columns={"avisos": "Avisos venta", "precio_m2_mediana": "Precio/m² (USD)", "avisos_alquiler": "Avisos alquiler",
                 "alquiler_m2_mediana": "Alquiler/m²/mes (USD)", "rentab_bruta_pct": "Rentab. bruta (%)"})
    st.dataframe(tabla.style.format({"Precio/m² (USD)": "{:,.0f}", "Alquiler/m²/mes (USD)": "{:.2f}",
                                     "Rentab. bruta (%)": "{:.1f}"}))

    st.markdown(f"##### Distribución por tipología — {p['distrito']}")
    sub = mercado[mercado["distrito"] == p["distrito"]]
    dist = (sub.groupby(["operacion", "tipologia"]).agg(avisos=("id", "size"), mediana_m2=("precio_m2_usd", "median"))
            .reset_index())
    dist["participacion_pct"] = dist["avisos"] / dist.groupby("operacion")["avisos"].transform("sum") * 100
    orden_tipos = ["Monoambiente", "1 dorm.", "2 dorm.", "3+ dorm.", "Sin dato"]
    fig_t = px.bar(dist, x="tipologia", y="participacion_pct", color="operacion", barmode="group",
                   category_orders={"tipologia": orden_tipos}, hover_data=["avisos", "mediana_m2"],
                   labels={"participacion_pct": "% de avisos", "tipologia": "", "operacion": "Operación"})
    fig_t.update_layout(height=320)
    st.plotly_chart(fig_t)

    with st.expander("Ver avisos del distrito seleccionado"):
        st.dataframe(sub, hide_index=True)
    if (mercado["fuente"] == "respaldo_sintetico").any():
        st.caption("ℹ️ Algunos segmentos usan la base sintética inicial (`scripts/generar_respaldo.py`). "
                   "Actualiza el dataset desde la barra lateral para reemplazarlos con avisos reales.")


def tab_rentabilidad(p: dict, ref: pd.DataFrame) -> dict:
    st.subheader("💰 Rentabilidad financiera — retorno del inversionista comprador")
    tc = p["tipo_cambio"]
    k, flujo = calcular_rentabilidad(p)

    st.info(
        "📌 **¿De quién es este retorno?** Todas las métricas de esta pestaña (Cap Rate, TIR, VAN, "
        "Payback, NOI y flujo de caja) miden el retorno del **INVERSIONISTA COMPRADOR** bajo un "
        "**modelo de alquiler + apreciación a 5 años**: compra el inmueble al contado (año 0), lo "
        "alquila durante 5 años y lo vende al final del año 5. **No** es la ganancia del vendedor, "
        "del corredor ni de una inmobiliaria desarrolladora, y no incluye hipoteca."
    )
    st.caption(f"Montos en USD con su equivalente en soles al tipo de cambio de la barra lateral: "
               f"**S/ {tc:.2f} por US$ 1**.")

    st.markdown("##### Inversión del comprador (año 0)")
    c1, c2, c3, c4 = st.columns(4)
    metrica_dual(c1, "Precio de compra", p["precio_usd"], tc)
    metrica_dual(c2, "Alcabala", k["alcabala"], tc, help="3 % sobre el valor que exceda 10 UIT")
    metrica_dual(c3, "Gastos de cierre + remodelación", k["gastos_cierre"] + p["remodelacion_usd"], tc,
                 help="Notaría, registros y remodelación/amoblado")
    metrica_dual(c4, "Precio total (inversión total)", k["inversion_total"], tc,
                 help="Precio + alcabala + gastos de cierre + remodelación")

    st.markdown("##### Retorno del comprador por alquiler y apreciación (horizonte 5 años)")
    bench = ref.loc[p["distrito"], "rentab_bruta_pct"]
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Cap Rate (comprador)", f"{k['cap_rate']:.2f} %",
              help="NOI del año 1 por alquiler / precio de compra. No incluye apreciación.")
    c6.metric("TIR 5 años (comprador)", "n/d" if k["tir"] is None else f"{k['tir'] * 100:.2f} %",
              help="Tasa que iguala a cero los flujos del comprador: inversión, 5 años de alquiler y venta al año 5.")
    metrica_dual(c7, f"VAN @ {p['tasa_descuento_pct']:.1f}% (comprador)", k["van"], tc,
                 help="Valor presente de los flujos del comprador descontados a su costo de oportunidad.")
    c8.metric("Payback (comprador)", f"Año {k['payback']}" if k["payback"] else "> 5 años",
              help="Año en que el flujo acumulado del comprador se vuelve positivo (incluye la venta al año 5).")

    c9, c10, _, _ = st.columns(4)
    metrica_dual(c9, "NOI año 1", k["noi_1"], tc, help="Ingreso por alquiler − vacancia − gastos operativos")
    c10.metric("Cash-on-cash año 1", f"{k['cash_on_cash']:.2f} %",
               help="Flujo del año 1 después de impuestos / inversión total")

    # Veredicto
    tir_ok = k["tir"] is not None and k["tir"] * 100 >= p["tasa_descuento_pct"]
    if tir_ok and k["cap_rate"] >= 5:
        st.success("✅ **Viable:** la TIR supera tu tasa de descuento y el Cap Rate es sólido (≥ 5 %).")
    elif tir_ok or k["cap_rate"] >= 4:
        st.warning("⚠️ **Viable con reservas:** revisa precio de compra, alquiler esperado o costos.")
    else:
        st.error("❌ **No viable** con estos supuestos: la TIR no cubre tu costo de oportunidad.")
    st.caption(f"Referencia: rentabilidad bruta mediana de {p['distrito']} ≈ {bench:.1f} %.")

    st.markdown("##### Flujo de caja del inversionista comprador a 5 años")
    moneda = st.radio("Moneda del gráfico", ["USD", "PEN"], horizontal=True, key="moneda_flujo")
    factor, simbolo = (1.0, "USD") if moneda == "USD" else (tc, "PEN (S/)")
    fig = go.Figure()
    fig.add_bar(x=flujo["Año"], y=flujo["Flujo de caja"] * factor, name="Flujo de caja",
                marker_color=["#c0392b" if v < 0 else "#2e86c1" for v in flujo["Flujo de caja"]])
    fig.add_scatter(x=flujo["Año"], y=flujo["Acumulado"] * factor, name="Acumulado", mode="lines+markers")
    fig.update_layout(height=400, xaxis_title="Año", yaxis_title=simbolo, legend=dict(orientation="h"))
    st.plotly_chart(fig)

    tabla = flujo.set_index("Año")
    tabla.columns = [f"{c} (USD)" for c in tabla.columns]
    tabla["Flujo de caja (PEN)"] = flujo.set_index("Año")["Flujo de caja"] * tc
    tabla["Acumulado (PEN)"] = flujo.set_index("Año")["Acumulado"] * tc
    st.dataframe(tabla.style.format("{:,.0f}"))
    st.caption("Año 0 = inversión total del comprador. El año 5 incluye la venta del inmueble (apreciación − "
               "comisión − 5 % de impuesto a la ganancia de capital si aplica). No incluye financiamiento hipotecario.")
    return k


def tab_legal(p: dict) -> None:
    st.subheader("⚖️ Alertas legales y permisos")
    base = cargar_legal()
    info = base["distritos"][p["distrito"]]

    st.markdown(f"**{info['municipalidad']}** · Riesgo base del distrito: **{info['nivel_riesgo_base']}**")
    for r in info["riesgos"]:
        st.markdown(f"- ⚠️ {r}")
    st.info(f"💡 {info['recomendacion']}")

    # --- Formulario de diagnóstico -------------------------------------------------
    st.markdown("#### 🩺 Diagnóstico de parámetros del inmueble")
    with st.form("diagnostico_legal"):
        f1, f2, f3 = st.columns(3)
        antiguedad = f1.number_input("Antigüedad de la edificación (años)", 0, 120, 15, 1)
        indep = f2.selectbox("Independización en SUNARP", OPCIONES_INDEP,
                             help="¿La unidad tiene partida registral propia? Revísalo en la copia literal.")
        pisos_c = f3.number_input("Pisos construidos (edificio/casa)", 1, 60, 5, 1)

        f4, f5, f6 = st.columns(3)
        uso_ins = f4.selectbox("Uso inscrito / según licencia", ["Residencial", "Comercial"])
        uso_prev = f5.selectbox("Uso previsto por el comprador", ["Residencial", "Comercial"],
                                help="Comercial = alquilar como oficina, consultorio o local.")
        pisos_d = f6.number_input("Pisos declarados en SUNARP (fábrica)", 1, 60, 5, 1,
                                  help="Si hay más pisos construidos que declarados, hay fábrica no declarada.")

        f7, f8, f9 = st.columns(3)
        cargas = f7.selectbox("¿Hipoteca, embargo u otra carga?", TRIESTADO)
        deuda = f8.selectbox("¿Deuda de predial o arbitrios?", TRIESTADO)
        sucesion = f9.selectbox("¿Vendedor heredó sin sucesión inscrita?", TRIESTADO)

        escenario = st.radio("Escenario de plazos", ["Conservador (días máx.)", "Optimista (días mín.)"],
                             horizontal=True)
        st.form_submit_button("🔍 Diagnosticar", type="primary")

    datos = {"antiguedad": int(antiguedad), "independizacion": indep, "uso_inscrito": uso_ins,
             "uso_previsto": uso_prev, "pisos_construidos": int(pisos_c), "pisos_declarados": int(pisos_d),
             "cargas_gravamenes": cargas, "deuda_arbitrios": deuda, "sucesion": sucesion}
    score, desglose, condiciones = diagnosticar(datos, p["distrito"], base)
    col_dias = "dias_max" if escenario.startswith("Conservador") else "dias_min"
    ruta = ruta_critica(condiciones, base, col_dias)

    # --- Score ------------------------------------------------------------------
    nivel = "🟢 Bajo" if score < 30 else "🟡 Medio" if score < 60 else "🔴 Alto"
    criticos = ruta[ruta["critico"]]
    dias_total = int(criticos["fin"].max())
    por_entidad = criticos.groupby("entidad")["duracion"].sum().reindex(ENTIDADES, fill_value=0)

    st.markdown("#### 📊 Resultado")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Score de Riesgo Burocrático", f"{score}/100", nivel, delta_color="off")
    c2.metric("Ruta crítica hasta inscribir", f"{dias_total} días")
    for col, ent in zip((c3, c4, c5), ENTIDADES):
        col.metric(f"Días en {ent}", f"{int(por_entidad[ent])} días",
                   help="Días que esta entidad aporta a la ruta crítica")

    g1, g2 = st.columns([2, 3])
    with g1:
        fig_s = px.bar(desglose[desglose["Puntos"] > 0].sort_values("Puntos"), x="Puntos", y="Factor",
                       orientation="h", text="Puntos", hover_data=["Detalle"],
                       title="¿De dónde sale el score?")
        fig_s.update_layout(height=360, yaxis_title="", showlegend=False)
        st.plotly_chart(fig_s)
    with g2:
        # Gantt simple: barras horizontales con base = día de inicio
        colores = {"Notaría": "#8e44ad", "SUNARP": "#2e86c1", "Municipalidad": "#d68910"}
        fig_g = go.Figure()
        for ent in ENTIDADES:
            sub = ruta[ruta["entidad"] == ent]
            if sub.empty:
                continue
            fig_g.add_bar(y=sub["tramite"], x=sub["duracion"].clip(lower=0.5), base=sub["inicio"],
                          orientation="h", name=ent, marker_color=colores[ent],
                          marker_opacity=[1.0 if c else 0.35 for c in sub["critico"]],
                          customdata=sub[["fase_nombre", "inicio", "fin"]],
                          hovertemplate="%{y}<br>%{customdata[0]}<br>Día %{customdata[1]} → %{customdata[2]}"
                                        "<extra>" + ent + "</extra>")
        fig_g.update_layout(title="Ruta crítica de trámites (barras tenues = fuera de la ruta crítica)",
                            height=360, barmode="overlay", xaxis_title="Días desde hoy",
                            yaxis=dict(autorange="reversed", title=""), legend=dict(orientation="h"))
        st.plotly_chart(fig_g)

    # --- Alertas ---------------------------------------------------------------
    alertas = {
        "fabrica_no_declarada": f"🔴 Hay {datos['pisos_construidos'] - datos['pisos_declarados']} piso(s) sin "
                                "declaratoria de fábrica: el banco puede no financiar y la regularización va antes de firmar.",
        "sin_independizar": "🔴 Sin independización no hay partida propia: no se puede inscribir la compra de la unidad sola.",
        "independizacion_en_tramite": "🟡 Independización en trámite: condiciona la firma a que se inscriba.",
        "cargas_gravamenes": "🔴 La carga debe levantarse (o pagarse con el precio en la notaría) antes de inscribir.",
        "sucesion": "🟡 Todos los herederos deben figurar y firmar: exige sucesión inscrita.",
        "deuda_arbitrios": "🟡 Exige la constancia de no adeudo; negocia descontar la deuda del precio.",
        "cambio_uso": "🟡 El uso inscrito no coincide con el previsto: requiere cambio de uso municipal.",
        "uso_comercial": "🟡 Uso comercial: verifica la zonificación y que el inquilino pueda sacar licencia de funcionamiento.",
    }
    if datos["antiguedad"] >= 30 and datos["independizacion"] == "No sé":
        st.warning("🟡 Edificación de 30+ años con independización desconocida: pide la copia literal antes de ofertar.")
    for c in condiciones:
        if alertas[c].startswith("🔴"):
            st.error(alertas[c])
        else:
            st.warning(alertas[c])
    if not condiciones:
        st.success("🟢 Sin condiciones de saneamiento detectadas: solo aplica la ruta estándar de compraventa.")

    tabla = ruta.assign(**{"En ruta crítica": ruta["critico"].map({True: "✅", False: "—"})})[
        ["fase_nombre", "tramite", "entidad", "inicio", "fin", "duracion", "En ruta crítica", "para_que"]
    ].rename(columns={"fase_nombre": "Fase", "tramite": "Trámite", "entidad": "Entidad", "inicio": "Día inicio",
                      "fin": "Día fin", "duracion": "Días", "para_que": "Para qué"})
    st.dataframe(tabla, hide_index=True)
    st.caption("Supuestos de la ruta crítica: en la due diligence SUNARP y la Municipalidad avanzan en paralelo; "
               "saneamiento, cierre e inscripción son secuenciales. La licencia de funcionamiento es post-compra. "
               f"⚖️ {base['_aviso']}")


def sidebar_dataset(meta: dict) -> None:
    """Indicador de última actualización + botón de refresco en segundo plano."""
    sb = st.sidebar
    sb.markdown("**🗓️ Última actualización del mercado**")
    real = meta.get("ultima_actualizacion_real")
    if real:
        sb.markdown(f"### {_fmt_fecha(real)}")
    else:
        sb.markdown("### Solo base sintética")
    sb.caption(f"Última sincronización del CSV: {_fmt_fecha(meta.get('ultima_sincronizacion'))} · "
               f"{meta.get('segmentos_reales_en_esta_corrida', 0)}/6 segmentos reales en esa corrida")

    if actualizacion_en_curso():
        sb.info("⏳ Actualización en curso (≈1–2 min). La app sigue usando el CSV actual.")
        if sb.button("Comprobar estado"):
            st.rerun()
    elif PERMITIR_ACTUALIZAR:
        if sb.button("🔄 Actualizar dataset con datos reales", help="Ejecuta scripts/actualizar_dataset_real.py "
                     "en segundo plano. Si el portal bloquea un segmento, se conservan sus datos previos."):
            lanzar_actualizacion()
            st.toast("Actualización iniciada en segundo plano")
            st.rerun()
    if LOG_ACTUALIZACION.exists():
        with sb.expander("Log de la última actualización"):
            st.code(LOG_ACTUALIZACION.read_text(encoding="utf-8", errors="replace")[-3000:] or "(vacío)")
    sb.divider()


def main() -> None:
    st.set_page_config(page_title="EVI.IA", page_icon="🏢", layout="wide")

    st.sidebar.title("🏢 EVI.IA")
    st.sidebar.caption("Evaluador de Viabilidad de Inversión Inmobiliaria")
    meta = cargar_metadata()
    sidebar_dataset(meta)

    mercado = cargar_mercado()  # lectura local cacheada: sin red, sin riesgo de 403
    ref = referencias_distrito(mercado)

    p = sidebar(ref)

    st.title("EVI.IA — Evaluador de Viabilidad de Inversión Inmobiliaria")
    st.caption(f"{p['distrito']} · {p['area_m2']:.0f} m² · {fmt_usd(p['precio_usd'])} · "
               f"alquiler {fmt_usd(p['alquiler_mensual_usd'])}/mes")

    t1, t2, t3 = st.tabs(["📊 Mercado", "💰 Rentabilidad", "⚖️ Legal"])
    with t1:
        tab_mercado(mercado, meta, ref, p)
    with t2:
        tab_rentabilidad(p, ref)
    with t3:
        tab_legal(p)


if __name__ == "__main__":
    main()
