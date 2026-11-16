# Síntesis de hallazgos — Validación con usuarios de EVI.IA

**Fecha:** Noviembre 2026 · **Método:** 3 entrevistas cualitativas semiestructuradas (5 preguntas cada una) a agentes inmobiliarios independientes, una por distrito objetivo.

| # | Entrevistada | Perfil | Distrito | Transcripción |
|---|---|---|---|---|
| 1 | Agente A | Agente independiente | Lince | [entrevista_01_agente_a_lince.md](entrevista_01_agente_a_lince.md) |
| 2 | Agente B | Agente independiente, ex banca | Jesús María | [entrevista_02_agente_b_jesus_maria.md](entrevista_02_agente_b_jesus_maria.md) |
| 3 | Agente C | Agente segmento medio-alto y corporativo | San Isidro | [entrevista_03_agente_c_san_isidro.md](entrevista_03_agente_c_san_isidro.md) |

---

## Resumen ejecutivo

Las tres agentes **ya calculan la rentabilidad por su cuenta**, desde una calculadora de celular hasta un Excel con dos escenarios. Ninguna pagaría solo por eso. El dolor que las tres repiten y que les cuesta comisiones es otro: **operaciones que se caen o se traban por problemas legales y municipales que se descubren tarde**, cuando el tasador del banco observa el inmueble, cuando la partida no está independizada o cuando aparece una multa o una sucesión sin inscribir. A esto se suma un problema de datos: **los precios publicados en portales no reflejan el precio de cierre**, y las agentes lo corrigen con llamadas y grupos de WhatsApp.

**Conclusión para el producto:** EVI.IA no compite con el Excel del agente. Su propuesta de valor es **prevenir que una operación se caiga antes de firmar arras**, y un motor financiero que no omita la fricción fiscal y operativa le da credibilidad frente al cliente.

---

## Tabla comparativa de dolor y validación

| Dimensión | Agente A · Lince | Agente B · Jesús María | Agente C · San Isidro |
|---|---|---|---|
| **Tiempo invertido hoy en comparativos** | 30–40 min (1 h+ si arma un PDF para un propietario) | 30–45 min | 1–2 h (le quita una mañana de visitas) |
| **Herramienta actual** | Calculadora del celular / Excel simple | Plantilla de Excel propia | Excel con dos escenarios (tradicional vs. amoblado corporativo) |
| **Fuente de precios** | Urbania, Adondevivir + 2 grupos de WhatsApp | Urbania, Nexo Inmobiliario, cierres propios, BCRP | Cierres con colegas, Urbania, ASEI / Urbania Index, llamadas |
| **Cuello de botella financiero** | Precios publicados inflados (pide US$ 120k, cierra US$ 105k); rentabilidad bruta 6–6.5 % sin detalle | Clientes olvidan alcabala, notaría, amoblado, IR 5 % y vacancia | Retorno bruto ≤ 5 %; mantenimiento de S/ 600–900/mes; m² cambia por cuadra |
| **Cuello de botella legal / municipal** | Pisos ampliados sin declaratoria de fábrica; deuda de arbitrios que bloquea el desembolso; **9 meses** para independizar | Sucesiones intestadas sin inscribir (edificios de los 90); herederos sin poderes vigentes; **~1 año** para independizar | Modificaciones sin permiso con multa abierta; semanas por la constancia de no adeudo; hipotecas antiguas levantadas a medias |
| **Disposición a pagar — por reporte** | **S/ 15–20** (preferida) | **S/ 20** por consulta | — |
| **Disposición a pagar — mensual** | Máx. S/ 40–50 (con reservas) | **S/ 60–80** | **S/ 100–150** |
| **Condición para pagar** | Precios reales de cierre; evitar sacar copias literales | Información actualizada y confiable ("si me falla una vez, dejo de usarla") | Datos reales de SUNARP, cargas municipales y cierres por calle |

**Rango total de disposición a pagar:** S/ 15 por reporte puntual hasta S/ 150 al mes por suscripción.

---

## Hallazgos clave que moldearon el MVP de EVI.IA

### 1. Sesgo de precios publicados vs. cierre real
- En **Lince** los precios de los portales están inflados: un ejemplo concreto de US$ 120,000 pedidos contra US$ 105,000 de cierre, un descuento de ~12.5 %.
- En **Jesús María**, la agente cruza la oferta publicada con sus propios cierres y con los reportes trimestrales del BCRP "para que el cliente vea que no me estoy inventando las cifras".
- En **San Isidro** el m² cambia radicalmente entre cuadras, por ejemplo frente a El Olivar contra cerca de Paseo de la República.

**Decisión de producto:** el Módulo de Mercado muestra la **distribución completa** de precio por m² (box plot con cada aviso), no un solo promedio, y ubica el precio del inmueble evaluado frente a la mediana del distrito. Avisa cuando el precio está más de 10 % por encima para apoyar la negociación.

### 2. Fricción fiscal y operativa omitida
Las tres agentes mencionan rubros que el cliente olvida y que convierten un "buen negocio" bruto en uno mediocre neto:
- **Alcabala:** 3 % sobre el valor que excede 10 UIT en segunda venta (Agente B).
- **Impuesto a la Renta:** 5 % sobre el alquiler (Agente A, Agente B).
- **Vacancia:** "un mes vacío al año" (Agente A, Agente B).
- **Mantenimiento alto:** S/ 600–900/mes en San Isidro (Agente C).
- Gastos notariales y amoblado (Agente B).

**Decisión de producto:** el Módulo de Rentabilidad calcula la **inversión total** con alcabala, gastos de cierre y remodelación, un **NOI** con vacancia, mantenimiento y arbitrios, y un flujo a 5 años **después del IR del 5 %**. Muestra todo en USD y en soles, con Cap Rate, TIR, VAN y payback. Agente C exige rigor numérico y Agente B habla en términos de "en cuántos años recuperan su plata", y el payback responde a esa pregunta.

