"""Rebuild initial economic scenarios and climate inputs from frozen public data."""

import csv
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import xlrd

from data.ingest.weather import compare_rain, normalize_power

RAW = Path("data/raw/margins")
OUT = Path("data/processed/margins")
PLACES = {"3170206": ("Uberlândia", "Unaí", "uberlandia"),
          "5107925": ("Sorriso", "Sorriso", "sorriso")}
PANELS = {"3170206": {"soy": "Unaí-MG-2023", "maize2": "Unaí-MG-2024"},
          "5107925": {"soy": "OGM-Sorriso-MT-2023", "maize2": "Sorriso-MT-2024"}}


def write_csv(name, rows):
    with (OUT / name).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def read_pam(name):
    records = json.loads((RAW / name).read_bytes())[1:]
    return {(r["D1C"], r["D3C"], r["D4C"], r["D2C"]): D(r["V"])
            for r in records if r["V"] not in {"-", "X", "x", "...", "..", ""}}


def round2(value):
    return value.quantize(D("0.01"))


def calculate():
    annual, by_season = read_pam("pam_history.json"), read_pam("pam839.json")
    history = []
    for code, (town, _, _) in PLACES.items():
        for year in map(str, range(2016, 2026)):
            for crop, records, crop_code in [
                ("soy", annual, "40124"), ("maize1", by_season, "114253"),
                ("maize2", by_season, "114254"), ("maize_all", annual, "40122"),
            ]:
                key = (code, year, crop_code)
                history.append({
                    "municipality_code": code, "municipality": town, "year": year, "crop": crop,
                    "area_planted_ha": records.get(key + ("8331" if records is annual else "109",)),
                    "area_harvested_ha": records.get(key + ("216",)),
                    "production_t": records.get(key + ("214",)),
                    "yield_kg_ha": records.get(key + ("112",)),
                    "gross_value_thousand_brl": records.get(key + ("215",)),
                })
    write_csv("pam_history.csv", history)
    with (RAW / "prices.csv").open(newline="", encoding="utf-8") as stream:
        quotes = {(r["municipality_code"], r["crop"]): r for r in csv.DictReader(stream)}
    books = {crop: xlrd.open_workbook(RAW / name, on_demand=True)
             for crop, name in {"soy": "soja.xls", "maize2": "milho2.xls"}.items()}
    inputs, scenarios = [], []
    for code, (town, cost_town, _) in PLACES.items():
        for crop in ("soy", "maize2"):
            annual_code = "40124" if crop == "soy" else "40122"
            pam_key = (code, "2024", annual_code)
            implied = annual[pam_key + ("215",)] * 60 / annual[pam_key + ("214",)]
            yield_key = pam_key if crop == "soy" else (code, "2024", "114254")
            yield_data = annual if crop == "soy" else by_season
            observed_yield = yield_data[yield_key + ("112",)]
            sheet = books[crop].sheet_by_name(PANELS[code][crop])
            for row, label in [(42, "CUSTO VARIÁVEL"), (55, "CUSTO OPERACIONAL"), (60, "CUSTO TOTAL")]:
                if not sheet.cell_value(row, 0).startswith(label):
                    raise ValueError(f"Unexpected worksheet layout: {sheet.name}")
            costs = {label: round2(D(str(sheet.cell_value(row, 1)))) for label, row in
                     [("variable", 42), ("operational", 55), ("total", 60)]}
            quote = quotes.get((code, crop))
            price = D(quote["price_brl_60kg"]) if quote else implied
            source = "Conab weekly producer" if quote else "PAM 5457 implied annual price"
            inputs.append({
                "municipality_code": code, "municipality": town, "crop": crop, "harvest_year": 2024,
                "pam_table": 5457 if crop == "soy" else 839,
                "yield_kg_ha": observed_yield, "harvested_area_ha": yield_data[yield_key + ("216",)],
                "price_brl_60kg": round2(price), "price_basis": source,
                "quote_end": quote["week_end"] if quote else "", "price_url": quote["source_url"] if quote else "https://sidra.ibge.gov.br/tabela/5457",
                "pam_implied_brl_60kg": round2(implied), "cost_municipality": cost_town,
                "regional_cost_proxy": cost_town != town, "cost_sheet": sheet.name,
                "cost_safra": sheet.cell_value(2, 0).strip(), "cost_reference": sheet.cell_value(4, 0).strip(),
                "panel_yield": sheet.cell_value(5, 0).strip(), "cost_cells": "B43/B56/B61",
                **{f"cost_{k}_brl_ha": v for k, v in costs.items()},
            })
            for price_case, selected_price in [("base", price), ("pam_annual", implied)]:
                for loss in map(D, ["0", "0.10", "0.20", "0.30"]):
                    output_yield = observed_yield * (1 - loss)
                    revenue = output_yield * selected_price / 60
                    for scope, cost in costs.items():
                        scenarios.append({
                            "municipality_code": code, "municipality": town, "crop": crop,
                            "price_case": price_case, "price_brl_60kg": round2(selected_price),
                            "yield_loss_fraction": loss, "yield_kg_ha": round2(output_yield),
                            "revenue_brl_ha": round2(revenue), "cost_basis": scope,
                            "cost_brl_ha": cost, "margin_brl_ha": round2(revenue - cost),
                            "break_even_kg_ha": round2(cost * 60 / selected_price),
                            "break_even_brl_60kg_at_scenario_yield": round2(cost * 60 / output_yield),
                        })
    write_csv("inputs.csv", inputs)
    write_csv("scenarios.csv", scenarios)
    for row in scenarios:
        if row["price_case"] == "base" and row["cost_basis"] == "total":
            print(row["crop"], row["municipality_code"], row["yield_loss_fraction"], row["margin_brl_ha"])


