"""Build the model feature row for one municipality, crop and cutoff, from retrospective or live sources.

The retrospective path reproduces the training cohorts exactly (NASA POWER with IMERG Final
rain and CERES radiation). Those two POWER variables arrive 2-3 months late, so the
operational path swaps them: IMERG Late daily rain (GES DISC, ~1 day late) and ERA5
shortwave radiation (Open-Meteo archive, ~6 days late); ET0 is recomputed with the
swapped radiation. Temperature, humidity, wind, dew point and pressure stay from POWER
(~2 days late). The effect of the swap on estimates is measured in source_swap_study.
"""
from datetime import date, timedelta

from data.ingest import cohort_v5 as v5
from data.ingest import cohort_v7 as c7
from data.ingest.crops import REGISTRY, sowing_date
from data.ingest.hydro_training_features import features_at_cutoff
from data.models.water_balance import et0_daily

SOURCES = {'retrospective': {'rain': 'power_imerg_final', 'radiation': 'power_ceres'},
           'operational': {'rain': 'imerg_late_v07', 'radiation': 'era5_open_meteo'}}
OPERATIONAL_LAG_DAYS = 6
COFFEE = c7.COFFEE


def swap_sources(weather, latitude, elevation, rain=None, radiation=None):
    """Replace rain and/or radiation by day and recompute ET0; a missing replacement leaves the day incomplete."""
    out = {}
    for day, row in weather.items():
        row = dict(row)
        if rain is not None:
            row['rain_mm'] = rain.get(day)
        if radiation is not None:
            row['radiation_mj_m2'] = radiation.get(day)
            needed = [row[k] for k in ('t_min_c', 't_max_c', 'radiation_mj_m2', 'ws2m_m_s', 'pressure_kpa', 'dewpoint_c')]
            row['et0_mm'] = None
            if None not in needed and row['t_min_c'] <= row['t_max_c'] and row['radiation_mj_m2'] >= 0:
                row['et0_mm'] = et0_daily(date.fromisoformat(day), latitude, elevation, *needed[:5], dewpoint=needed[5])['et0_mm']
        out[day] = row
    return out


def annual_row(crop, code, harvest_year, horizon, weather, history, location, elevation, sowing=None):
    """Feature row of an annual crop; `history` maps harvest year to published municipal yield."""
    entry = REGISTRY[crop]
    start = sowing or sowing_date(crop, code, harvest_year)
    prefix = [weather.get((start + timedelta(days=i)).isoformat()) for i in range(horizon)]
    required = ('rain_mm', 'et0_mm', 't_min_c', 't_max_c', 'radiation_mj_m2', 'rh2m_pct', 'ws2m_m_s')
    if any(r is None or any(r[f] is None for f in required) for r in prefix):
        return None
    features = features_at_cutoff(prefix, crop, start, horizon, config=entry['water'], weather_only=entry['water'] is None)
    lag_mean, lag_count = v5.lagged_yield(history, harvest_year)
    return {'municipality_code': code, 'crop': crop, 'harvest_year': harvest_year, 'horizon_days': horizon,
            'sowing_date': start.isoformat(), 'feature_cutoff': prefix[-1]['day_utc'], 'yield_lag2to4_mean_kg_ha': lag_mean,
            'yield_lag_count': lag_count, 'ref_lag_mean_kg_ha': lag_mean, 'latitude': location['latitude'],
            'longitude': location['longitude'], 'elevation_m': elevation, **features}


def coffee_row(crop, code, harvest_year, horizon, weather, history, location, elevation):
    milestones = COFFEE[crop]['milestones']
    start = date(harvest_year - 1, 6, 1)
    cutoff = start + timedelta(days=horizon - 1)
    milestone = next((m for m, (month, day, offset) in milestones.items() if date(harvest_year + offset, month, day) == cutoff), None)
    if milestone is None:
        raise ValueError(f'Cutoff {horizon} is not a coffee milestone')
    if any(weather.get((start + timedelta(days=i)).isoformat()) is None for i in range(horizon)):
        return None
    features = c7.coffee_features(weather, start, cutoff)
    if features is None:
        return None
    return {'municipality_code': code, 'crop': crop, 'harvest_year': harvest_year, 'horizon_days': horizon, 'milestone': milestone,
            'sowing_date': start.isoformat(), 'feature_cutoff': cutoff.isoformat(), 'latitude': location['latitude'],
            'longitude': location['longitude'], 'elevation_m': elevation, **c7.coffee_lags(history, harvest_year, milestone),
            **c7.previous_season(weather, harvest_year), **features}


def feature_row(crop, code, harvest_year, horizon, weather, history, location, elevation, sowing=None):
    if crop in COFFEE:
        return coffee_row(crop, code, harvest_year, horizon, weather, history, location, elevation)
    return annual_row(crop, code, harvest_year, horizon, weather, history, location, elevation, sowing)


