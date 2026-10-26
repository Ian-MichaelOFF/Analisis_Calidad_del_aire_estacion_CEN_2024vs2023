# -*- coding: utf-8 -*-
"""
limpieza_años.py
================
Procesa las bases horarias del SIMAJ (estación CEN) de 2023 y 2024 y genera
Datos/datos_dashboard.js conforme a la NOM-172-SEMARNAT-2023
(DOF 25/01/2024), Índice AIRE Y SALUD.

Qué aplica de la norma
----------------------
* Tabla 3  - Concentración base:
    PM10 / PM2.5 -> horario: promedio móvil ponderado de 12 h (con FA)
                    diario : promedio de 24 h
    CO           -> horario: promedio móvil de 8 h
                    diario : máximo de los móviles de 8 h del día
    O3/NO2/SO2   -> horario: promedio horario
                    diario : máximo horario del día
* 5.2.5   - Completitud:
    móvil 12 h : al menos 2 de las 3 horas más recientes (el índice i se conserva)
    móvil 8 h  : al menos 6 de 8 horas
    24 h       : al menos 18 horas, de 00:00 a 23:00
* 5.2.4 / Tabla 2 - Redondeo "half-up": PM a 0 decimales, O3/NO2/SO2 a 3, CO a 2.
* 5.3 / Tablas 4-9 - Bandas. Los umbrales de PM10 y PM2.5 dependen del año
  (ver LIMITES_PM): 2023 -> columna "Al entrar en vigor de la NOM",
  2024 -> columna "A partir de enero de 2024".

Los promedios móviles se calculan sobre una línea de tiempo horaria CONTINUA
que une 2023 y 2024, de modo que las primeras horas de 2024 usan las últimas
de 2023, y las horas ausentes cuentan como "sin dato" sin recorrer el índice i.

Estructura de salida (compatible con la anterior):
    window.DATA_DASHBOARD = {"2023": {"meses": [...], "dias_criticos": [...],
                                      "dias": [...], "meta": {...}}, "2024": {...}}
Las claves originales se conservan; las nuevas son adicionales.
"""
import json
import math
import os
import re
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- CONFIGURACIÓN
ESTACION = "CEN"
ARCHIVOS = {2023: "BD_2023 (1).xlsx", 2024: "BD_2024 (2).xlsx"}

CONTAMINANTES = ["O3", "NO2", "SO2", "CO", "PM10", "PM2.5"]
COLS_NUM = [
    "O3", "NO", "NO2", "NOX", "SO2", "CO", "PM10", "PM2.5",
    "IT", "ET", "RH", "WS", "WD", "PP", "ATM", "RS", "UVI",
]
# Columnas donde un valor negativo no es físicamente posible -> "sin dato"
COLS_NO_NEGATIVAS = CONTAMINANTES + ["NO", "NOX", "RH", "WS"]
# Cualquier columna con valores <= este límite se considera código de "sin dato" (-99, -999...)
CENTINELA = -90

FA = {"PM10": 0.714, "PM2.5": 0.694}  # factor de ajuste (5.2.5.3)
DECIMALES = {"PM10": 0, "PM2.5": 0, "O3": 3, "NO2": 3, "SO2": 3, "CO": 2}  # Tabla 2

CATEGORIAS = ["Buena", "Aceptable", "Mala", "Muy mala", "Extremadamente mala"]
UMBRAL_DIA_CRITICO = 2  # 2 = "Mala" o peor

# Valor diario de O3/NO2/SO2 (máx. horario) y CO (máx. del móvil de 8 h):
# la NOM no fija un mínimo de datos para el máximo diario, así que por defecto basta
# con 1 valor válido. El análisis previo de marzo exigía 18 de 24; si quieres replicar
# ese criterio más estricto, cámbialo a 18.
MIN_VALIDOS_MAX_DIARIO = 1

# Límites superiores de cada banda (Buena, Aceptable, Mala, Muy mala); arriba = Extremadamente mala
LIMITES_FIJOS = {
    "O3": (0.058, 0.090, 0.135, 0.175),    # Tabla 6, 1 h (ppm)
    "NO2": (0.053, 0.106, 0.160, 0.213),   # Tabla 7, 1 h (ppm)
    "SO2": (0.035, 0.075, 0.185, 0.304),   # Tabla 8, 1 h (ppm)
    "CO": (5.00, 9.00, 12.00, 16.00),      # Tabla 9, móvil 8 h (ppm)
}
# Tablas 4 y 5 (µg/m3, móvil ponderado 12 h): cambian según la columna de la NOM
LIMITES_PM = {
    "entrada_en_vigor": {"PM10": (45, 70, 132, 213), "PM2.5": (15, 41, 79, 130)},
    "enero_2024": {"PM10": (45, 60, 132, 213), "PM2.5": (15, 33, 79, 130)},
    "enero_2026": {"PM10": (45, 50, 132, 213), "PM2.5": (15, 25, 79, 130)},
}


