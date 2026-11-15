"""Compare each demonstration bundle with the daily V07B reference for its query point."""

import argparse
import csv
import json
import math
from pathlib import Path

from data.ingest.run import sha256, write_json
from data.ingest.weather import compare_rain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Attach the audit summary to the local demo bundles")
    args = parser.parse_args()
    manifest = Path("gaiapus-mvp/data/regions.json")
    results = []
    for entry in json.loads(manifest.read_text(encoding="utf-8")):
        ident = entry["id"]
        path = Path("gaiapus-mvp", entry["directory"], "field.json")
        package = json.loads(path.read_bytes())
        folder = Path("data/raw/imerg/regions", ident)
        original = folder / "imerg_final_v07.csv"
        provenance = json.loads((folder / "imerg_final_v07.manifest.json").read_bytes())
        rainfall = [{"day_utc": row["date"], "rain_mm": row["rain_mm"]} for row in package["rainfall"]]
        lon, lat = package["sources"]["rain_query_point"]
        cell_lon = round(math.floor((lon + 180) * 10) / 10 - 179.95, 2)
        cell_lat = round(math.floor((lat + 90) * 10) / 10 - 89.95, 2)
        expected = {"cell_lat": cell_lat, "cell_lon": cell_lon, "file_version": "V07B", "run": "Final",
                    "units": "mm/day", "time_standard": "UTC", "sha256": sha256(original.read_bytes()),
                    "start": rainfall[0]["day_utc"], "end": rainfall[-1]["day_utc"]}
        if any(provenance.get(key) != value for key, value in expected.items()):
            raise ValueError(f"{ident}: reference metadata does not match the demo rainfall")
        with original.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        reference = {row["day_utc"]: float(row["rain_mm"]) if row["rain_mm"] else None for row in rows}
        if len(rows) != len(reference) or set(reference) != {row["day_utc"] for row in rainfall}:
            raise ValueError(f"{ident}: duplicate or unexpected reference dates")
        report = compare_rain(rainfall, reference)
        summary = {
            "region": ident, "start": expected["start"], "end": expected["end"],
            "expected_days": len(rainfall), "product": "IMERG daily Final V07B",
            "cell_center": [cell_lon, cell_lat], "tolerance_mm": 0.02,
            "ground_check": "not_checked", "reference_sha256": expected["sha256"],
            **{key: value for key, value in report.items() if key != "monthly"},
        }
        if args.publish:
            package["validation"]["rain_crosscheck"] = summary
            package["reference_rainfall"] = (
                [{"date": row["day_utc"], "rain_mm": reference[row["day_utc"]]} for row in rainfall]
                if report["matched_days"] == len(rainfall) else []
            )
            package["sources"]["rain_validation"] = "Comparada com a distribuição diária IMERG Final V07B; não calibrada em campo."
            package["sources"]["weather_scope"] = "Estimativa da célula IMERG de 0,1° associada ao ponto de consulta (centro do recorte)."
            write_json(path, package)
        results.append(summary)
        print(f"{ident}: {report['status']}, {report['matched_days']}/{len(rainfall)} days, "
              f"{report['mismatched_days']} outliers, MAE {report['mae_mm']}")
    write_json(Path("data/processed/demo-rain-audit.json"), results)
    return int(any(item["status"] != "complete_match" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
