"""Daily weather (NASA POWER, IMERG rain) and per-season FAO-56 water balance for the location core.

The water balance runs one complete crop cycle per season with `water_balance.simulate`; seasons not
fully covered by the weather are dropped, never truncated. No rows outside the crop season.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from data.models.water_balance import et0_daily, simulate
from data.serving.location_catalog import calendar_reason, season_dates, season_label

POWER = 'https://power.larc.nasa.gov/api/temporal/daily/point'
POWER_PARAMS = 'IMERG_PRECTOT,T2M_MIN,T2M_MAX,ALLSKY_SFC_SW_DWN,RH2M,WS2M,T2MDEW,PS'  # same set as cohort_v5
WEATHER_SOURCE = 'NASA POWER daily point, community AG (rain: IMERG_PRECTOT); ET0 FAO-56 Penman-Monteith'
WATER_SOURCE = 'FAO-56 single-layer bucket (Gaiapus data/models/water_balance.simulate)'
# Hypotheses that the parameter-set name alone does not show.
PARAMETER_NOTES = {'wheat_winter_us': 'wheat_winter_us: FAO-56 Idaho winter-wheat stage proportions scaled to a 260-day Kansas cycle (hypothesis)'}
FILL = -999.0
CACHE = Path('data/cache/power')


class MissingWeather(ValueError):
    pass


def fetch_power(lon, lat, first, last, cache=CACHE):
    """POWER daily point JSON, cached per point and period (the cache is not committed)."""
    path = cache / f'{lat:.4f}_{lon:.4f}_{first:%Y%m%d}_{last:%Y%m%d}.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    params = {'parameters': POWER_PARAMS, 'community': 'AG', 'longitude': lon, 'latitude': lat,
              'start': f'{first:%Y%m%d}', 'end': f'{last:%Y%m%d}', 'format': 'JSON', 'time-standard': 'UTC'}
    r = requests.get(POWER, params=params, timeout=300)
    r.raise_for_status()
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding='utf-8')
    return r.json()


def parse_power(payload):
    """Rows {date, rain_mm, et0_mm, tmin_c, tmax_c}; raises MissingWeather on any fill value."""
    lon, lat, elevation = payload['geometry']['coordinates']
    p = payload['properties']['parameter']
    rows = []
    for key in sorted(p['IMERG_PRECTOT']):
        v = {name: p[name][key] for name in ('IMERG_PRECTOT', 'T2M_MIN', 'T2M_MAX', 'ALLSKY_SFC_SW_DWN', 'WS2M', 'T2MDEW', 'PS')}
        if any(x is None or x <= FILL for x in v.values()):
            raise MissingWeather(f'Missing POWER value on {key}')
        day = datetime.strptime(key, '%Y%m%d').date()
        et0 = et0_daily(day, lat, elevation, v['T2M_MIN'], v['T2M_MAX'], v['ALLSKY_SFC_SW_DWN'], v['WS2M'], v['PS'], dewpoint=v['T2MDEW'])['et0_mm']
        rows.append({'date': day.isoformat(), 'rain_mm': v['IMERG_PRECTOT'], 'et0_mm': et0, 'tmin_c': v['T2M_MIN'], 'tmax_c': v['T2M_MAX']})
    return rows


def unavailable(reason, source):
    return {'status': 'unavailable', 'reason_code': reason, 'source': source}


def weather_layer(payload):
    try:
        rows = parse_power(payload)
    except MissingWeather:
        return unavailable('source_unavailable', WEATHER_SOURCE)
    if not rows:
        return unavailable('source_unavailable', WEATHER_SOURCE)
    return {'status': 'available', 'source': WEATHER_SOURCE, 'method': 'FAO-56 Penman-Monteith (daily)',
            'period': {'start': rows[0]['date'], 'end': rows[-1]['date']}, 'rows': rows}


def _season(crop, sowing, harvest, by_date, capacity, initial_fraction):
    days = (harvest - sowing).days
    forcing = []
    for i in range(days):
        row = by_date.get((sowing + timedelta(days=i)).isoformat())
        if row is None:
            return None
        forcing.append({'day_utc': row['date'], 'rain_mm': row['rain_mm'], 'et0_mm': row['et0_mm']})
    out = []
    for r in simulate(forcing, crop['parameter_set'], capacity, initial_fraction):
        out.append({'date': r['day_utc'], 'storage_mm': r['storage_end_mm'], 'available_fraction': r['storage_end_mm'] / capacity,
                    'etc_mm': r['etc_mm'], 'eta_mm': r['eta_mm'], 'deficit_mm': r['et_deficit_mm'], 'drainage_mm': r['drainage_mm'],
                    'ks': r['ks'], 'stage': r['phase']})
    return {'crop': crop['crop'], 'season': season_label(crop, sowing), 'sowing_date': sowing.isoformat(), 'harvest_date': harvest.isoformat(),
            'initial_fraction': initial_fraction, 'parameter_set': crop['parameter_set'], 'rows': out}


def water_layer(crops, weather_rows, capacity, initial_fraction=.7):
    """Every complete season of every crop inside the weather period. Initial storage 70% (water_balance_v1 baseline)."""
    if all(calendar_reason(c) for c in crops):
        return unavailable('crop_calendar_unknown', WATER_SOURCE)
    by_date = {r['date']: r for r in weather_rows}
    first, last = date.fromisoformat(weather_rows[0]['date']), date.fromisoformat(weather_rows[-1]['date'])
    seasons = []
    for crop in crops:
        if calendar_reason(crop):
            continue
        for year in range(first.year - 1, last.year + 1):
            sowing, harvest = season_dates(crop, year)
            if sowing < first or harvest - timedelta(days=1) > last:
                continue
            season = _season(crop, sowing, harvest, by_date, capacity, initial_fraction)
            if season:
                seasons.append(season)
    if not seasons:
        return unavailable('source_unavailable', WATER_SOURCE)
    seasons.sort(key=lambda s: (s['sowing_date'], s['crop']))
    notes = sorted({PARAMETER_NOTES[s['parameter_set']] for s in seasons if s['parameter_set'] in PARAMETER_NOTES})
    return {'status': 'available', 'source': WATER_SOURCE, 'method': '; '.join(['FAO-56 Kc single crop coefficient, fixed root zone (hypothesis)', *notes]),
            'period': {'start': seasons[0]['sowing_date'], 'end': max(s['rows'][-1]['date'] for s in seasons)}, 'seasons': seasons}