def columna_nom(ano):
    """Columna de las Tablas 4 y 5 que corresponde a cada año."""
    if ano <= 2023:
        return "entrada_en_vigor"
    if ano <= 2025:
        return "enero_2024"
    return "enero_2026"


def limites_ano(ano):
    lim = dict(LIMITES_FIJOS)
    lim.update(LIMITES_PM[columna_nom(ano)])
    return lim


# ------------------------------------------------------------------ UTILIDADES
def redondear(x, decimales):
    """Redondeo de la NOM (5.2.4): si la cifra siguiente es >= 5, se incrementa."""
    if x is None:
        return float("nan")
    x = float(x)
    if math.isnan(x):
        return x
    cuanto = Decimal(1).scaleb(-decimales)
    return float(Decimal(repr(round(x, 9))).quantize(cuanto, rounding=ROUND_HALF_UP))


def redondear_arr(arr, decimales):
    return np.array([redondear(v, decimales) for v in arr], dtype=float)


def num(x, nd=None):
    """NaN -> None; float nativo para JSON."""
    if x is None:
        return None
    x = float(x)
    if math.isnan(x):
        return None
    return round(x, nd) if nd is not None else x


# --------------------------------------------------------- CÁLCULOS DE LA NOM
def promedio_movil_ponderado_12h(serie, fa):
    """5.2.5.3. `serie` es una línea de tiempo horaria continua (NaN = sin dato).

    C = [ sum(Ci * W^(i-1)) / sum(W^(i-1)) ] * FA, con i=1 la hora más reciente.
    W = 0.5 si w <= 0.5; si no, w redondeado a 2 decimales; w = 1 - (Cmax-Cmin)/Cmax.
    Válido solo si hay dato en al menos 2 de las 3 horas más recientes.
    """
    n = len(serie)
    out = np.full(n, np.nan)
    for t in range(n):
        ventana = serie[max(0, t - 11): t + 1][::-1]  # ventana[k] <-> i = k + 1
        validos = ~np.isnan(ventana)
        if validos[:3].sum() < 2:
            continue
        vals = ventana[validos]
        cmax, cmin = vals.max(), vals.min()
        if cmax <= 0:
            out[t] = 0.0
            continue
        w = 1 - (cmax - cmin) / cmax
        W = 0.5 if w <= 0.5 else redondear(w, 2)
        pesos = W ** np.arange(len(ventana))  # W^(i-1)
        out[t] = (ventana[validos] * pesos[validos]).sum() / pesos[validos].sum() * fa
    return out


def promedio_movil_8h(serie):
    """5.2.5.2. Promedio de la hora y las 7 previas; requiere al menos 6 de 8 horas."""
    n = len(serie)
    out = np.full(n, np.nan)
    for t in range(n):
        ventana = serie[max(0, t - 7): t + 1]
        validos = ~np.isnan(ventana)
        if validos.sum() >= 6:
            out[t] = ventana[validos].mean()
    return out


def promedio_24h(matriz_dias):
    """5.2.5.4. matriz (n_dias, 24) de 00:00 a 23:00; requiere al menos 18 horas."""
    validos = ~np.isnan(matriz_dias)
    suma = np.where(validos, matriz_dias, 0).sum(axis=1)
    cuenta = validos.sum(axis=1)
    out = np.full(len(matriz_dias), np.nan)
    ok = cuenta >= 18
    out[ok] = suma[ok] / cuenta[ok]
    return out


def categoria_arr(valores, limites):
    """Índice de banda 0-4 (Buena ... Extremadamente mala); -1 si no hay dato.
    Límite superior inclusivo (p. ej. 'Aceptable' = >45 a 60)."""
    valores = np.asarray(valores, dtype=float)
    cat = np.searchsorted(np.asarray(limites, dtype=float), valores, side="left")
    cat = np.where(np.isnan(valores), -1, cat)
    return cat.astype(int)


