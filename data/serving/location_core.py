"""Assemble the LocationCore v1 packages (Gaiapus-APP packages/contracts LocationCore) for every catalog place.

Usage (network): python -m data.serving.location_core [--out data/serving/location-core]
Writes one <id with ':' -> '_'>.json per place plus manifest.json (sha256 of each file, generator commit).
"""
import argparse
import hashlib
import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from data.models.water_balance import CROPS
from data.serving import location_catalog as lc
from data.serving import location_forecast as lfc
from data.serving import location_ndvi as lnd
from data.serving import location_weather as lw

OUT = Path(__file__).resolve().parent / 'location-core'
PERIOD = (date(2015, 1, 1), date(2024, 12, 31))
CAD = 'AD4'
BOUNDARY_URL = '/publications/location-core-v1/{stem}.geojson'
BOUNDARY_SOURCES = {'US': 'US Census TIGERweb State_County MapServer', 'BR': 'IBGE API de malhas v4'}


def git_commit():
    try:
        return subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def network_fetchers():
    return {'power': lw.fetch_power, 'boundary': lc.boundary, 'ndvi': lnd.ndvi_layer, 'forecast': lfc.fetch_forecast, 'generator': f'gaiapus_nasa@{git_commit()}'}


def _geometry(boundary, bbox):
    from pyproj import Geod
    from shapely.geometry import box, shape
    from shapely.ops import unary_union
    area = unary_union([shape(f['geometry']) for f in boundary['features']])
    c = area.representative_point() if not area.centroid.within(area) else area.centroid
    field_area, _ = Geod(ellps='WGS84').geometry_area_perimeter(box(*bbox))
    return [round(c.x, 5), round(c.y, 5)], [round(v, 5) for v in area.bounds], abs(field_area) / 10_000