### 3. El verdadero *deal-breaker* son los trámites legales y municipales
Es el dolor más intenso y el único por el que las tres pagarían. Casos reportados:

| Riesgo | Distrito | Consecuencia reportada |
|---|---|---|
| Demora de **9 a 12 meses** en la independización en SUNARP de proyectos nuevos | Lince, Jesús María | Sin partida independiente el banco no financia: el comprador que quería revender "se queda amarrado" |
| Ampliaciones del último piso (drywall, calamina) **sin declaratoria de fábrica** | Lince antiguo (Petit Thouars) | El tasador observa el inmueble y se cae el crédito hipotecario |
| Deuda de arbitrios que el vendedor quiere pagar con la venta | Lince | El banco no desembolsa sin la constancia de no adeudo |
| **Sucesiones intestadas sin inscribir**; herederos en el extranjero sin poderes vigentes | Jesús María (edificios de los 90) | Meses de trabajo perdidos |
| Modificaciones sin permiso con **multa municipal** abierta | San Isidro | El comprador desiste en el estudio de títulos |
| **Hipotecas antiguas levantadas a medias** en SUNARP | San Isidro | Un mes de retraso mientras el banco emite la minuta |

**Decisión de producto:** esto valida directamente el submódulo **Pre-Arras** de la pestaña Legal:
- **Semáforo Pre-Arras:** 🟢 viable / 🟡 firmar solo con cláusula suspensiva / 🔴 no firmar hasta sanear. Separa *proyecto en planos / entrega reciente* (alerta de retraso en independización) de *segundo uso* (fábrica, aires/último piso, deudas, sucesión).
- **Checklist documental dinámico:** copia literal, CRI, HR/PU, no adeudo, vigencia de poder, sucesión, levantamiento de hipoteca y más, según los riesgos activados.
- **Cláusula suspensiva autocompletada:** devolución del 100 % de las arras si no se levanta la carga, no se inscribe la fábrica o la independización, o si la tasación bancaria sale observada.
- El **Score de Riesgo Burocrático** y la **ruta crítica** (Notaría, SUNARP y Municipalidad) traducen el riesgo en días, que es como lo viven las agentes: "pierdes meses de trabajo".
- Los rangos de retraso en independización que usa la app (6–18 meses en Lince y Jesús María) contienen los 9–12 meses reportados.

### 4. Modelo de monetización validado
- **Segmento medio (Lince):** el agente independiente es sensible a los costos fijos ("ya pago bastante entre avisos, gasolina y celular"). Prefiere **pago por reporte de S/ 15–20**, justo antes de firmar arras con un cliente concreto.
- **Segmentos de mayor ticket (Jesús María, San Isidro):** aceptan **suscripción de S/ 60–80** (Jesús María) a **S/ 100–150 al mes** (San Isidro). El argumento de la Agente C: una comisión de US$ 6,000–10,000 justifica la herramienta si evita "perder dos meses en una operación trabada".

**Implicancia: Modelo Híbrido Validado en Campo** (adoptado en la [sección 7 del README](../../README.md#7-modelo-de-negocio)):

| Plan | Precio | Para quién | Validado por |
|---|---|---|---|
| **Pay-per-Use: Reporte Pre-Arras** | **S/ 15 – S/ 20 por consulta** | Agentes independientes de alta rotación en segmentos medios, sin costos fijos mensuales | Agente A (Lince), Agente B (Jesús María) |
| **Suscripción Mensual Pro** | **S/ 60 – S/ 150 / mes** | Brókeres especializados y agencias con evaluaciones recurrentes en distritos de ticket alto | Agente B (Jesús María), Agente C (San Isidro) |

La propuesta inicial era una suscripción rígida (S/ 29 y S/ 119 al mes). Pasamos a este esquema híbrido porque el agente de segmento medio rechaza los pagos fijos pero sí paga por reporte en el momento crítico.

---

## Brechas que las entrevistas revelan (próximos pasos)

Las entrevistadas pusieron condiciones explícitas que el MVP **todavía no cumple**. Se registran para no sobreprometer:

1. **Precios de cierre, no de oferta.** EVI.IA usa avisos publicados, que son precisamente los "precios inflados" que Agente C rechaza. Próximo paso: un factor de ajuste oferta→cierre por distrito, calibrado con cierres que aporten agentes aliados.
2. **Granularidad por calle o cuadra (San Isidro).** Hoy la referencia es la mediana distrital. Próximo paso: segmentar por zona o micro-ubicación.
3. **Datos reales de SUNARP y cargas municipales.** Hoy el diagnóstico depende de lo que el usuario ingresa en el formulario; no consulta partidas. Próximo paso: guiar la lectura de la copia literal y explorar la integración con los servicios de publicidad registral.
4. **Historial de constructoras (Agente B).** Hoy no existe un registro por constructora de sus tiempos reales de independización. Es un diferencial claro si se construye con datos de agentes.
5. **Confiabilidad.** Las tres condicionan el pago a la exactitud ("si me falla una vez, dejo de usarla"). Los rangos legales del MVP son referenciales y deben validarse con notarios y abogados antes de comercializar.

---

## Limitaciones del estudio
- Muestra pequeña (n = 3), todas agentes independientes. No se entrevistó a inversionistas compradores directos ni a constructoras.
- Una entrevistada por distrito: los hallazgos por distrito son indicativos, no representativos.
- La disposición a pagar es **declarada**, no observada. El siguiente paso es validarla con una preventa o un piloto de reportes pagados.
