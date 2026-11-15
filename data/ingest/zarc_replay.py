"""Normalize MAPA planting windows and replay NASA POWER weather by crop phase."""

import argparse
import calendar
import csv
import hashlib
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

RAW = Path("data/raw/zarc")
OUT = Path("data/processed/zarc")
CLIMATE = Path("data/processed/margins")
PLACES = {"3170206": "uberlandia", "5107925": "sorriso"}
CROPS = {"Soja": "soy", "Milho 2ª Safra": "maize2"}
GROUPS = {"20": "I", "21": "II", "22": "III"}
PORTARIAS = {("3170206", "soy"): 200, ("5107925", "soy"): 202,
             ("3170206", "maize2"): 324, ("5107925", "maize2"): 326}
PHASES = {"soy": {"I": (15, 30, 40, 15), "II": (15, 35, 45, 20), "III": (15, 40, 50, 25)},
          "maize2": {"I": (20, 30, 30, 20), "II": (20, 40, 40, 20), "III": (20, 50, 50, 20)}}
SOILS = {"soy": (24, 32, 42, 55, 72, 95), "maize2": (24, 31.8, 41.4, 54.6, 72.6, 95.4)}
NAMES = ("establishment", "vegetative", "reproductive", "maturation")
DAY = timedelta(days=1)


def dates_for_decendio(first_year, index):
    if not 1 <= index <= 36:
        raise ValueError("Decendio must be 1–36")
    month, slot = divmod(index - 1, 3)
    month += 1
    year = first_year if month >= 7 else first_year + 1
    start = date(year, month, 1 + 10 * slot)
    finish = date(year, month, 10 * (slot + 1) if slot < 2 else calendar.monthrange(year, month)[1])
    return start, finish


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def read_windows(path):
    fields, combinations = [], set()
    with path.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file, delimiter=";"):
            code = row["geocodigo"]
            crop = CROPS.get(row["Nome_cultura"])
            if code not in PLACES or not crop:
                continue
            number = PORTARIAS[code, crop]
            if (row["SafraIni"], row["SafraFin"], row["Cod_Outros_Manejos"], row["Cod_Clima"]) != ("2026", "2027", "1", "0"):
                raise ValueError("Unexpected season, management or climate class")
            if not row["Portaria"].startswith(f"Port.{number}_"):
                raise ValueError("Unexpected portaria")
            group = GROUPS[row["Cod_Ciclo"]]
            soil = int(row["Cod_Solo"]) - 10
            key = code, crop, group, soil
            if key in combinations or not 1 <= soil <= 6 or group not in PHASES[crop]:
                raise ValueError(f"Unexpected source combination {key}")
            combinations.add(key)
            for d in range(1, 37):
                risk = int(row[f"dec{d}"])
                if risk not in (0, 20, 30, 40):
                    raise ValueError(f"Unexpected ZARC category: {risk}")
                start, end = dates_for_decendio(2026, d)
                fields.append(dict(municipality_code=code, municipality=row["municipio"], state=row["UF"],
                                   crop=crop, zarc_season="2026/27", group=group, soil=f"AD{soil}",
                                   management="sequeiro", zarc_management_level_code=row["Cod_NM"],
                                   decendio=d, starts_on=start, ends_on=end, indicated=bool(risk),
                                   zarc_risk_pct=risk or None, source_risk_code=risk,
                                   portaria=row["Portaria"], cad_mm=SOILS[crop][soil - 1]))
    expected = {(code, crop, group, ad) for code in PLACES for crop in CROPS.values()
                for group in (("I", "II", "III") if crop == "soy" else ("I", "II"))
                for ad in range(1, 7)}
    if combinations != expected:
        raise ValueError(f"Unexpected coverage: missing {expected-combinations}; extra {combinations-expected}")
    return sorted(fields, key=lambda w: (w["municipality_code"], w["crop"], w["group"], w["soil"], w["starts_on"]))


def climate_index(place):
    path = CLIMATE / f"climate_{place}.csv"
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    dates = [date.fromisoformat(row["day_utc"]) for row in rows]
    if len(dates) != len(set(dates)) or dates != sorted(dates):
        raise ValueError("Invalid POWER daily keys")
    return {day: row for day, row in zip(dates, rows)}