def _trim_trailing_missing(payload):
    """Keep the leading run of complete POWER days (near-real-time data lag at the end)."""
    p = payload['properties']['parameter']
    keep = []
    for key in sorted(p['IMERG_PRECTOT']):
        if any(p[name][key] is None or p[name][key] <= lw.FILL for name in p):
            break
        keep.append(key)
    return {**payload, 'properties': {'parameter': {name: {k: p[name][k] for k in keep} for name in p}}}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build_core(place, sources, issued_at, fetch, period=PERIOD):
    stem = lc.file_stem(place['id'])
    boundary = fetch['boundary'](place)
    bbox = place['field']['bbox']
    if not lc.inside(bbox, boundary):
        raise ValueError(f'Field box outside {place["id"]}')
    centroid, extent, area_ha = _geometry(boundary, bbox)
    lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    capacity = CROPS[place['crops'][0]['parameter_set']]['cad_mm'][CAD]
    retrieved = issued_at.isoformat()

    payload = fetch['power'](lon, lat, *period)
    weather = lw.weather_layer(payload)
    rows = weather.get('rows', [])
    water = lw.water_layer(place['crops'], rows, capacity) if rows else lw.unavailable('source_unavailable', lw.WATER_SOURCE)
    seasons = water.get('seasons', [])

    crops = []
    for crop in place['crops']:
        last = [s for s in seasons if s['crop'] == crop['crop']]
        src = sources.get(crop.get('calendar_source'), {})
        crops.append({'crop': crop['crop'], 'season': last[-1]['season'] if last else str(period[1].year),
                      'sowing_date': last[-1]['sowing_date'] if last else None, 'harvest_date': last[-1]['harvest_date'] if last else None,
                      'calendar_source': f"{src['title']} — {src['url']}" if src else None, 'is_hypothesis': True})
    # NDVI window: latest complete season of the primary crop by its calendar (independent of the weather layer).
    window = None
    for year in range(period[1].year, period[0].year - 2, -1):
        sowing, harvest = lc.season_dates(place['crops'][0], year)
        if sowing is None:
            break
        if harvest - timedelta(days=1) <= period[1]:
            window = (sowing.isoformat(), harvest.isoformat()); break
    ndvi = fetch['ndvi'](bbox, boundary, *window) if window else {'status': 'unavailable', 'reason_code': 'crop_calendar_unknown', 'source': lnd.SOURCE}

    # Forecast: spin-up with POWER up to its last complete day (IMERG rain and radiation lag weeks), then the
    # same Open-Meteo model (past_days) up to the day before issue, then 10 forecast days from the issue date.
    issued_on = issued_at.date()
    recent = _trim_trailing_missing(fetch['power'](lon, lat, date(issued_at.year - 1, 1, 1), issued_on - timedelta(days=1)))
    try:
        recent_rows = lw.parse_power(recent)
    except lw.MissingWeather:
        recent_rows = []
    last_power = date.fromisoformat(recent_rows[-1]['date']) if recent_rows else None
    gap = (issued_on - last_power).days - 1 if last_power else None
    in_field = lfc._in_field(place['crops'], issued_on)
    forecast_data = None
    if in_field and gap is not None and gap <= lfc.MAX_PAST_DAYS:
        forecast_data = fetch['forecast'](lon, lat, gap)
    spin_up = list(recent_rows)
    if forecast_data:
        for i in range(gap):
            d = (last_power + timedelta(days=i + 1)).isoformat()
            if forecast_data.get(d) is None:
                spin_up = []; forecast_data = None; break
            spin_up.append({'date': d, **forecast_data[d], 'tmin_c': None, 'tmax_c': None})
    forecast = lfc.forecast_layer(place['crops'], issued_at, spin_up, forecast_data, capacity)
    power_used = None
    if forecast['status'] == 'available':
        sowing = in_field[1]
        parts = []
        if last_power and sowing <= last_power:
            power_used = (sowing, last_power)
            parts.append(f'NASA POWER from {sowing} to {last_power}')
        first_model = max(sowing, last_power + timedelta(days=1)) if last_power else sowing
        if first_model < issued_on:
            parts.append(f'Open-Meteo {lfc.MODEL} past days from {first_model} to {issued_on - timedelta(days=1)} (model output, not observations)')
        forecast['method'] = 'FAO-56 bucket advanced with forecast rain and ET0. Storage spun up from sowing with ' + ', then '.join(parts) + '.'

    inputs = [
        {'source': 'NASA POWER', 'dataset': f'daily point AG {lw.POWER_PARAMS} lat={lat:.5f} lon={lon:.5f} {period[0]}..{period[1]}', 'retrieved_at': retrieved, 'sha256': _sha(payload)},
        {'source': BOUNDARY_SOURCES[place['country']], 'dataset': f'{place["scheme"]} {place["code"]}', 'retrieved_at': retrieved, 'sha256': _sha(boundary)},
        {'source': 'NASA HLS v2 (Microsoft Planetary Computer)', 'dataset': 'hls2-l30, hls2-s30 B04/B05/B8A/Fmask', 'retrieved_at': retrieved, 'sha256': None},
        {'source': 'Open-Meteo', 'dataset': f'forecast API {lfc.MODEL}', 'retrieved_at': retrieved, 'sha256': _sha(forecast_data) if forecast_data else None},
    ]
    if power_used:
        inputs.insert(1, {'source': 'NASA POWER', 'dataset': f'daily point AG (forecast spin-up) {power_used[0]}..{power_used[1]}', 'retrieved_at': retrieved, 'sha256': _sha(recent)})
    return {
        'schema_version': 'location-core.v1',
        'place': {'id': place['id'], 'country': place['country'], 'scheme': place['scheme'], 'code': place['code'], 'name': place['name'],
                  'admin1_code': place['admin1_code'], 'admin1_name': place['admin1_name'], 'legacy_area_id': place['legacy_area_id'],
                  'centroid': centroid, 'bbox': extent, 'boundary_url': BOUNDARY_URL.format(stem=stem)},
        'field': {'polygon': {'type': 'Polygon', 'coordinates': [[[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]]},
                  'area_ha': round(area_ha, 1), 'inside_boundary': True,
                  'rationale': f"Illustrative ~850 m crop window chosen as {place['field']['cropland_share'] * 100:.1f}% cropland in {place['field']['cropland_source']}; the crop grown in the box each season is not verified; not a property boundary."},
        'crops': crops,
        'soil': {'capacity_mm': capacity, 'source': f'{CAD} available-water class (Gaiapus hypothesis, not a soil survey)', 'is_hypothesis': True},
        'layers': {'weather': weather, 'water_balance': water, 'ndvi': ndvi, 'forecast': forecast},
        'provenance': {'generated_at': retrieved, 'generator': fetch['generator'], 'inputs': inputs},
    }


def write_packages(packages, out, generator, issued_at):
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for pkg in packages:
        name = f'{lc.file_stem(pkg["place"]["id"])}.json'
        body = json.dumps(pkg, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        (out / name).write_bytes(body)
        files.append({'id': pkg['place']['id'], 'path': name, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)})
    manifest = {'schema_version': 'location-core.v1', 'generator': generator, 'generated_at': issued_at.isoformat(), 'files': files}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--only', help='one location id')
    args = parser.parse_args()
    catalog = json.loads(lc.CATALOG.read_text(encoding='utf-8'))
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    fetch = network_fetchers()
    packages = []
    for place in lc.load_catalog():
        if args.only and place['id'] != args.only:
            continue
        pkg = build_core(place, catalog['sources'], issued_at, fetch)
        print(place['id'], {k: v['status'] + ('' if v['status'] == 'available' else f" ({v['reason_code']})") for k, v in pkg['layers'].items()}, flush=True)
        packages.append(pkg)
    manifest = write_packages(packages, args.out, fetch['generator'], issued_at)
    for f in manifest['files']:
        print(f['path'], f['bytes'], 'bytes')


if __name__ == '__main__':
    main()