def autoprueba_anexo_a():
    """Reproduce los ejemplos del Anexo A de la NOM. Si falla, no se procesa nada."""
    a1 = np.array([50, 80, 75, 90, 82, 53, 64, 74, 21, 10, 16, 13], float)
    a2 = np.array([118, 97, 130, 142, 146, 144, 141, 134, 147, 150, 141, 103], float)
    a3 = a2.copy()
    a3[-2] = np.nan  # falta la 2a hora más reciente (11:00)
    r1 = redondear(promedio_movil_ponderado_12h(a1, FA["PM2.5"])[-1], 0)
    r2 = redondear(promedio_movil_ponderado_12h(a2, FA["PM10"])[-1], 0)
    r3 = redondear(promedio_movil_ponderado_12h(a3, FA["PM10"])[-1], 0)
    assert (r1, r2, r3) == (12, 92, 89), f"Anexo A no reproducido: {(r1, r2, r3)}"
    return r1, r2, r3


# ------------------------------------------------------------------- CARGA
def parsear_fechas(serie):
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie.dt.normalize()
    s = serie.astype(str).str.strip()
    dmy = s.str.extract(r"^(\d{1,2}/\d{1,2}/\d{4})")[0]
    if dmy.notna().all():  # formato mexicano dd/mm/aaaa
        return pd.to_datetime(dmy, format="%d/%m/%Y").dt.normalize()
    return pd.to_datetime(s, errors="coerce").dt.normalize()


def hora_numerica(serie):
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce")
    return pd.to_numeric(serie.astype(str).str.extract(r"^\s*(\d{1,2})")[0], errors="coerce")


def cargar_estacion(ruta, ano_archivo):
    df = pd.read_excel(ruta)
    df.columns = [str(c).strip() for c in df.columns]
    df = df[df["STATION"].astype(str).str.strip().str.upper() == ESTACION].copy()
    df["FECHA"] = parsear_fechas(df["DATE"])
    df["H"] = hora_numerica(df["HOUR"])
    df = df.dropna(subset=["FECHA", "H"])

    presentes = [c for c in COLS_NUM if c in df.columns]
    faltan = [c for c in COLS_NUM if c not in df.columns]
    if faltan:
        print(f"   Aviso {ano_archivo}: columnas ausentes (se tratan como sin dato): {faltan}")
    for c in COLS_NUM:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
            df.loc[df[c] <= CENTINELA, c] = np.nan
            if c in COLS_NO_NEGATIVAS:
                df.loc[df[c] < 0, c] = np.nan
        else:
            df[c] = np.nan

    anos = df["FECHA"].dt.year.value_counts().to_dict()
    if set(anos) - {ano_archivo}:
        print(f"   Aviso {ano_archivo}: el archivo trae fechas de otros años: {anos}")
    return df


def posicion_horaria(h):
    """Devuelve (posición 0-23, base). Detecta si HOUR va de 0-23 o de 1-24."""
    base = 1 if (h.min() >= 1 and h.max() == 24) else 0
    return (h - base).astype(int), base


