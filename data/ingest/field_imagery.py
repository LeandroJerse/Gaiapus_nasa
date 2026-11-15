"""Build the Gaiapus demonstration image and linked HLS/POWER observations."""

import argparse
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from functools import lru_cache
from urllib.parse import urlsplit

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.features import geometry_mask, rasterize
from rasterio.windows import Window, from_bounds
from rasterio.warp import transform_bounds, transform_geom

from data.ingest.weather import normalize_power

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
# This illustrative crop window lies west of the city. It is not a property boundary.
FIELD_BOUNDS = (-48.413, -18.907, -48.405, -18.900)
START, END = "2024-04-01", "2024-07-31"
COLLECTIONS = {"hls2-l30": "B05", "hls2-s30": "B8A"}
WEATHER = Path("data/raw/power/uberlandia-recorte-2024.json")
MIN_COVERAGE = 0.65


def fetch_json(url, **kwargs):
    response = requests.get(url, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def catalog(bounds=None, start=None, end=None):
    # Defaults are the published recorte; the location core passes its own box and season.
    bounds, start, end = bounds or FIELD_BOUNDS, start or START, end or END
    query = {
        "collections": ",".join(COLLECTIONS), "bbox": ",".join(map(str, bounds)),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "limit": 100,
    }
    url = STAC + "/search"
    seen = set()
    while url and url not in seen:
        seen.add(url)
        result = fetch_json(url, params=query)
        yield from result["features"]
        url = next((link["href"] for link in result["links"] if link["rel"] == "next"), None)
        query = None


def ring(bounds):
    west, south, east, north = bounds
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


@lru_cache(maxsize=8)
def container_token(account, container):
    return fetch_json(f"{TOKEN}/{account}/{container}")["token"].lstrip("?")


def blob_parts(href):
    url = urlsplit(href)
    if url.scheme != "https" or not url.hostname.endswith(".blob.core.windows.net"):
        raise ValueError("Unexpected HLS asset host")
    return url.hostname.split(".")[0], url.path.split("/")[1]


def band(scene, key, bounds):
    asset = scene["assets"][key]
    href = asset["href"]
    signed = href + "?" + container_token(*blob_parts(href))
    with rasterio.open(signed) as source:
        search = from_bounds(*transform_bounds("EPSG:4326", source.crs, *bounds), source.transform)
        x, y = np.floor([search.col_off, search.row_off]).astype(int)
        right, bottom = np.ceil([search.col_off + search.width, search.row_off + search.height]).astype(int)
        window = Window(x, y, right - x, bottom - y)
        data = source.read(1, window=window, boundless=True, masked=True)
        transform = source.window_transform(window)
        return data, transform, source.crs


def scene_id(scene):
    return scene["id"]


def summarize(scene, shape, bounds=None):
    acquired = scene["properties"]["datetime"]
    record = {"date": acquired[:10], "acquired_at": acquired, "scene_id": scene_id(scene),
              "collection": scene["collection"], "ndvi": None, "valid_fraction": None}
    # An asset error is reported separately from a cloudy observation.
    try:
        bounds = bounds or FIELD_BOUNDS
        red, transform, crs = band(scene, "B04", bounds)
        nir, nt, nc = band(scene, COLLECTIONS[scene["collection"]], bounds)
        mask, mt, mc = band(scene, "Fmask", bounds)
        if transform != nt or transform != mt or crs != nc or crs != mc:
            raise ValueError("Misaligned HLS grids")
        in_field = geometry_mask([transform_geom("EPSG:4326", crs, shape)], red.shape, transform, invert=True)
        denominator = nir.data.astype(float) + red.data.astype(float)
        valid = (in_field & ~(np.ma.getmaskarray(red) | np.ma.getmaskarray(nir) | np.ma.getmaskarray(mask))
                 & ((mask.data.astype(np.uint8) & 63) == 0) & (red.data >= 0)
                 & (nir.data >= 0) & (denominator > 0))
        count = int(in_field.sum())
        usable = int(valid.sum())
        fraction = usable / count if count else 0
        record.update(valid_fraction=round(fraction, 4), valid_pixels=usable, field_pixels=count)
        if usable and fraction >= MIN_COVERAGE:
            values = (nir.data[valid].astype(float) - red.data[valid]) / denominator[valid]
            record["ndvi"] = round(float(np.median(values)), 4)
    except (requests.RequestException, rasterio.errors.RasterioError, ValueError, KeyError) as error:
        # Do not serialize signed URLs that may appear in exception messages.
        record["error"] = type(error).__name__
    return record


def scene_preview(scene, destination):
    from rasterio.transform import array_bounds, from_bounds as affine_from_bounds
    from rasterio.warp import Resampling, reproject

    west, south, east, north = FIELD_BOUNDS
    extent = west - .009, south - .007, east + .009, north + .007
    side = 560
    output_transform = affine_from_bounds(*extent, side, side)
    rgb = []
    for name in ("B04", "B03", "B02"):
        values, affine, crs = band(scene, name, extent)
        image = np.full((side, side), np.nan, dtype=np.float32)
        reproject(values.astype(np.float32).filled(np.nan), image, src_transform=affine, src_crs=crs,
                  src_nodata=np.nan, dst_transform=output_transform, dst_crs="EPSG:4326",
                  dst_nodata=np.nan, resampling=Resampling.bilinear)
        rgb.append(np.clip(np.nan_to_num(image) / 3200.0, 0, 1) ** (1 / 1.3))
    Image.fromarray((np.stack(rgb, axis=-1) * 255).astype(np.uint8)).save(destination)
    w, s, e, n = array_bounds(side, side, output_transform)
    return [[w, n], [e, n], [e, s], [w, s]]


def geometry_bounds(value):
    points = []

    def visit(node):
        if isinstance(node[0], (int, float)):
            points.append(node)
        else:
            for child in node:
                visit(child)

    for item in value["features"]:
        visit(item["geometry"]["coordinates"])
    x, y = zip(*(point[:2] for point in points))
    return [[min(x), min(y)], [max(x), max(y)]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("gaiapus-mvp/data"))
    parser.add_argument("--bbox", type=float, nargs=4, default=FIELD_BOUNDS)
    parser.add_argument("--start", type=date.fromisoformat, default=date.fromisoformat(START))
    parser.add_argument("--end", type=date.fromisoformat, default=date.fromisoformat(END))
    parser.add_argument("--weather", type=Path, default=WEATHER)
    parser.add_argument("--uf-file", default="MG.geojson")
    args = parser.parse_args()
    build(args)


def build(args):
    global FIELD_BOUNDS, START, END
    FIELD_BOUNDS = tuple(args.bbox)
    START, END = str(args.start), str(args.end)
    w, s, e, n = FIELD_BOUNDS
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90) or args.start > args.end:
        raise ValueError("Invalid AOI or date interval")
    rain_point = [(w + e) / 2, (s + n) / 2]
    args.out.mkdir(parents=True, exist_ok=True)
    layers = {key: json.loads((args.out / name).read_bytes())
              for key, name in (("BR", "BR.geojson"), ("MG", args.uf_file), ("city", "city.geojson"))}
    bounds = {key: geometry_bounds(layer) for key, layer in layers.items()}
    # Gate the entire demonstration window against the administrative mask.
    from rasterio.transform import from_bounds as affine_from_bounds
    covered = rasterize([(feature["geometry"], 1) for feature in layers["city"]["features"]],
                        out_shape=(32, 32), transform=affine_from_bounds(w, s, e, n, 32, 32))
    if not covered.all():
        raise ValueError("AOI crosses or falls outside the municipality")
    shape = {"type": "Polygon", "coordinates": [ring(FIELD_BOUNDS)]}
    bounds["field"] = [list(FIELD_BOUNDS[:2]), list(FIELD_BOUNDS[2:])]
    # Reject duplicate STAC items and preserve every acquisition, including cloudy observations.
    scenes = {scene_id(scene): scene for scene in catalog()}
    accounts = {blob_parts(scene["assets"]["B04"]["href"]) for scene in scenes.values()}
    for account in accounts:
        container_token(*account)
    with ThreadPoolExecutor(max_workers=4) as pool:
        observations = list(pool.map(lambda s: summarize(s, shape), scenes.values()))
    observations.sort(key=lambda row: (row["date"], row["scene_id"]))
    good = [row for row in observations if row["ndvi"] is not None]
    if len({row["date"] for row in good}) < 3:
        raise RuntimeError(f"Too few HLS observations: {len(good)} accepted of {len(observations)}")
    selected = max(good, key=lambda row: (row["valid_fraction"], row["date"]))
    raw = args.weather.read_bytes()
    payload = json.loads(raw)
    coordinates = payload["geometry"]["coordinates"]
    # The POWER response displays the input coordinates rounded to three decimals.
    if len(coordinates) < 2 or any(abs(a - b) > 0.00051 for a, b in zip(coordinates[:2], rain_point)):
        raise ValueError("Unexpected POWER coordinates")
    weather = {row["day_utc"]: row["rain_mm"] for row in normalize_power(
        payload, date.fromisoformat(START) - timedelta(days=7), date.fromisoformat(END)
    )}
    if None in weather.values():
        raise ValueError("Missing rain in demonstration window or its lead-in")
    corners = scene_preview(scenes[selected["scene_id"]], args.out / "scene.png")
    for row in observations:
        day = date.fromisoformat(row["date"])
        past = [weather.get(str(day - timedelta(days=i))) for i in range(1, 8)]
        row["rain_7d_mm"] = round(sum(past), 2) if None not in past else None
    output = {
        "bounds": bounds, "field": {"geojson": shape, "label": "Recorte demonstrativo rural"},
        "image": {"url": "./data/scene.png", "corners": corners, "date": selected["date"],
                  "scene_id": selected["scene_id"], "collection": selected["collection"]},
        "observations": observations,
        "rainfall": [{"date": day, "rain_mm": rain} for day, rain in weather.items() if START <= day <= END],
        "sources": {"stac": STAC, "imagery": "NASA HLS v2 / Microsoft Planetary Computer",
                    "rain": "NASA POWER / IMERG_PRECTOT", "rain_query_point": rain_point,
                    "rain_sha256": hashlib.sha256(raw).hexdigest(),
                    "rain_validation": "Integridade, unidade e dias UTC verificados; sem calibração local nem pareamento com granules nesta célula.",
                    "boundary": "IBGE API Malhas v4",
                    "ndvi_min_fraction": MIN_COVERAGE,
                    "weather_scope": "Estimativa da célula IMERG de 0,1° associada ao ponto de consulta (centro do recorte)."},
        "validation": {"status": "validated", "min_valid_fraction": MIN_COVERAGE,
                       "accepted_scenes": len(good), "total_scenes": len(observations),
                       "valid_dates": len({row["date"] for row in good}), "scope": "demo_product_quality"},
    }
    (args.out / "field.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False),
                                         encoding="utf-8")
    print(f"{len(good)}/{len(observations)} HLS observations; {len(output['rainfall'])} rain days; {selected['scene_id']}")


if __name__ == "__main__":
    main()
