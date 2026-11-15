"""Cache NASA POWER dates and validate them against a separate daily reference."""

import argparse
import calendar
import csv
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from data.ingest.weather import normalize_power, compare_rain

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMETERS = "T2M_MIN,T2M_MAX,ALLSKY_SFC_SW_DWN,IMERG_PRECTOT"
IMERG_DOI = "10.5067/GPM/IMERGDF/DAY/07"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_reference(path: Path) -> dict[str, float | None]:
    values = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != ["day_utc", "rain_mm"]:
            raise ValueError("Reference CSV header must be day_utc,rain_mm")
        for row in reader:
            day = date.fromisoformat(row["day_utc"]).isoformat()
            if day in values:
                raise ValueError(f"Duplicate reference day: {day}")
            values[day] = float(row["rain_mm"]) if row["rain_mm"].strip() else None
    return values


def write_json(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2016, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 12, 31))
    parser.add_argument("--lat", type=float, default=-18.92)
    parser.add_argument("--lon", type=float, default=-48.28)
    parser.add_argument("--raw", type=Path, default=Path("data/raw/power/uberlandia-2016-2024.json"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("data/processed/uberlandia-2016-2024"))
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-manifest", type=Path)
    parser.add_argument("--monthly-reference", type=Path, help="IMERG GPM_3IMERGM V07B monthly totals")
    parser.add_argument("--tolerance-mm", type=float, default=0.02)
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must precede or equal --end")
    if not -90 <= args.lat <= 90 or not -180 <= args.lon <= 180:
        parser.error("Invalid latitude or longitude")
    if bool(args.reference) != bool(args.reference_manifest):
        parser.error("--reference and --reference-manifest must be supplied together")
    query = urlencode({
        "parameters": POWER_PARAMETERS, "community": "AG", "longitude": args.lon,
        "latitude": args.lat, "start": args.start.strftime("%Y%m%d"),
        "end": args.end.strftime("%Y%m%d"), "format": "JSON", "time-standard": "UTC",
    })
    url = f"{POWER_URL}?{query}"
    if args.download:
        with urlopen(url, timeout=90) as response:
            raw = response.read()
    else:
        raw = args.raw.read_bytes()
    payload = json.loads(raw)
    coordinates = payload["geometry"]["coordinates"]
    if abs(coordinates[0] - args.lon) > 0.0001 or abs(coordinates[1] - args.lat) > 0.0001:
        raise ValueError("POWER response point does not match requested longitude/latitude")
    header = payload["header"]
    if (header["start"], header["end"]) != (args.start.strftime("%Y%m%d"), args.end.strftime("%Y%m%d")):
        raise ValueError("POWER response does not match requested date interval")
    rows = normalize_power(payload, args.start, args.end)
    if args.download:
        args.raw.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.raw.with_suffix(".part")
        temporary.write_bytes(raw)
        temporary.replace(args.raw)

    reference = {}
    ref_manifest = None
    if args.reference:
        if args.reference.resolve() == args.raw.resolve():
            raise ValueError("Reference must be a separate artifact")
        ref_manifest = json.loads(args.reference_manifest.read_text(encoding="utf-8"))
        if (ref_manifest.get("doi"), ref_manifest.get("run"), ref_manifest.get("version"),
            ref_manifest.get("time_standard"), ref_manifest.get("units")) != (
                IMERG_DOI, "Final", "07", "UTC", "mm/day"):
            raise ValueError("Reference manifest must identify daily IMERG Final 07 UTC mm/day")
        if ref_manifest.get("sha256") != sha256(args.reference.read_bytes()):
            raise ValueError("Reference CSV checksum does not match its manifest")
        lat, lon = ref_manifest["cell_lat"], ref_manifest["cell_lon"]
        if abs(lat - args.lat) > 0.050001 or abs(lon - args.lon) > 0.050001:
            raise ValueError("Reference cell does not contain the POWER query point")
        reference = load_reference(args.reference)
    reference_rows = rows
    if ref_manifest:
        start = date.fromisoformat(ref_manifest["start"])
        end = date.fromisoformat(ref_manifest["end"])
        if not args.start <= start <= end <= args.end:
            raise ValueError("Reference period must be inside the POWER interval")
        reference_rows = [row for row in rows if start.isoformat() <= row["day_utc"] <= end.isoformat()]
        if set(reference) - {row["day_utc"] for row in reference_rows}:
            raise ValueError("Reference CSV has dates outside its declared period")
    report = compare_rain(reference_rows, reference, args.tolerance_mm)
    report["comparison_start"] = reference_rows[0]["day_utc"]
    report["comparison_end"] = reference_rows[-1]["day_utc"]
    report["expected_days"] = len(reference_rows)
    report["history_days"] = len(rows)
    report["tolerance_mm"] = args.tolerance_mm
    report["reference"] = ref_manifest
    if args.monthly_reference:
        monthly_raw = args.monthly_reference.read_bytes()
        monthly = {}
        for entry in json.loads(monthly_raw):
            month = entry["month"]
            if month in monthly:
                raise ValueError(f"Duplicate monthly reference: {month}")
            value = entry["reference_monthly_mm"]
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid monthly reference precipitation: {month}")
            monthly[month] = value
        power_by_month = {}
        for row in rows:
            power_by_month.setdefault(row["day_utc"][:7], []).append(row["rain_mm"])
        report["monthly_product_reference"] = {
            "collection": "GPM_3IMERGM",
            "doi": "10.5067/GPM/IMERG/3B-MONTH/07",
            "file_version": "V07B",
            "source": "NSF NCAR GDEX d736000",
            "path": args.monthly_reference.as_posix(),
            "sha256": sha256(monthly_raw),
            "conversion": "native mean rate (mm/hr) × UTC hours in month",
        }
        for month, metrics in report["monthly"].items():
            year, number = map(int, month.split("-"))
            expected = calendar.monthrange(year, number)[1]
            values = power_by_month[month]
            total = math.fsum(values) if len(values) == expected and None not in values else None
            reference_total = monthly.get(month)
            metrics.update(
                power_full_month_mm=total,
                imerg_monthly_product_mm=reference_total,
                monthly_product_difference_mm=(
                    total - reference_total if total is not None and reference_total is not None else None
                ),
                monthly_product_missing=reference_total is None,
            )
        deltas = [
            metrics["monthly_product_difference_mm"]
            for metrics in report["monthly"].values()
            if metrics["monthly_product_difference_mm"] is not None
        ]
        report["monthly_product_summary"] = {
            "expected_months": len(report["monthly"]),
            "paired_months": len(deltas),
            "missing_reference_months": sum(
                metrics["monthly_product_missing"] for metrics in report["monthly"].values()
            ),
            "mae_mm": math.fsum(map(abs, deltas)) / len(deltas) if deltas else None,
            "bias_mm": math.fsum(deltas) / len(deltas) if deltas else None,
            "max_abs_mm": max(map(abs, deltas)) if deltas else None,
        }
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "daily_weather.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([
        ("day_utc", pa.date32()), ("rain_mm", pa.float64()), ("t_min_c", pa.float64()),
        ("t_max_c", pa.float64()), ("radiation_mj_m2", pa.float64()),
    ])
    parquet_rows = [{**row, "day_utc": date.fromisoformat(row["day_utc"])} for row in rows]
    pq.write_table(pa.Table.from_pylist(parquet_rows, schema=schema), args.out / "daily_weather.parquet")
    write_json(args.out / "provenance.json", {
        "source": "NASA POWER Daily AG",
        "request_url": url,
        "raw_path": args.raw.as_posix(), "raw_sha256": sha256(raw),
        "processed_at_utc": datetime.now(timezone.utc).isoformat(),
        "header": header, "parameters": payload["parameters"], "coordinates": coordinates,
        "rain_run": (
            "unverified" if report["status"] != "complete_match"
            else "consistent_with_final_v07" if len(reference_rows) == len(rows)
            else "consistent_with_final_v07_in_sample_only"
        ),
        "expected_days": len(rows),
        "missing_by_field": {field: sum(row[field] is None for row in rows) for field in rows[0] if field != "day_utc"},
    })
    write_json(args.out / "rain_validation.json", report)
    with (args.out / "rain_validation_monthly.csv").open("w", encoding="utf-8", newline="") as file:
        monthly_rows = [{"month": month, **metrics} for month, metrics in report["monthly"].items()]
        for row in monthly_rows:
            for field in ("missing_power_dates", "missing_reference_dates"):
                row[field] = ";".join(row[field])
        writer = csv.DictWriter(file, fieldnames=list(monthly_rows[0]))
        writer.writeheader()
        writer.writerows(monthly_rows)
    print(f'{len(rows)} days; rain validation: {report["status"]}; paired days: {report["matched_days"]}')
    return 0 if not args.reference or report["status"] == "complete_match" else 2


if __name__ == "__main__":
    raise SystemExit(main())
