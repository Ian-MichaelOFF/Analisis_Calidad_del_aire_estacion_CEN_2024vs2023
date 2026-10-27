# -*- coding: utf-8 -*-
"""
Genera Reporte_Atipicos_CEN_marzo2023.xlsx con el formato del reporte de 2024:
"""

import os
import pandas as pd
import numpy as np

BASE = r"c:\Users\Hulas\Downloads\Calidad del aire en la estación Centro (CEN), marzo 2024"
DATOS = os.path.join(BASE, "Datos")

df_raw = pd.read_csv(os.path.join(DATOS, "CEN_marzo2023.csv"), sep=";", encoding="utf-8", low_memory=False)
df = df_raw[df_raw["STATION"].astype(str).str.strip() == "CEN"].dropna(subset=["DATE"]).copy()
df = df.reset_index(drop=True)

# Normalizar columnas numéricas
num_cols = ["O3","SO2","CO","PM10","PM2.5","IT","ET","RH","WS","WD","PP","ATM","RS","UVI","NO","NO2","NOX"]
for col in num_cols:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ".").str.strip(), errors="coerce")

observaciones = []

# 1. Inconsistencias estructurales (columnas 100% nulas)
for col in df.columns:
    if col in ["STATION", "DATE", "HOUR", "dia ", "mes "]:
        continue
    if df[col].isna().all():
        observaciones.append({
            "Fila Original": "Todas",
            "Fecha y Hora": "Todo el mes",
            "Columna": col,
            "Valor Original": None,
            "Tipo de Observación": "Inconsistencia Estructural",
            "Detalle": "Columna completamente vacía (100% nulos)"
        })

# 2. Valores Faltantes
pollutants_and_met = ["O3", "CO", "PM10", "PM2.5", "SO2", "ET", "RH", "WS", "WD", "IT", "ATM", "PP"]
for col in pollutants_and_met:
    if col in df.columns and not df[col].isna().all():
        missing_idx = df[df[col].isna()].index
        for idx in missing_idx:
            row = df.loc[idx]
            dt_str = f"{row['DATE']} {int(row['HOUR']):02d}:00:00" if "HOUR" in df.columns else str(row["DATE"])
            observaciones.append({
                "Fila Original": int(idx + 2),
                "Fecha y Hora": dt_str,
                "Columna": col,
                "Valor Original": None,
                "Tipo de Observación": "Valor Faltante",
                "Detalle": "Dato nulo / no registrado"
            })

# 3. Valores Atípicos Estadísticos (Outlier) por IQR 1.5
for col in pollutants_and_met:
    if col in df.columns:
        s = df[col].dropna()
        if len(s) > 10:
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            lo = round(float(q1 - 1.5 * iqr), 4)
            hi = round(float(q3 + 1.5 * iqr), 4)
            outliers = df[(df[col].notna()) & ((df[col] < lo) | (df[col] > hi))]
            for idx, row in outliers.iterrows():
                val = row[col]
                dt_str = f"{row['DATE']} {int(row['HOUR']):02d}:00:00" if "HOUR" in df.columns else str(row["DATE"])
                observaciones.append({
                    "Fila Original": int(idx + 2),
                    "Fecha y Hora": dt_str,
                    "Columna": col,
                    "Valor Original": str(val).replace(".", ","),
                    "Tipo de Observación": "Valor Atípico Estadístico (Outlier)",
                    "Detalle": f"Valor numérico analizado ({val}) fuera del límite esperado [{lo}, {hi}]"
                })

df_obs = pd.DataFrame(observaciones)

# Resumen
resumen = df_obs["Tipo de Observación"].value_counts().reset_index()
resumen.columns = ["Tipo de Observación", "Cantidad"]

out_file = os.path.join(DATOS, "Reporte_Atipicos_CEN_marzo2023.xlsx")
with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
    df_obs.to_excel(writer, sheet_name="Valores Atípicos e Inconsist", index=False)
    resumen.to_excel(writer, sheet_name="Resumen", index=False)

print("Reporte generado exitosamente con formato idéntico a 2024:")
print(resumen)
print(f"Total registros en 'Valores Atípicos e Inconsist': {len(df_obs)}")
