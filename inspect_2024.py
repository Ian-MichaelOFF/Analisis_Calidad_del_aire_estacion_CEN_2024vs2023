# -*- coding: utf-8 -*-
import sys, io
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

"""
Genera dashboard_2024.json a partir de CEN_marzo.xlsx (datos ya procesados por los notebooks).
"""

import pandas as pd
import numpy as np
import json
import os

BASE  = r"C:\Users\Hulas\Downloads\Calidad del aire en la estación Centro (CEN), marzo 2024"
DATOS = os.path.join(BASE, "Datos")

THRESHOLDS = {
    "O3":   [0.058, 0.090, 0.135, 0.175],
    "CO":   [5.0,   9.0,  12.0,  16.0],
    "PM10": [45.0,  60.0, 132.0, 213.0],
    "PM25": [15.0,  33.0,  79.0, 130.0],
}
CAT_NAMES = ["Buena", "Aceptable", "Mala", "Muy mala", "Extremadamente mala"]

def get_cat(val, thresholds):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return -1
    for i, t in enumerate(thresholds):
        if (i == 0 and val < t) or (i > 0 and val <= t):
            return i
    return 4

def safe(val, dec=None):
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    if dec is not None:
        return round(float(val), dec)
    return val

print("Cargando CEN_marzo.xlsx (2024)...")
df = pd.read_excel(os.path.join(DATOS, "CEN_marzo.xlsx"))
df["DIA"] = df["DATE"].dt.day
df["HOUR"] = df["DATE"].dt.hour

# Map categoria text to int
CAT_MAP = {"Buena": 0, "Aceptable": 1, "Mala": 2, "Muy mala": 3, "Extremadamente mala": 4}

# ── Indicadores diarios ──
days = sorted(df["DIA"].unique())
daily = []

for dia in days:
    ddf = df[df["DIA"] == dia]

    pm10_vals = ddf["PM10"].dropna()
    pm25_vals = ddf["PM2.5"].dropna()
    o3_vals   = ddf["O3"].dropna()
    co_vals   = ddf["CO_prom_8h"].dropna() if "CO_prom_8h" in ddf.columns else pd.Series([], dtype=float)

    pm10_d = pm10_vals.mean() if len(pm10_vals) >= 18 else np.nan
    pm25_d = pm25_vals.mean() if len(pm25_vals) >= 18 else np.nan
    o3_max  = o3_vals.max() if len(o3_vals) >= 18 else np.nan
    co_max  = co_vals.max() if len(co_vals) >= 18 else np.nan

    cats = {
        "PM10": get_cat(pm10_d, THRESHOLDS["PM10"]),
        "PM25": get_cat(pm25_d, THRESHOLDS["PM25"]),
        "O3":   get_cat(o3_max, THRESHOLDS["O3"]),
        "CO":   get_cat(co_max, THRESHOLDS["CO"]),
    }
    valid_cats = {k: v for k, v in cats.items() if v >= 0}
    cat_global = max(valid_cats.values()) if valid_cats else -1
    resp = [k for k, v in valid_cats.items() if v == cat_global]

    et_mean = ddf["ET"].mean() if "ET" in ddf.columns and ddf["ET"].notna().any() else np.nan

    daily.append({
        "dia": int(dia),
        "fecha": f"2024-03-{int(dia):02d}",
        "PM10_d24h": safe(pm10_d, 1),
        "PM25_d24h": safe(pm25_d, 1),
        "O3_max":    safe(o3_max, 3),
        "CO_max8h":  safe(co_max, 2),
        "cat_PM10":  cats["PM10"],
        "cat_PM25":  cats["PM25"],
        "cat_O3":    cats["O3"],
        "cat_CO":    cats["CO"],
        "cat_global": cat_global,
        "responsable": "+".join(resp) if resp else "ND",
        "cat_nombre": CAT_NAMES[cat_global] if cat_global >= 0 else "ND",
        "temp_media": safe(et_mean, 1),
    })

df_daily = pd.DataFrame(daily)
df_daily.to_csv(os.path.join(DATOS, "numeralia_diaria_2024.csv"), index=False, encoding="utf-8")
print(f"-> numeralia_diaria_2024.csv ({len(df_daily)} días)")

# ── JSON para dashboard ──
h_data = []
for _, row in df.iterrows():
    h_data.append([
        safe(row.get("O3"), 3),
        safe(row.get("CO_prom_8h"), 2),
        safe(row.get("PM10_NowCast"), 1),
        safe(row.get("PM2.5_NowCast"), 1),
        safe(row.get("PM10"), 1),
        safe(row.get("PM2.5"), 1),
        safe(row.get("CO"), 3),
        safe(row.get("ET"), 1),
        safe(row.get("WD"), 0),
        None,  # RH not in 2024
        None,  # WS not in 2024
    ])

d_data = []
for _, row in df_daily.iterrows():
    r_list = row["responsable"].split("+") if row["responsable"] != "ND" else []
    d_data.append({
        "c": int(row["cat_global"]),
        "r": r_list,
        "v": [row["PM10_d24h"], row["PM25_d24h"], row["O3_max"], row["CO_max8h"]],
        "temp": row["temp_media"],
    })

total = len(df)
av_data = []
for col, label in [("O3","O3"), ("CO","CO"), ("PM10","PM10"), ("PM2.5","PM2.5"),
                    ("ET","Temp. Ext."), ("WD","Dir. viento")]:
    if col in df.columns:
        n_null = int(df[col].isna().sum())
        pct = round(df[col].notna().sum() / total * 100, 1)
        av_data.append([label, pct, n_null])

dashboard_json = {"year": 2024, "h": h_data, "d": d_data, "av": av_data}

with open(os.path.join(DATOS, "dashboard_2024.json"), "w", encoding="utf-8") as f:
    json.dump(dashboard_json, f, ensure_ascii=False)
print("-> dashboard_2024.json")

# Resumen
cat_counts = df_daily["cat_nombre"].value_counts()
print(f"\nDistribucion 2024:")
print(cat_counts.to_string())
dom_counts = {}
for _, row in df_daily.iterrows():
    for pol in row["responsable"].split("+"):
        if pol and pol != "ND":
            dom_counts[pol] = dom_counts.get(pol, 0) + 1
print(f"Dominancia: {dom_counts}")
