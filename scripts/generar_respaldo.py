"""Genera data/inmuebles_respaldo.csv con datos SINTÉTICOS y reproducibles.

Los rangos de precio/m² y alquiler/m² son supuestos referenciales del equipo
para que el demo funcione sin internet. No son avisos reales.

    python scripts/generar_respaldo.py
"""
import random
from pathlib import Path

import pandas as pd

random.seed(2026)

# distrito: (precio USD/m² medio, alquiler USD/m²/mes medio)
SUPUESTOS = {
    "Lince": (1_750, 9.0),
    "Jesús María": (1_950, 9.5),
    "San Isidro": (2_750, 11.5),
}
TIPOS = ["Departamento", "Departamento", "Departamento", "Dúplex", "Oficina"]

filas = []
for distrito, (p_m2, a_m2) in SUPUESTOS.items():
    for i in range(30):
        tipo = random.choice(TIPOS)
        area = round(random.uniform(45, 160) if tipo != "Oficina" else random.uniform(35, 120))
        precio_m2 = random.gauss(p_m2, p_m2 * 0.10)
        alquiler_m2 = random.gauss(a_m2, a_m2 * 0.12)
        filas.append(
            {
                "id": f"{distrito[:3].upper()}-{i + 1:03d}",
                "distrito": distrito,
                "tipo": tipo,
                "area_m2": area,
                "dormitorios": 0 if tipo == "Oficina" else max(1, min(4, round(area / 35))),
                "precio_usd": round(area * precio_m2, -2),
                "alquiler_mensual_usd": round(area * alquiler_m2, -1),
                "antiguedad_anios": random.choice([0, 0, 2, 5, 10, 15, 25, 35]),
                "fuente": "respaldo_sintetico",
            }
        )

df = pd.DataFrame(filas)
df["precio_m2_usd"] = (df["precio_usd"] / df["area_m2"]).round(0)
df["alquiler_m2_usd"] = (df["alquiler_mensual_usd"] / df["area_m2"]).round(2)

destino = Path(__file__).resolve().parent.parent / "data" / "inmuebles_respaldo.csv"
df.to_csv(destino, index=False, encoding="utf-8")
print(f"{len(df)} filas -> {destino}")