# --------------------------------------------------------------- PROCESAMIENTO
def procesar(df):
    """df: estación CEN de todos los años, ya limpio. Devuelve dict por año."""
    df = df.copy()
    df["POS"], base = posicion_horaria(df["H"])
    df = df[(df["POS"] >= 0) & (df["POS"] <= 23)]

    n_dup = int(df.duplicated(subset=["FECHA", "POS"]).sum())
    if n_dup:
        print(f"   Aviso: {n_dup} registros duplicados (fecha-hora); se conserva el último.")
        df = df.drop_duplicates(subset=["FECHA", "POS"], keep="last")

    t0, t1 = df["FECHA"].min(), df["FECHA"].max()
    n_dias = (t1 - t0).days + 1
    n = n_dias * 24
    idx = ((df["FECHA"] - t0).dt.days.values * 24 + df["POS"].values).astype(int)

    S = {}
    for c in COLS_NUM:
        a = np.full(n, np.nan)
        a[idx] = df[c].values
        S[c] = a

    # Concentraciones base HORARIAS (Tabla 3) ya redondeadas (Tabla 2)
    BH = {
        "O3": redondear_arr(S["O3"], 3),
        "NO2": redondear_arr(S["NO2"], 3),
        "SO2": redondear_arr(S["SO2"], 3),
        "CO": redondear_arr(promedio_movil_8h(S["CO"]), 2),
        "PM10": redondear_arr(promedio_movil_ponderado_12h(S["PM10"], FA["PM10"]), 0),
        "PM2.5": redondear_arr(promedio_movil_ponderado_12h(S["PM2.5"], FA["PM2.5"]), 0),
    }
    # PM diario sin redondear (para promedios mensuales) y redondeado (para la banda)
    PM24 = {k: promedio_24h(S[k].reshape(n_dias, 24)) for k in ("PM10", "PM2.5")}

    fechas = pd.date_range(t0, periods=n_dias, freq="D")
    anos_dia = fechas.year.values
    meses_dia = fechas.month.values
    resultado = {}

    for ano in sorted(ARCHIVOS):
        dias_y = np.where(anos_dia == ano)[0]
        if len(dias_y) == 0:
            continue
        lim = limites_ano(ano)
        sl = np.concatenate([np.arange(d * 24, d * 24 + 24) for d in dias_y])

        # Categorías horarias por contaminante (con los umbrales del año)
        CH = {k: categoria_arr(BH[k], lim[k]).reshape(n_dias, 24) for k in CONTAMINANTES}
        # Valores y categorías DIARIAS (Tabla 3, reporte diario)
        def maximo_diario(a):
            filas = a.reshape(n_dias, 24)
            return np.array([np.nanmax(r) if (~np.isnan(r)).sum() >= MIN_VALIDOS_MAX_DIARIO
                             else np.nan for r in filas])

        VD = {
            "PM10": redondear_arr(PM24["PM10"], 0),
            "PM2.5": redondear_arr(PM24["PM2.5"], 0),
            "O3": maximo_diario(BH["O3"]),
            "NO2": maximo_diario(BH["NO2"]),
            "SO2": maximo_diario(BH["SO2"]),
            "CO": maximo_diario(BH["CO"]),
        }
        CD = {k: categoria_arr(VD[k], lim[k]) for k in CONTAMINANTES}

        meses, dias, criticos = [], [], []
        meses_con_datos = sorted(set(df.loc[df["FECHA"].dt.year == ano, "FECHA"].dt.month))

        for mes in meses_con_datos:
            dm = dias_y[meses_dia[dias_y] == mes]
            hm = np.concatenate([np.arange(d * 24, d * 24 + 24) for d in dm])

            def mx(a):
                v = a[hm]
                return num(np.nanmax(v)) if (~np.isnan(v)).any() else None

            def media_dias(a):
                v = a[dm]
                return num(np.nanmean(v), 2) if (~np.isnan(v)).any() else None

            def media_horas(a):
                v = a[hm]
                return num(np.nanmean(v), 2) if (~np.isnan(v)).any() else None

            n_crit = 0
            for d in dm:
                cat_h = max([CH[k][d].max() for k in CONTAMINANTES])
                cat_d = max([CD[k][d] for k in CONTAMINANTES])
                if max(cat_h, cat_d) >= UMBRAL_DIA_CRITICO:
                    n_crit += 1

            meses.append({
                "mes": int(mes),
                "O3_max": mx(BH["O3"]),
                "CO_max": mx(BH["CO"]),                 # máx. del promedio móvil de 8 h
                "PM10_mean": media_dias(PM24["PM10"]),  # media de promedios de 24 h válidos
                "PM25_mean": media_dias(PM24["PM2.5"]),
                "Temp_mean": media_horas(S["ET"]),
                "RH_mean": media_horas(S["RH"]),
                "WS_mean": media_horas(S["WS"]),
                # adicionales
                "PM10_max": mx(BH["PM10"]),             # máx. del móvil ponderado 12 h
                "PM25_max": mx(BH["PM2.5"]),
                "NO2_max": mx(BH["NO2"]),
                "SO2_max": mx(BH["SO2"]),
                "dias_criticos": int(n_crit),
            })

        for d in dias_y:
            if int(fechas[d].month) not in meses_con_datos:
                continue
            fecha = fechas[d].strftime("%Y-%m-%d")
            cat_h_k = {k: int(CH[k][d].max()) for k in CONTAMINANTES}
            cat_h = max(cat_h_k.values())
            cat_d_k = {k: int(CD[k][d]) for k in CONTAMINANTES}
            cat_d = max(cat_d_k.values())
            if all(np.isnan(S[k][d * 24:(d + 1) * 24]).all() for k in CONTAMINANTES):
                continue  # día sin ningún dato de contaminantes
            resp_d = [k for k, v in cat_d_k.items() if v == cat_d and cat_d >= 0]
            dias.append({
                "fecha": fecha,
                "PM10_24h": num(VD["PM10"][d]),
                "PM25_24h": num(VD["PM2.5"][d]),
                "O3_max": num(VD["O3"][d]),
                "NO2_max": num(VD["NO2"][d]),
                "SO2_max": num(VD["SO2"][d]),
                "CO_max8h": num(VD["CO"][d]),
                "cat_diaria": cat_d if cat_d >= 0 else None,
                "resp_diaria": resp_d,
                "cat_horaria_max": cat_h if cat_h >= 0 else None,
            })

            if max(cat_h, cat_d) >= UMBRAL_DIA_CRITICO:
                horas = []
                for h in range(24):
                    i = d * 24 + h
                    cats = {k: int(CH[k][d][h]) for k in CONTAMINANTES}
                    cmax = max(cats.values())
                    horas.append({
                        "HOUR": int(h + base),
                        "O3": num(BH["O3"][i]),
                        "PM10": num(BH["PM10"][i]),    # móvil ponderado 12 h
                        "PM2.5": num(BH["PM2.5"][i]),  # móvil ponderado 12 h
                        "CO": num(BH["CO"][i]),        # móvil 8 h
                        "ET": num(S["ET"][i]),
                        # adicionales
                        "NO2": num(BH["NO2"][i]),
                        "SO2": num(BH["SO2"][i]),
                        "PM10_crudo": num(S["PM10"][i]),
                        "PM25_crudo": num(S["PM2.5"][i]),
                        "CO_crudo": num(S["CO"][i]),
                        "CAT": cmax if cmax >= 0 else None,
                        "RESP": [k for k, v in cats.items() if v == cmax and cmax >= 0],
                    })
                dia_h = BH["PM10"][d * 24:(d + 1) * 24]
                criticos.append({
                    "fecha": fecha,
                    "O3_max": num(VD["O3"][d]),
                    "PM10_max": num(np.nanmax(dia_h)) if (~np.isnan(dia_h)).any() else None,
                    "cat_diaria": cat_d if cat_d >= 0 else None,
                    "cat_horaria_max": cat_h if cat_h >= 0 else None,
                    "horas": horas,
                })

        esperadas = len(dias_y) * 24
        disp = {}
        for k in CONTAMINANTES + ["ET", "RH", "WS", "WD"]:
            validos = int((~np.isnan(S[k][sl])).sum())
            disp[k] = {"pct": round(100 * validos / esperadas, 1), "faltantes": esperadas - validos}

        resultado[str(ano)] = {
            "meses": meses,
            "dias_criticos": criticos,
            "dias": dias,
            "meta": {
                "norma": "NOM-172-SEMARNAT-2023 (DOF 25/01/2024)",
                "estacion": ESTACION,
                "columna_umbrales_PM": columna_nom(ano),
                "limites": {k: list(v) for k, v in lim.items()},
                "categorias": CATEGORIAS,
                "umbral_dia_critico": UMBRAL_DIA_CRITICO,
                "convencion_HOUR": "1-24" if base == 1 else "0-23",
                "disponibilidad": disp,
            },
        }
    return resultado, n_dup


