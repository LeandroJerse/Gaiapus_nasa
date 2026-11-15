"""Extract one cell from daily Final V07B files mirrored by NSF NCAR GDEX."""

import argparse
import csv
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from data.ingest.run import IMERG_DOI, sha256, write_json

BASE_URL = "https://tds.gdex.ucar.edu/thredds/dodsC/files/d734000/gpm_3imergdf_v07"
FILE_URL = "https://data.gdex.ucar.edu/d734000/gpm_3imergdf_v07"
EPOCH = date(1980, 1, 6)
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def filename(day: date) -> str:
    return f"3B-DAY.MS.MRG.3IMERG.{day:%Y%m%d}-S000000-E235959.V07B.nc4"


def make_url(day: date, lon_index: int, lat_index: int) -> str:
    query = f"precipitation%5B0%5D%5B{lon_index}%5D%5B{lat_index}%5D"
    return f"{BASE_URL}/{day:%Y/%m}/{filename(day)}.ascii?{query}"


def parse_subset(raw: bytes, day: date, cell_lat: float, cell_lon: float) -> float | None:
    content = raw.decode("utf-8")
    values = []
    for variable, shape, prefix in (
        ("precipitation", r"\[1\]\[1\]\[1\]", r"\[0\]\[0\],\s*"),
        ("time", r"\[1\]", ""), ("lon", r"\[1\]", ""), ("lat", r"\[1\]", ""),
    ):
        match = re.search(rf"(?m)^precipitation\.{variable}{shape}\s*\n{prefix}({NUMBER})\s*$", content.replace("\r\n", "\n"))
        if not match:
            raise ValueError(f"{day}: malformed OPeNDAP subset ({variable})")
        values.append(float(match[1]))
    rain, timestamp, lon, lat = values
    if timestamp != (day - EPOCH).days or abs(lon - cell_lon) > 0.0001 or abs(lat - cell_lat) > 0.0001:
        raise ValueError(f"{day}: unexpected coordinates or UTC date")
    if abs(rain + 9999.9) < 0.01:
        return None
    if not math.isfinite(rain) or rain < 0:
        raise ValueError(f"{day}: invalid precipitation")
    return rain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--lat", type=float, default=-18.92)
    parser.add_argument("--lon", type=float, default=-48.28)
    parser.add_argument("--out", type=Path, default=Path("data/raw/imerg/reference"))
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=6)
    parser.add_argument("--netcdf-dir", type=Path, help="Local originals from NCAR GDEX, when available")
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args()
    if args.start > args.end or not -90 <= args.lat < 90 or not -180 <= args.lon < 180:
        parser.error("Invalid dates or point outside IMERG grid")
    lat_idx = math.floor((args.lat + 90) * 10)
    lon_idx = math.floor((args.lon + 180) * 10)
    cell_lat, cell_lon = round(lat_idx / 10 - 89.95, 2), round(lon_idx / 10 - 179.95, 2)
    cache = args.out / f"cell-{lon_idx}-{lat_idx}"
    cache.mkdir(parents=True, exist_ok=True)
    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
    local = {}
    if args.netcdf_dir:
        from netCDF4 import Dataset

        for day in days:
            path = args.netcdf_dir / filename(day)
            if (cache / f"{day}.ascii").is_file() or not path.is_file():
                continue
            with Dataset(path) as dataset:
                grid = dataset["precipitation"]
                if (grid.units != "mm/day" or grid.dimensions != ("time", "lon", "lat")
                    or dataset["time"].units != "days since 1980-01-06 00:00:00Z"
                    or float(dataset["time"][0]) != (day - EPOCH).days
                    or abs(float(dataset["lon"][lon_idx]) - cell_lon) > 0.0001
                    or abs(float(dataset["lat"][lat_idx]) - cell_lat) > 0.0001):
                    raise ValueError(f"{path}: unexpected NetCDF units, grid or time")
                value = grid[0, lon_idx, lat_idx]
                rain = None if getattr(value, "mask", False) else float(value)
                if rain is not None and (not math.isfinite(rain) or rain < 0):
                    raise ValueError(f"{day}: invalid precipitation")
            local[day] = {"day_utc": str(day), "rain_mm": rain,
                          "url": f"{FILE_URL}/{day:%Y/%m}/{filename(day)}",
                          "sha256": sha256(path.read_bytes())}

    def extract(day: date) -> dict:
        url = make_url(day, lon_idx, lat_idx)
        path = cache / f"{day}.ascii"
        try:
            if day in local:
                return local[day]
            if path.is_file():
                raw = path.read_bytes()
                rain = parse_subset(raw, day, cell_lat, cell_lon)
            else:
                if args.cache_only:
                    return {"day_utc": str(day), "rain_mm": None, "url": url, "error": "Subset not cached"}
                for attempt in range(3):
                    try:
                        with urlopen(url, timeout=18) as response:
                            raw = response.read()
                        break
                    except (URLError, TimeoutError) as error:
                        if attempt == 2 or isinstance(error, HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                            raise
                        time.sleep(1 + attempt * 2)
                rain = parse_subset(raw, day, cell_lat, cell_lon)
                path.write_bytes(raw)
            return {"day_utc": day.isoformat(), "rain_mm": rain, "url": url, "sha256": sha256(raw)}
        except (URLError, TimeoutError, ValueError) as error:
            return {"day_utc": day.isoformat(), "rain_mm": None, "url": url, "error": str(error)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        data = list(pool.map(extract, days))
    csv_path = args.out / "imerg_final_v07.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["day_utc", "rain_mm"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    write_json(args.out / "imerg_final_v07.manifest.json", {
        "source": "NSF NCAR GDEX d734000 THREDDS OPeNDAP mirror",
        "source_catalog": "https://gdex.ucar.edu/datasets/d734000/dataaccess/",
        "collection": "GPM_3IMERGDF", "doi": IMERG_DOI, "version": "07", "file_version": "V07B",
        "run": "Final", "time_standard": "UTC", "units": "mm/day",
        "cell_lat": cell_lat, "cell_lon": cell_lon,
        "start": str(args.start), "end": str(args.end), "sha256": sha256(csv_path.read_bytes()),
        "granules": data,
    })
    matched = sum(row["rain_mm"] is not None for row in data)
    print(f"IMERG Final V07B: {matched}/{len(data)} days at {cell_lat}, {cell_lon}")
    return 0 if matched == len(data) else 2


if __name__ == "__main__":
    raise SystemExit(main())
