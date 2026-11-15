"""Location catalog for the location core: ids, official boundaries, field boxes and crop calendars.

Ids are `<ISO 3166-1 alpha-2>:<scheme>:<code>`, codes kept as strings (IBGE 7 digits, FIPS county 5 digits
with leading zeros). Boundaries come from official services and are stored under
data/serving/location-core/boundaries so tests and the API never depend on the network.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import requests

from data.models.water_balance import CROPS

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / 'locations.v1.json'
BOUNDARIES = ROOT / 'location-core' / 'boundaries'
ID_RE = re.compile(r'^(BR):(ibge):([0-9]{7})$|^(US):(fips):([0-9]{5})$')
TIGER = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer'
IBGE = 'https://servicodados.ibge.gov.br/api/v4/malhas/municipios'


def parse_id(location_id):
    match = ID_RE.match(location_id or '')
    if not match:
        raise ValueError(f'Malformed location id: {location_id!r}')
    groups = [g for g in match.groups() if g is not None]
    return tuple(groups)


def file_stem(location_id):
    parse_id(location_id)
    return location_id.replace(':', '_')


def load_catalog(path=CATALOG):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    places, seen = [], set()
    for entry in data['places']:
        country, scheme, code = parse_id(entry['id'])
        if entry['id'] in seen:
            raise ValueError(f'Duplicate location {entry["id"]}')
        seen.add(entry['id'])
        for crop in entry['crops']:
            if crop['parameter_set'] not in CROPS:
                raise ValueError(f'Unknown parameter set {crop["parameter_set"]}')
        places.append({**entry, 'country': country, 'scheme': scheme, 'code': code})
    return places


def calendar_reason(crop):
    return None if crop.get('sowing_month_day') else 'crop_calendar_unknown'


def season_dates(crop, sowing_year):
    """(sowing, harvest) for a crop sown in `sowing_year`; harvest = sowing + FAO-56 cycle. (None, None) without a calendar."""
    if calendar_reason(crop):
        return None, None
    sowing = date(sowing_year, *crop['sowing_month_day'])
    return sowing, sowing + timedelta(days=sum(CROPS[crop['parameter_set']]['phase_days']))


def season_label(crop, sowing):
    """Agricultural-year label: crops sown from July onwards, and Brazilian second crops, span two years."""
    if crop['crop'] == 'maize2':
        return f'{sowing.year - 1}/{str(sowing.year)[2:]}'
    return f'{sowing.year}/{str(sowing.year + 1)[2:]}' if sowing.month >= 7 else str(sowing.year)


def _fetch_boundary(place):
    if place['country'] == 'US':
        r = requests.get(f'{TIGER}/1/query', params={'where': f"GEOID='{place['code']}'", 'outFields': 'GEOID,NAME', 'outSR': 4326, 'f': 'geojson'}, timeout=120)
    else:
        r = requests.get(f'{IBGE}/{place["code"]}', params={'formato': 'application/vnd.geo+json', 'qualidade': 'intermediaria'}, timeout=120)
    r.raise_for_status()
    geo = r.json()
    if not geo.get('features'):
        raise LookupError(f'No boundary for {place["id"]}')
    return geo


def boundary(place, refresh=False):
    """Official boundary GeoJSON (FeatureCollection), cached in the repository."""
    path = BOUNDARIES / f'{file_stem(place["id"])}.geojson'
    if refresh or not path.exists():
        BOUNDARIES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_fetch_boundary(place), separators=(',', ':')), encoding='utf-8')
    return json.loads(path.read_text(encoding='utf-8'))


def inside(bbox, geojson):
    from shapely.geometry import box, shape
    from shapely.ops import unary_union
    area = unary_union([shape(f['geometry']) for f in geojson['features']])
    return area.contains(box(*bbox))