# ---- network fetchers (used by the daily job; never called by the tests) ----

def fetch_power(latitude, longitude, first, last):
    import requests
    url = 'https://power.larc.nasa.gov/api/temporal/daily/point'
    params = {'parameters': v5.POWER_PARAMS, 'community': 'AG', 'longitude': longitude, 'latitude': latitude,
              'start': first.strftime('%Y%m%d'), 'end': last.strftime('%Y%m%d'), 'format': 'JSON', 'time-standard': 'UTC'}
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def fetch_era5_radiation(points, first, last):
    """{(lat, lon): {day: MJ/m2}} from the Open-Meteo ERA5 archive; up to 100 points per call."""
    import os
    import requests
    out = {}
    key = os.environ.get('OPEN_METEO_API_KEY')  # commercial key: higher quota on the customer endpoint
    url = 'https://customer-archive-api.open-meteo.com/v1/archive' if key else 'https://archive-api.open-meteo.com/v1/archive'
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        params = {'latitude': ','.join(str(p[0]) for p in chunk), 'longitude': ','.join(str(p[1]) for p in chunk),
                  'start_date': str(first), 'end_date': str(last), 'daily': 'shortwave_radiation_sum', 'timezone': 'UTC', 'models': 'era5'}
        if key:
            params['apikey'] = key
        response = requests.get(url, params=params, timeout=180)
        response.raise_for_status()
        payload = response.json()
        for point, item in zip(chunk, payload if isinstance(payload, list) else [payload]):
            if item['daily_units']['shortwave_radiation_sum'] != 'MJ/m²':
                raise ValueError('Unexpected radiation unit')
            out[point] = dict(zip(item['daily']['time'], item['daily']['shortwave_radiation_sum']))
    return out


def fetch_imerg_late(points, day, token):
    """IMERG Late V07 daily rain (mm/day) at the 0.1 degree cell of each point, one OPeNDAP subset per day."""
    import netCDF4
    import numpy as np
    import requests
    cells = [(round((lon + 179.95) / 0.1), round((lat + 89.95) / 0.1)) for lat, lon in points]
    i0, i1 = min(c[0] for c in cells), max(c[0] for c in cells)
    j0, j1 = min(c[1] for c in cells), max(c[1] for c in cells)
    grid = f'[0:0][{i0}:{i1}][{j0}:{j1}]'
    filename = imerg_late_filename(day)  # minor version changes (V07B -> V07C), so ask the catalog
    url = (f'https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDL.07/{day:%Y}/{day:%m}/'
           f'{filename}.nc4?precipitation{grid},precipitation_cnt{grid}')
    response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=180)
    response.raise_for_status()
    with netCDF4.Dataset('late', memory=response.content) as ds:
        if ds.variables['precipitation'].units not in ('mm/day', 'mm d-1') or ds.BeginDate != day.isoformat():
            raise ValueError('Unexpected IMERG Late schema or day')
        rain = np.ma.filled(ds.variables['precipitation'][0].astype(float), np.nan)
        count = np.ma.filled(ds.variables['precipitation_cnt'][0].astype(float), np.nan)
    out = {'_granule': filename}
    for point, (i, j) in zip(points, cells):
        value, valid = rain[i - i0, j - j0], count[i - i0, j - j0]
        out[point] = None if np.isnan(value) or valid != 48 else float(value)
    return out


def imerg_late_filename(day):
    """Exact IMERG Late daily granule name for `day` from NASA CMR; raises if not yet published."""
    import requests
    entries = requests.get('https://cmr.earthdata.nasa.gov/search/granules.json', params={
        'short_name': 'GPM_3IMERGDL', 'version': '07', 'temporal': f'{day}T00:00:00Z,{day}T23:59:59Z', 'page_size': 5},
        timeout=60).json()['feed']['entry']
    names = sorted({e['producer_granule_id'] for e in entries if e['time_start'][:10] == day.isoformat()})
    if not names:
        raise LookupError(f'IMERG Late for {day} not published yet')
    return names[-1]


def fetch_pam_history(crop, code, years):
    """Published municipal yields from SIDRA; only years the caller is allowed to use."""
    import requests
    if crop in COFFEE:
        table, classification, category = 1613, 'c82', COFFEE[crop]['category']
    else:
        entry = REGISTRY[crop]
        table, classification, category = entry['table'], entry['classification'], entry['category']
    url = (f'https://apisidra.ibge.gov.br/values/t/{table}/n6/{code}/v/216,214,112/p/{",".join(map(str, years))}/{classification}/{category}')
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    labels = c7.parse_labels(response.json(), crop, category, years=(min(years), max(years)))
    return {r['harvest_year']: r['yield_kg_ha'] for r in labels if r['label_valid']}
