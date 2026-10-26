# -*- coding: utf-8 -*-
import sys, io
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Pipeline de procesamiento: CEN_marzo2023
Replica el pipeline del Sprint 1 (Calculos_Diarios_CEN + Indice_AIRE_Y_SALUD_CEN)
conforme a NOM-172-SEMARNAT-2023.

Genera:
  Datos/CEN_marzo2023_procesado.csv        → datos horarios con NowCast y CO_8h
  Datos/numeralia_diaria_2023.csv          → indicadores diarios
  Datos/Reporte_Atipicos_CEN_marzo2023.xlsx
  Datos/dashboard_2023.json                → datos compactos para el dashboard
"""

import pandas as pd
import numpy as np
import json
import os
import warnings
warnings.filterwarnings("ignore")

BASE = r"C:\Users\Hulas\Downloads\Calidad del aire en la estación Centro (CEN), marzo 2024"
DATOS = os.path.join(BASE, "Datos")

# ────────────────────────────────────────────────
# 1. CARGA Y LIMPIEZA
# ────────────────────────────────────────────────
print("1. Cargando CEN_marzo2023.csv …")
df_raw = pd.read_csv(
    os.path.join(DATOS, "CEN_marzo2023.csv"),
    sep=";", encoding="utf-8", low_memory=False
)

# Filtrar filas válidas (CEN, marzo 2023)
df = df_raw[df_raw["STATION"].astype(str).str.strip() == "CEN"].dropna(subset=["DATE"]).copy()
df = df.reset_index(drop=True)
print(f"   Filas válidas: {len(df)}")

# Normalizar nombres de columna
df = df.rename(columns={"dia ": "DIA", "mes ": "MES"})

# Convertir tipos numéricos (decimal coma → punto)
num_cols = ["O3","SO2","CO","PM10","PM2.5","IT","ET","RH","WS","WD","PP","ATM","RS","UVI"]
for col in num_cols:
    if col in df.columns:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", ".").str.strip(),
            errors="coerce"
        )

df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
df["HOUR"] = df["DATE"].dt.hour
df["DIA"]  = df["DATE"].dt.day

# Usar ET (temperatura exterior) como temperatura principal; IT como interior
# Para 2023 tenemos: O3, CO, PM10, PM2.5, IT, ET, RH, WS, WD, PP, ATM
print(f"   Temperatura exterior (ET) disponible: {df['ET'].notna().sum()} / {len(df)}")
print(f"   PM10 disponible: {df['PM10'].notna().sum()} / {len(df)}")
print(f"   PM2.5 disponible: {df['PM2.5'].notna().sum()} / {len(df)}")

# ────────────────────────────────────────────────
# 2. CÁLCULO DE INDICADORES HORARIOS (NOM-172)
# ────────────────────────────────────────────────

# ── Umbrales NOM-172-SEMARNAT-2023 ──
THRESHOLDS = {
    "O3":   [0.058, 0.090, 0.135, 0.175],   # ppm
    "CO":   [5.0,   9.0,  12.0,  16.0],     # ppm (promedio 8h)
    "PM10": [45.0,  60.0, 132.0, 213.0],    # µg/m³ (NowCast)
    "PM25": [15.0,  33.0,  79.0, 130.0],   # µg/m³ (NowCast)
}
CAT_NAMES = ["Buena", "Aceptable", "Mala", "Muy mala", "Extremadamente mala"]

def get_cat(val, thresholds):
    """Retorna categoría (0–4) o -1 si null."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return -1
    for i, t in enumerate(thresholds):
        if val < t if i == 0 else val <= t:
            return i
    return 4

def get_cat_arr(arr, thresholds):
    return np.array([get_cat(v, thresholds) for v in arr])

# ── CO: promedio móvil 8 horas (mín 6 válidos de 8) ──
print("\n2. Calculando indicadores horarios…")

co_raw = df["CO"].values
co_8h = np.full(len(df), np.nan)
for i in range(len(df)):
    window = co_raw[max(0, i-7):i+1]
    valid  = window[~np.isnan(window)]
    if len(valid) >= 6:
        co_8h[i] = np.mean(valid)
df["CO_8h"] = co_8h