# ------------------------------------------------------------------ PRINCIPAL
def main():
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("Autoprueba con los ejemplos del Anexo A de la NOM ...", end=" ")
    r = autoprueba_anexo_a()
    print(f"OK (PM2.5={r[0]}, PM10={r[1]}, PM10 con faltante={r[2]} ug/m3)")

    frames = []
    for ano, nombre in ARCHIVOS.items():
        ruta = os.path.join(BASE_DIR, nombre)
        if not os.path.exists(ruta):
            ruta = os.path.join(BASE_DIR, "Datos", nombre)
        print(f"Cargando {ano} desde: {ruta}")
        frames.append(cargar_estacion(ruta, ano))
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["FECHA", "H"])

    datos, _ = procesar(df)

    dir_datos = os.path.join(BASE_DIR, "Datos")
    os.makedirs(dir_datos, exist_ok=True)
    salida = os.path.join(dir_datos, "datos_dashboard.js")
    with open(salida, "w", encoding="utf-8") as f:
        f.write("window.DATA_DASHBOARD = " + json.dumps(datos, ensure_ascii=False) + ";")

    for ano, d in datos.items():
        crit = len(d["dias_criticos"])
        disp = d["meta"]["disponibilidad"]
        resumen = ", ".join(f"{k} {disp[k]['pct']}%" for k in ("O3", "CO", "PM10", "PM2.5"))
        print(f"  {ano}: {len(d['meses'])} meses | {crit} días críticos "
              f"(umbrales PM: {d['meta']['columna_umbrales_PM']}) | disponibilidad: {resumen}")
    print(f"✅ Archivo guardado con éxito en: {salida}")


if __name__ == "__main__":
    main()