def climate():
    coordinates = {"uberlandia": (-48.409, -18.9035), "sorriso": (-55.72, -12.58)}
    start, end = date(2016, 1, 1), date(2024, 12, 31)
    for code, (_, _, slug) in PLACES.items():
        payload = json.loads((RAW / f"power_{slug}.json").read_bytes())
        point = payload["geometry"]["coordinates"][:2]
        if any(abs(a - b) > .00051 for a, b in zip(point, coordinates[slug])):
            raise ValueError("Unexpected NASA POWER point")
        rows = normalize_power(payload, start, end)
        series = payload["properties"]["parameter"]
        for parameter, unit in [("RH2M", "%"), ("WS2M", "m/s")]:
            if payload["parameters"][parameter]["units"] != unit:
                raise ValueError(f"Unexpected unit of {parameter}")
        for i, row in enumerate(rows):
            key = (start + timedelta(days=i)).strftime("%Y%m%d")
            humidity, wind = series["RH2M"][key], series["WS2M"][key]
            if not 0 <= humidity <= 100 or not wind >= 0 or None in row.values():
                raise ValueError(f"Invalid or incomplete climate data: {slug}, {key}")
            row.update(municipality_code=code, rh2m_pct=humidity, ws2m_m_s=wind)
        write_csv(f"climate_{slug}.csv", rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    climate()
    calculate()
    field = json.loads((RAW / "sorriso_hls/field.json").read_bytes())
    reference_path = RAW / "sorriso_imerg/imerg_final_v07.csv"
    provenance = json.loads((reference_path.parent / "imerg_final_v07.manifest.json").read_bytes())
    if (provenance["cell_lat"], provenance["cell_lon"], provenance["file_version"],
            provenance["units"], provenance["time_standard"], provenance["run"], provenance["sha256"]) != (
            -12.55, -55.75, "V07B", "mm/day", "UTC", "Final", hashlib.sha256(reference_path.read_bytes()).hexdigest()):
        raise ValueError("Unexpected IMERG reference point or version")
    with reference_path.open(newline="", encoding="utf-8") as stream:
        reference = {row["day_utc"]: float(row["rain_mm"]) if row["rain_mm"] else None
                     for row in csv.DictReader(stream)}
    rainfall = [{"day_utc": row["date"], "rain_mm": row["rain_mm"]} for row in field["rainfall"]]
    if set(reference) != {row["day_utc"] for row in rainfall}:
        raise ValueError("IMERG reference period differs from observation package")
    report = compare_rain(rainfall, reference)
    (OUT / "sorriso_rain_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Sorriso IMERG:", report["matched_days"], "days;", report["mismatched_days"],
          "above tolerance; max diff", report["max_abs_mm"])
    paths = sorted(p for p in RAW.rglob("*") if p.is_file())
    checksums = {p.relative_to(RAW).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (OUT / "raw_sha256.json").write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
