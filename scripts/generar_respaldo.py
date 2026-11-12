"""Genera la base SINTÉTICA inicial de data/inmuebles_respaldo.csv (formato largo).

Es el último respaldo: el demo funciona sin internet aunque nunca se haya corrido
scripts/actualizar_dataset_real.py. Los rangos son supuestos referenciales, no avisos reales.

    python scripts/generar_respaldo.py
"""
import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd
from actualizar_dataset_real import COLUMNAS, CSV_DESTINO, CSV_RESUMEN, METADATA, limpiar, resumir, tipologia

random.seed(2026)

# distrito: (precio venta USD/m² medio, alquiler USD/m²/mes medio)
SUPUESTOS = {"Lince": (1_750, 9.0), "Jesús María": (1_950, 9.5), "San Isidro": (2_750, 11.5)}
N = {"venta": 30, "alquiler": 20}
ahora = datetime.now().isoformat(timespec="seconds")

filas = []
for distrito, (p_venta, p_alq) in SUPUESTOS.items():
    for operacion, media in (("venta", p_venta), ("alquiler", p_alq)):
        for i in range(N[operacion]):
            area = round(random.uniform(40, 160))
            dorm = max(1, min(4, round(area / 35)))
            precio = area * random.gauss(media, media * (0.10 if operacion == "venta" else 0.12))
            filas.append({
                "id": f"SIN-{operacion[:3].upper()}-{distrito[:3].upper()}-{i + 1:03d}",
                "operacion": operacion, "distrito": distrito, "tipo": "Departamento",
                "tipologia": tipologia(dorm), "dormitorios": dorm, "area_m2": area,
                "precio_usd": round(precio, -2 if operacion == "venta" else -1),
                "fuente": "respaldo_sintetico", "fecha_extraccion": ahora,
            })

df = limpiar(pd.DataFrame(filas)).reindex(columns=COLUMNAS)
df.to_csv(CSV_DESTINO, index=False, encoding="utf-8")
resumir(df).to_csv(CSV_RESUMEN, index=False, encoding="utf-8")
METADATA.write_text(json.dumps({
    "ultima_sincronizacion": ahora, "ultima_actualizacion_real": None, "motor": "sintetico",
    "segmentos_reales_en_esta_corrida": 0,
    "segmentos": {f"{d}|{o}": {"fuente": "sintetico", "filas": int(((df.distrito == d) & (df.operacion == o)).sum()),
                               "estado": "base sintética inicial", "actualizado": ahora}
                  for o in N for d in SUPUESTOS},
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{len(df)} filas sintéticas -> {CSV_DESTINO}")
