import pandas as pd
import numpy as np
import json
import os
import warnings
warnings.filterwarnings("ignore")

DATOS = os.getcwd()

# ── Umbrales 2023 (Previo a la actualización estricta) ──
THRESHOLDS = {
    "O3":   [0.058, 0.090, 0.135, 0.175],
    "CO":   [5.0,   9.0,  12.0,  16.0],
    "PM10": [50.0,  70.0, 154.0, 277.0], # Límite Aceptable 70
    "PM25": [25.0,  41.0,  79.0, 147.0], # Límite Aceptable 41
}
CAT_NAMES = ["Buena", "Aceptable", "Mala", "Muy mala", "Extremadamente mala"]

def get_cat(val, thresholds):
    if val is None or (isinstance(val, float) and np.isnan(val)): return -1
    for i, t in enumerate(thresholds):
        if val < t if i == 0 else val <= t: return i
    return 4

def get_cat_arr(arr, thresholds): return np.array([get_cat(v, thresholds) for v in arr])
def safe(val, dec): return round(float(val), dec) if not np.isnan(val) else None

print("Procesando BD_2023 (1).xlsx ...")
df_raw = pd.read_excel(os.path.join(DATOS, "BD_2023 (1).xlsx"))
df = df_raw[df_raw["STATION"].astype(str).str.strip() == "CEN"].dropna(subset=["DATE"]).copy().reset_index(drop=True)

num_cols = ["O3","SO2","CO","PM10","PM2.5","IT","ET","RH","WS","WD","PP","ATM","RS","UVI"]
for col in num_cols:
    if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ".").str.strip(), errors="coerce")

df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
df = df.sort_values("DATE").reset_index(drop=True)
df["HOUR"], df["DIA"], df["MES"] = df["DATE"].dt.hour, df["DATE"].dt.day, df["DATE"].dt.month

# ── NowCast y CO 8h ──
co_raw = df["CO"].values
co_8h = np.full(len(df), np.nan)
for i in range(len(df)):
    window = co_raw[max(0, i-7):i+1]
    valid = window[~np.isnan(window)]
    if len(valid) >= 6: co_8h[i] = np.mean(valid)
df["CO_8h"] = co_8h

def nowcast(series, min_valid=2):
    res = np.full(len(series), np.nan)
    for i in range(len(series)):
        window = series[max(0, i-11):i+1]
        valid = window[~np.isnan(window)]
        if len(valid) < min_valid: continue
        c_min, c_max = np.nanmin(window), np.nanmax(window)
        w = max(1 - ((c_max - c_min) / c_max) if c_max > 0 else 1, 0.5)
        weights = np.array([w ** k for k in range(len(window)-1, -1, -1)])
        mask = ~np.isnan(window)
        if mask.sum() >= min_valid: res[i] = np.sum(weights[mask] * window[mask]) / np.sum(weights[mask])
    return res

df["PM10_nc"] = nowcast(df["PM10"].values)
df["PM25_nc"] = nowcast(df["PM2.5"].values)

df["cat_O3"] = get_cat_arr(df["O3"].values, THRESHOLDS["O3"])
df["cat_CO"] = get_cat_arr(df["CO_8h"].values, THRESHOLDS["CO"])
df["cat_PM10"] = get_cat_arr(df["PM10_nc"].values, THRESHOLDS["PM10"])
df["cat_PM25"] = get_cat_arr(df["PM25_nc"].values, THRESHOLDS["PM25"])
df["cat_global"] = df.apply(lambda r: max([c for c in [r.cat_O3, r.cat_CO, r.cat_PM10, r.cat_PM25] if c >= 0], default=-1), axis=1)

# ── Indicadores Diarios ──
daily = []
for (mes, dia), ddf in df.groupby(["MES", "DIA"]):
    pm10_v, pm25_v, o3_v, co_v = ddf["PM10"].dropna(), ddf["PM2.5"].dropna(), ddf["O3"].dropna(), ddf["CO_8h"].dropna()
    pm10_d = pm10_v.mean() if len(pm10_v) >= 18 else np.nan
    pm25_d = pm25_v.mean() if len(pm25_v) >= 18 else np.nan
    o3_max = o3_v.max() if len(o3_v) >= 18 else np.nan
    co_max = co_v.max() if len(co_v) >= 18 else np.nan

    cats = {"PM10": get_cat(pm10_d, THRESHOLDS["PM10"]), "PM25": get_cat(pm25_d, THRESHOLDS["PM25"]),
            "O3": get_cat(o3_max, THRESHOLDS["O3"]), "CO": get_cat(co_max, THRESHOLDS["CO"])}
    v_cats = {k: v for k, v in cats.items() if v >= 0}
    c_glob = max(v_cats.values()) if v_cats else -1
    daily.append({
        "mes": int(mes), "dia": int(dia), "fecha": f"2023-{int(mes):02d}-{int(dia):02d}",
        "PM10_d24h": safe(pm10_d, 1), "PM25_d24h": safe(pm25_d, 1), "O3_max": safe(o3_max, 3), "CO_max8h": safe(co_max, 2),
        "cat_global": c_glob, "responsable": "+".join([k for k, v in v_cats.items() if v == c_glob]) if v_cats else "ND",
        "temp": safe(ddf["ET"].mean(), 1), "hum": safe(ddf["RH"].mean(), 1), "viento": safe(ddf["WS"].mean(), 2)
    })
df_daily = pd.DataFrame(daily)

# ── JSON ──
h_data = [[safe(r.get("O3"),3), safe(r.get("CO_8h"),2), safe(r.get("PM10_nc"),1), safe(r.get("PM25_nc"),1), safe(r.get("PM10"),1), safe(r.get("PM2.5"),1), safe(r.get("CO"),3), safe(r.get("ET"),1), safe(r.get("WD"),0), safe(r.get("RH"),1), safe(r.get("WS"),2), int(r.MES), int(r.DIA)] for _, r in df.iterrows()]
d_data = [{"m": r.mes, "d": r.dia, "c": r.cat_global, "r": r.responsable.split("+") if r.responsable != "ND" else [], "v": [r.PM10_d24h, r.PM25_d24h, r.O3_max, r.CO_max8h], "temp": r.temp} for _, r in df_daily.iterrows()]
av_data = [[l, round(df[c].notna().sum()/len(df)*100,1), int(df[c].isna().sum())] for c, l in [("O3","O₃"), ("CO","CO"), ("PM10","PM₁₀"), ("PM2.5","PM₂.₅"), ("ET","Temp"), ("RH","Hum"), ("WS","Viento")] if c in df.columns]

with open(os.path.join(DATOS, "datos_2023.js"), "w", encoding="utf-8") as f:
    f.write("window.DATA_2023 = ")
    json.dump({"year": 2023, "h": h_data, "d": d_data, "av": av_data}, f, ensure_ascii=False)
    f.write(";")

df_daily.to_csv(os.path.join(DATOS, "numeralia_diaria_2023.csv"), index=False)
print("¡Generados datos_2023.js y numeralia_diaria_2023.csv!")