def weather_metrics(rows, dry_threshold):
    rainfall = [float(row["rain_mm"]) for row in rows]
    if any(v < 0 for v in rainfall):
        raise ValueError("Negative precipitation")
    streak = longest = 0
    for rain in rainfall:
        streak = streak + 1 if rain < dry_threshold else 0
        longest = max(streak, longest)
    return dict(days=len(rows), rain_mm=round(sum(rainfall), 3),
                dry_days=sum(r < dry_threshold for r in rainfall), longest_dry_streak_days=longest,
                mean_tmin_c=round(mean(float(r["t_min_c"]) for r in rows), 3),
                mean_tmax_c=round(mean(float(r["t_max_c"]) for r in rows), 3),
                mean_rh2m_pct=round(mean(float(r["rh2m_pct"]) for r in rows), 3))


def replay(windows, climate, threshold):
    # Weather exposure is independent of the AD filter. Reuse climate rows across soils.
    cases = {(w["municipality_code"], w["crop"], w["group"], w["decendio"])
             for w in windows if w["indicated"]}
    skipped = Counter()
    phases, sample = [], []
    demo = {("3170206", "soy"): 31, ("5107925", "soy"): 28,
            ("3170206", "maize2"): 1, ("5107925", "maize2"): 1}
    for code, crop, group, d in sorted(cases):
        archive = climate[code]
        durations = PHASES[crop][group]
        for year in range(2015, 2025):
            first, last = dates_for_decendio(year, d)
            for offset in range((last - first).days + 1):
                sow = first + offset * DAY
                timeline = [sow + i * DAY for i in range(sum(durations))]
                if not all(t in archive for t in timeline):
                    skipped[f"{code}:{crop}"] += 1
                    continue
                cursor = 0
                for phase, duration in zip(NAMES, durations):
                    days = timeline[cursor:cursor + duration]
                    cursor += duration
                    part = [archive[day] for day in days]
                    info = dict(municipality_code=code, crop=crop, group=group, decendio=d,
                                historical_season=f"{year}/{year+1}", sowing_date=sow, phase=phase,
                                phase_start=days[0], phase_end=days[-1], phase_days=duration)
                    phases.append(dict(info, dry_threshold_mm=threshold, **weather_metrics(part, threshold)))
                    if year == 2023 and group == "II" and demo[code, crop] == d and offset == (last-first).days // 2:
                        for day, row in zip(days, part):
                            sample.append(dict(info, days_after_sowing=(day-sow).days, **{
                                name: value for name, value in row.items() if name != "municipality_code"}))
    return phases, sample, skipped


def write(name, rows):
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=RAW / "recorte-2026-2027.csv")
    parser.add_argument("--dry-threshold-mm", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 < args.dry_threshold_mm <= 10:
        parser.error("--dry-threshold-mm must be greater than 0 and at most 10")
    OUT.mkdir(parents=True, exist_ok=True)
    windows = read_windows(args.source)
    data = {code: climate_index(place) for code, place in PLACES.items()}
    phases, sample, skipped = replay(windows, data, args.dry_threshold_mm)
    write("windows.csv", windows)
    write("phase_replay.csv", phases)
    write("daily_examples.csv", sample)
    write("phase_profiles.csv", [
        dict(crop=crop, group=group, cycle_days=sum(durations),
             **{phase + "_days": length for phase, length in zip(NAMES, durations)},
             source="Embrapa ZARC Soja 06 ADs (2023)" if crop == "soy" else "MAPA 324/326 (2026), item 1.12")
        for crop, groups in PHASES.items() for group, durations in groups.items()
        if any(w["crop"] == crop and w["group"] == group for w in windows)
    ])
    national = RAW / "tabua-2026-2027.csv"
    manifest = dict(zarc_season="2026/27", zarc_resource_id="139e5a60-1f43-4cc8-aeab-a35dbbf816c0",
                    zarc_input=args.source.as_posix(), zarc_sha256=digest(args.source),
                    national_csv_sha256=digest(national) if national.is_file() else None,
                    source_portarias={p.name: digest(p) for p in sorted(Path("data/raw/margins").glob("zarc_*.pdf"))},
                    climate_source="NASA POWER Daily UTC", climate_sha256={
                        p: digest(CLIMATE / f"climate_{p}.csv") for p in PLACES.values()},
                    phase_durations=dict(soy="Embrapa ZARC Soja 06 ADs (2023), retrospective simulation reference",
                                         maize2="MAPA Portarias 324 and 326, section 1.12"),
                    dry_threshold_mm=args.dry_threshold_mm, skipped_incomplete_candidate_dates=skipped,
                    window_rows=len(windows), indicated_windows=sum(w["indicated"] for w in windows),
                    phase_rows=len(phases), example_days=len(sample))
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