# ── PM10 & PM2.5: NowCast 12 horas ──
def nowcast(series, min_valid=2):
    result = np.full(len(series), np.nan)
    for i in range(len(series)):
        window = series[max(0, i-11):i+1]
        valid  = window[~np.isnan(window)]
        if len(valid) < min_valid:
            continue
        c_min, c_max = np.nanmin(window), np.nanmax(window)
        rng  = c_max - c_min if c_max > 0 else 0
        ratio = 1 - (rng / c_max) if c_max > 0 else 1
        w = max(ratio, 0.5)
        weights = np.array([w ** k for k in range(len(window)-1, -1, -1)])
        vals    = window.copy()
        mask    = ~np.isnan(vals)
        if mask.sum() < min_valid:
            continue
        result[i] = np.sum(weights[mask] * vals[mask]) / np.sum(weights[mask])
    return result

df["PM10_nc"]  = nowcast(df["PM10"].values)
df["PM25_nc"]  = nowcast(df["PM2.5"].values)

# ── Categorías horarias ──
df["cat_O3"]   = get_cat_arr(df["O3"].values,    THRESHOLDS["O3"])
df["cat_CO"]   = get_cat_arr(df["CO_8h"].values, THRESHOLDS["CO"])
df["cat_PM10"] = get_cat_arr(df["PM10_nc"].values, THRESHOLDS["PM10"])
df["cat_PM25"] = get_cat_arr(df["PM25_nc"].values, THRESHOLDS["PM25"])

def global_cat(row):
    cats = [row["cat_O3"], row["cat_CO"], row["cat_PM10"], row["cat_PM25"]]
    valid = [c for c in cats if c >= 0]
    return max(valid) if valid else -1

df["cat_global"] = df.apply(global_cat, axis=1)

# ────────────────────────────────────────────────
# 3. INDICADORES DIARIOS
# ────────────────────────────────────────────────
print("3. Calculando indicadores diarios…")

days = sorted(df["DIA"].unique())
daily = []

for dia in days:
    ddf = df[df["DIA"] == dia]

    # PM10 / PM2.5: promedio 24h (mín 18 válidos)
    pm10_vals = ddf["PM10"].dropna().values
    pm25_vals = ddf["PM2.5"].dropna().values
    pm10_d = np.mean(pm10_vals) if len(pm10_vals) >= 18 else np.nan
    pm25_d = np.mean(pm25_vals) if len(pm25_vals) >= 18 else np.nan

    # O3: máximo horario del día (mín 18 válidos)
    o3_vals = ddf["O3"].dropna().values
    o3_max  = np.max(o3_vals) if len(o3_vals) >= 18 else np.nan

    # CO: máximo del promedio 8h (mín 18 válidos)
    co_vals = ddf["CO_8h"].dropna().values
    co_max  = np.max(co_vals) if len(co_vals) >= 18 else np.nan

    # Categorías diarias
    cats = {}
    resp = []
    for pol, val, thr in [("PM10", pm10_d, "PM10"), ("PM25", pm25_d, "PM25"),
                           ("O3", o3_max, "O3"), ("CO", co_max, "CO")]:
        c = get_cat(val, THRESHOLDS[thr])
        cats[pol] = c

    # Categoría global = peor
    valid_cats = {k: v for k, v in cats.items() if v >= 0}
    if not valid_cats:
        cat_global = -1
    else:
        cat_global = max(valid_cats.values())
        resp = [k for k, v in valid_cats.items() if v == cat_global]

    # Variables meteo del día
    et_mean = ddf["ET"].mean() if ddf["ET"].notna().any() else np.nan
    rh_mean = ddf["RH"].mean() if ddf["RH"].notna().any() else np.nan
    ws_mean = ddf["WS"].mean() if ddf["WS"].notna().any() else np.nan

    daily.append({
        "dia": int(dia),
        "fecha": f"2023-03-{int(dia):02d}",
        "n_valid_pm10": int(len(pm10_vals)),
        "n_valid_pm25": int(len(pm25_vals)),
        "n_valid_o3":   int(len(o3_vals)),
        "n_valid_co":   int(len(co_vals)),
        "PM10_d24h": round(pm10_d, 1) if not np.isnan(pm10_d) else None,
        "PM25_d24h": round(pm25_d, 1) if not np.isnan(pm25_d) else None,
        "O3_max":    round(o3_max, 3) if not np.isnan(o3_max) else None,
        "CO_max8h":  round(co_max, 2) if not np.isnan(co_max) else None,
        "cat_PM10":  int(cats["PM10"]),
        "cat_PM25":  int(cats["PM25"]),
        "cat_O3":    int(cats["O3"]),
        "cat_CO":    int(cats["CO"]),
        "cat_global": int(cat_global),
        "responsable": "+".join(resp) if resp else "ND",
        "cat_nombre": CAT_NAMES[cat_global] if cat_global >= 0 else "ND",
        "temp_media": round(et_mean, 1) if not np.isnan(et_mean) else None,
        "humedad_media": round(rh_mean, 1) if not np.isnan(rh_mean) else None,
        "viento_medio": round(ws_mean, 2) if not np.isnan(ws_mean) else None,
    })

