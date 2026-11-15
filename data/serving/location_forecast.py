"""10-day soil-water forecast for the location core.

Same physics as data/ingest/water_projection (bucket_day with the crop's Kc), same provider and model
(Open-Meteo, ECMWF IFS 0.25, UTC daily sums; a day with a missing hour stays missing). Storage is spun up
from sowing with observed POWER weather up to the day before issue. Without a crop in the field on the
issue date the layer is `out_of_season`; nothing is shown as current when it is not.
"""
from datetime import timedelta

import requests

from data.ingest.water_projection import MODEL, daily_sums
from data.models.water_balance import CROPS, bucket_day, crop_day
from data.serving.location_catalog import calendar_reason, season_dates, season_label

SOURCE = f'Open-Meteo forecast API, model {MODEL} (ECMWF IFS 0.25), UTC daily sums'
URL = 'https://api.open-meteo.com/v1/forecast'
HORIZON = 10
MAX_PAST_DAYS = 92  # Open-Meteo forecast API past_days limit


def unavailable(reason):
    return {'status': 'unavailable', 'reason_code': reason, 'source': SOURCE}


def fetch_forecast(lon, lat, past_days=0):
    """{date: {'rain_mm', 'et0_mm'} | None}; None for the whole call when the service fails."""
    params = {'latitude': lat, 'longitude': lon, 'hourly': 'precipitation,et0_fao_evapotranspiration', 'models': MODEL,
              'timezone': 'UTC', 'forecast_days': HORIZON + 1, 'past_days': past_days}
    try:
        r = requests.get(URL, params=params, timeout=120)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return None
    try:
        units, h = payload['hourly_units'], payload['hourly']
        if units['precipitation'] != 'mm' or units['et0_fao_evapotranspiration'] != 'mm':
            return None
        h['time'], h['precipitation'], h['et0_fao_evapotranspiration']
    except (KeyError, TypeError):
        return None
    rain, et0 = daily_sums(h['time'], h['precipitation']), daily_sums(h['time'], h['et0_fao_evapotranspiration'])
    return {d: (None if rain[d] is None or et0.get(d) is None else {'rain_mm': rain[d], 'et0_mm': et0[d]}) for d in rain}


def _step(storage, parameter_set, index, capacity, rain, et0):
    phase, kc = crop_day(parameter_set, index)
    etc = kc * et0
    p = min(.8, max(.1, CROPS[parameter_set]['p_base'] + .04 * (5 - etc)))
    item = bucket_day(storage, capacity, rain, etc, p)
    return item['storage_end_mm'], item['ks'] < 1 - 1e-9


def _in_field(crops, day):
    for crop in crops:
        if calendar_reason(crop):
            continue
        for year in (day.year - 1, day.year):
            sowing, harvest = season_dates(crop, year)
            if sowing <= day < harvest:
                return crop, sowing, harvest
    return None


def forecast_layer(crops, issued_at, weather_rows, forecast, capacity, initial_fraction=.7, start_on=None):
    """`start_on`: first forecast day when observed weather lags (POWER latency); defaults to the issue date."""
    issued_on = start_on or issued_at.date()
    found = _in_field(crops, issued_on)
    if not found:
        return unavailable('out_of_season')
    crop, sowing, harvest = found
    if forecast is None:
        return unavailable('source_unavailable')
    observed = {r['date']: r for r in weather_rows}
    storage, index = capacity * initial_fraction, 0
    day = sowing
    while day < issued_on:
        row = observed.get(day.isoformat())
        if row is None:
            return unavailable('source_unavailable')
        storage, _ = _step(storage, crop['parameter_set'], index, capacity, row['rain_mm'], row['et0_mm'])
        day += timedelta(days=1); index += 1
    rows = []
    while len(rows) < HORIZON and day < harvest:
        f = forecast.get(day.isoformat())
        if f is None:
            break
        storage, stress = _step(storage, crop['parameter_set'], index, capacity, f['rain_mm'], f['et0_mm'])
        rows.append({'date': day.isoformat(), 'rain_mm': f['rain_mm'], 'et0_mm': f['et0_mm'], 'storage_mm': storage, 'stress': stress})
        day += timedelta(days=1); index += 1
    if not rows:
        return unavailable('source_unavailable')
    return {'status': 'available', 'source': SOURCE, 'method': 'FAO-56 bucket advanced with forecast rain and ET0; storage spun up from sowing with NASA POWER',
            'period': {'start': rows[0]['date'], 'end': rows[-1]['date']}, 'issued_at': issued_at.isoformat(),
            'crop': crop['crop'], 'season': season_label(crop, sowing), 'rows': rows}