df_daily = pd.DataFrame(daily)
print(f"   Días procesados: {len(df_daily)}")
cat_counts = df_daily["cat_nombre"].value_counts()
print(f"   Distribución categorías:\n{cat_counts.to_string()}")

# ────────────────────────────────────────────────
# 4. DETECCIÓN DE ATÍPICOS
# ────────────────────────────────────────────────
print("\n4. Detectando atípicos…")

atipicos = []

for col, label, thr_factor in [("O3", "O3 (ppm)", 3.0),
                                 ("CO", "CO (ppm)", 3.0),
                                 ("PM10", "PM10 (µg/m³)", 3.0),
                                 ("PM2.5", "PM2.5 (µg/m³)", 3.0),
                                 ("ET", "Temp. Ext. (°C)", 3.0)]:
    if col not in df.columns:
        continue
    series = df[col].dropna()
    if len(series) < 10:
        continue
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5*iqr, q3 + 1.5*iqr
    mean, std = series.mean(), series.std()
    z_lo, z_hi = mean - thr_factor*std, mean + thr_factor*std

    mask = (df[col].notna()) & ((df[col] < lo) | (df[col] > hi) |
                                  (df[col] < z_lo) | (df[col] > z_hi))
    outlier_rows = df[mask]
    for _, row in outlier_rows.iterrows():
        atipicos.append({
            "Fecha": str(row["DATE"])[:10],
            "Hora": int(row["HOUR"]),
            "Contaminante": label,
            "Valor": round(float(row[col]), 4) if not np.isnan(row[col]) else None,
            "Q1": round(q1, 4),
            "Q3": round(q3, 4),
            "IQR": round(iqr, 4),
            "Límite_bajo_IQR": round(lo, 4),
            "Límite_alto_IQR": round(hi, 4),
            "Media": round(mean, 4),
            "DE": round(std, 4),
            "Método_detección": "IQR + Z-score",
            "Interpretación": "Tráfico matutino / evento real" if row["HOUR"] in range(5, 11) else "Revisar",
        })

df_atipicos = pd.DataFrame(atipicos)
print(f"   Atípicos detectados: {len(df_atipicos)}")

# ────────────────────────────────────────────────
# 5. GUARDAR ARCHIVOS
# ────────────────────────────────────────────────
print("\n5. Guardando archivos…")

# 5a. Datos horarios procesados
export_cols = ["DATE","HOUR","DIA","O3","CO","CO_8h","PM10","PM10_nc","PM2.5","PM25_nc",
               "ET","RH","WS","WD","cat_O3","cat_CO","cat_PM10","cat_PM25","cat_global"]
export_cols = [c for c in export_cols if c in df.columns]
df_export = df[export_cols].copy()
df_export.to_csv(os.path.join(DATOS, "CEN_marzo2023_procesado.csv"), index=False, encoding="utf-8")
print("   -> CEN_marzo2023_procesado.csv")

# 5b. Numeralia diaria
df_daily.to_csv(os.path.join(DATOS, "numeralia_diaria_2023.csv"), index=False, encoding="utf-8")
print("   -> numeralia_diaria_2023.csv")

# 5c. Reporte atípicos (XLSX)
ruta_xlsx = os.path.join(DATOS, "Reporte_Atipicos_CEN_marzo2023.xlsx")
with pd.ExcelWriter(ruta_xlsx, engine="openpyxl") as writer:
    df_atipicos.to_excel(writer, sheet_name="Atípicos", index=False)
    df_daily.to_excel(writer, sheet_name="Indicadores_Diarios", index=False)

    # Estadísticas resumen
    resumen_rows = []
    for col in ["O3","CO","PM10","PM2.5","ET","RH","WS"]:
        if col in df.columns and df[col].notna().any():
            s = df[col].dropna()
            resumen_rows.append({
                "Variable": col,
                "N_validos": len(s),
                "N_faltantes": len(df) - len(s),
                "Pct_disponibilidad": round(len(s)/len(df)*100, 1),
                "Min": round(s.min(), 4),
                "Q1": round(s.quantile(0.25), 4),
                "Mediana": round(s.median(), 4),
                "Media": round(s.mean(), 4),
                "Q3": round(s.quantile(0.75), 4),
                "Max": round(s.max(), 4),
                "DE": round(s.std(), 4),
            })
    pd.DataFrame(resumen_rows).to_excel(writer, sheet_name="Estadísticas", index=False)
print("   -> Reporte_Atipicos_CEN_marzo2023.xlsx")

# 5d. JSON compacto para dashboard
# Formato igual al que usa index.html (array h con datos horarios, array d con diarios)
h_data = []
for _, row in df.iterrows():
    h_data.append([
        round(row["O3"], 3) if not np.isnan(row["O3"]) else None,
        round(row["CO_8h"], 2) if "CO_8h" in df.columns and not np.isnan(row["CO_8h"]) else None,
        round(row["PM10_nc"], 1) if "PM10_nc" in df.columns and not np.isnan(row["PM10_nc"]) else None,
        round(row["PM25_nc"], 1) if "PM25_nc" in df.columns and not np.isnan(row["PM25_nc"]) else None,
        round(row["PM10"], 1) if not np.isnan(row["PM10"]) else None,
        round(row["PM2.5"], 1) if not np.isnan(row["PM2.5"]) else None,
        round(row["CO"], 3) if not np.isnan(row["CO"]) else None,
        round(row["ET"], 1) if "ET" in df.columns and not np.isnan(row["ET"]) else None,
        round(row["WD"], 0) if "WD" in df.columns and not np.isnan(row["WD"]) else None,
        round(row["RH"], 1) if "RH" in df.columns and not np.isnan(row["RH"]) else None,
        round(row["WS"], 2) if "WS" in df.columns and not np.isnan(row["WS"]) else None,
    ])

d_data = []
for _, row in df_daily.iterrows():
    r_list = row["responsable"].split("+") if row["responsable"] != "ND" else []
    d_data.append({
        "c": int(row["cat_global"]),
        "r": r_list,
        "v": [
            row["PM10_d24h"],
            row["PM25_d24h"],
            row["O3_max"],
            row["CO_max8h"],
        ],
        "temp": row["temp_media"],
        "hum": row["humedad_media"],
        "viento": row["viento_medio"],
    })

# Disponibilidad por variable
av_data = []
total = len(df)
for col, label in [("O3","O₃"), ("CO","CO"), ("PM10","PM₁₀"), ("PM2.5","PM₂.₅"),
                    ("ET","Temp. Ext."), ("RH","Humedad rel."), ("WS","Vel. viento"),
                    ("WD","Dir. viento"), ("IT","Temp. Int.")]:
    if col in df.columns:
        n_null = int(df[col].isna().sum())
        pct = round(df[col].notna().sum() / total * 100, 1)
        av_data.append([label, pct, n_null])

dashboard_json = {
    "year": 2023,
    "h": h_data,
    "d": d_data,
    "av": av_data,
}

with open(os.path.join(DATOS, "dashboard_2023.json"), "w", encoding="utf-8") as f:
    json.dump(dashboard_json, f, ensure_ascii=False)
print("   -> dashboard_2023.json")

# ────────────────────────────────────────────────
# 6. RESUMEN FINAL
# ────────────────────────────────────────────────
print("\n" + "="*60)
print("RESUMEN — CEN Marzo 2023")
print("="*60)
print(f"Total registros horarios: {len(df)}")
print(f"Días procesados: {len(df_daily)}")
print(f"Distribución por categoría:")
for cat_i, cat_n in enumerate(CAT_NAMES):
    count = (df_daily["cat_global"] == cat_i).sum()
    if count > 0:
        print(f"  {cat_n}: {count} días ({round(count/len(df_daily)*100)}%)")
print(f"Días sin dato: {(df_daily['cat_global'] == -1).sum()}")
print(f"Atípicos detectados: {len(df_atipicos)}")

dom_counts = {}
for _, row in df_daily.iterrows():
    for pol in row["responsable"].split("+"):
        if pol and pol != "ND":
            dom_counts[pol] = dom_counts.get(pol, 0) + 1
print(f"Dominancia de contaminantes: {dom_counts}")
print(f"\nArchivos generados en: {DATOS}")
