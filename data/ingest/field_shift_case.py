"""Field Shift case: soy -> second-crop maize vs soy -> maize intercropped with Brachiaria (Sorriso, MT).

SIMULATED indicators only, from the project's water balance with real NASA POWER weather
for six agricultural years (2015/16 to 2020/21). Soy is sown on the state calendar date
(MT: 5 October, hypothetical); maize is sown the day after the soy cycle ends (115 days).
After the maize cycle (120 days) the reference leaves bare fallow; the alternative keeps
Brachiaria as cover until desiccation 15 days before the next soy sowing.

Hypotheses (central value and range tested in the sensitivity):
- Brachiaria dry-season Kc 0.80 (0.75-0.85, FAO-56 grazing pasture); water stress is applied by the bucket.
- Straw lowers soil evaporation at soy establishment: soy Kc_initial x 0.8 (0.7-1.0).
- Bare fallow Kc 0.30; soil water capacity AD4 = 55 mm (AD2 32 mm, AD6 95 mm).
- Soy sowing on the calendar date (shifted by -15 and +15 days in the sensitivity).
Not simulated and not measured: yield, soil carbon, infiltration, weeds, nitrogen.
The municipal yield estimate is a separate product and response.
"""
import argparse
from datetime import date, datetime, timedelta, timezone

from data.ingest import cohort_v5 as v5
from data.ingest.cohort_v6 import RAW as V6_RAW
from data.ingest.water_balance_run import ROOT, save_json, write_csv
from data.models.water_balance import CROPS, bucket_day, phase_kc

CODE = '5107925'
YEARS = range(2015, 2021)
OUT = ROOT / 'data/processed/field_shift_v1'
CENTRAL = {'cover_kc': 0.80, 'straw_factor': 0.80, 'cad_class': 'AD4', 'soy_shift_days': 0}
SENSITIVITY = {'cover_kc': (0.75, 0.85), 'straw_factor': (0.7, 0.9, 1.0), 'cad_class': ('AD2', 'AD6'), 'soy_shift_days': (-15, 15)}
SOY_DAYS, MAIZE_DAYS, DESICCATION_DAYS = 115, 120, 15
# 'soy_long_fallow' (v2): the same soy, then bare fallow until the next soy sowing. No maize is grown.
SCENARIOS = ('reference_fallow', 'brachiaria', 'soy_long_fallow')


def calendar(year, shift):
    soy = date(year, 10, 5) + timedelta(days=shift)
    maize = soy + timedelta(days=SOY_DAYS)
    next_soy = date(year + 1, 10, 5) + timedelta(days=shift)
    return {'soy_sowing': soy, 'maize_sowing': maize, 'maize_end': maize + timedelta(days=MAIZE_DAYS),
            'desiccation': next_soy - timedelta(days=DESICCATION_DAYS), 'next_soy_sowing': next_soy}


def simulate_year(weather, year, scenario, params, storage, trace=None):
    """Daily bucket for one agricultural year; `scenario` is one of SCENARIOS.

    `trace`, when a list, receives the unrounded bucket result of each day (mass balance checks and window sums).
    """
    if scenario not in SCENARIOS:
        raise ValueError(f'Unknown Field Shift scenario: {scenario}')
    dates = calendar(year, params['soy_shift_days'])
    capacity = CROPS['soy']['cad_mm'][params['cad_class']]
    straw = params['straw_factor'] if scenario == 'brachiaria' and year > YEARS[0] else 1.0
    soy = dict(CROPS['soy'], kc_initial=CROPS['soy']['kc_initial'] * straw)
    rows, day = [], dates['soy_sowing']
    while day < dates['next_soy_sowing']:
        w = weather[day.isoformat()]
        if day < dates['maize_sowing']:
            stage, kc = phase_kc(soy, (day - dates['soy_sowing']).days); land, p_base = 'soy', soy['p_base']
        elif day < dates['maize_end'] and scenario != 'soy_long_fallow':
            stage, kc = phase_kc(CROPS['maize2'], (day - dates['maize_sowing']).days); land, p_base = 'maize2', CROPS['maize2']['p_base']
            if scenario == 'brachiaria':
                land = 'maize2+brachiaria'
        elif scenario == 'brachiaria' and day < dates['desiccation']:
            stage, kc, land, p_base = 'off_season_cover', params['cover_kc'], 'brachiaria', .6
        else:
            stage, kc, land, p_base = 'off_season_bare', .30, 'bare', .6
        etc = kc * w['et0_mm']
        p = min(.8, max(.1, p_base + .04 * (5 - etc)))
        item = bucket_day(storage, capacity, w['rain_mm'], etc, p)
        storage = item['storage_end_mm']
        if trace is not None:
            trace.append(item)
        rows.append({'date': day.isoformat(), 'land_cover': land, 'stage': stage, 'soil_covered': land != 'bare',
                     'rain_mm': round(w['rain_mm'], 2), 'et0_mm': round(w['et0_mm'], 2), 'kc': round(kc, 3),
                     'etc_mm': round(item['etc_mm'], 3), 'eta_mm': round(item['eta_mm'], 3), 'et_deficit_mm': round(item['et_deficit_mm'], 3),
                     'drainage_mm': round(item['drainage_mm'], 3), 'storage_end_mm': round(storage, 3),
                     'available_water_fraction': round(storage / capacity, 4), 'ks': round(item['ks'], 4)})
        day += timedelta(days=1)
    return rows, storage, dates


def indicators(rows, dates, next_rows):
    soy = [r for r in rows if r['land_cover'] == 'soy']
    maize = [r for r in rows if r['land_cover'].startswith('maize2')]
    off = [r for r in rows if r['stage'].startswith('off_season')]
    # Without maize rows the maize indicators are absent (None): an empty sum is not a zero deficit.
    return {'soy_sowing': dates['soy_sowing'].isoformat(), 'maize_sowing': dates['maize_sowing'].isoformat() if maize else None,
            'next_soy_sowing': dates['next_soy_sowing'].isoformat(),
            'covered_days': sum(r['soil_covered'] for r in rows), 'bare_days': sum(not r['soil_covered'] for r in rows),
            'off_season_water_use_mm': round(sum(r['eta_mm'] for r in off), 1),
            'off_season_drainage_mm': round(sum(r['drainage_mm'] for r in off), 1),
            'storage_at_next_sowing_mm': rows[-1]['storage_end_mm'],
            'soy_deficit_mm': round(sum(r['et_deficit_mm'] for r in soy), 1),
            'maize_deficit_mm': round(sum(r['et_deficit_mm'] for r in maize), 1) if maize else None,
            'maize_stress_days': sum(r['ks'] < 1 - 1e-9 for r in maize) if maize else None,
            'next_soy_establishment_deficit_mm': round(sum(r['et_deficit_mm'] for r in next_rows[:15]), 2) if next_rows else None}


def run(weather, scenario, params):
    capacity = CROPS['soy']['cad_mm'][params['cad_class']]
    storage, years, series = .7 * capacity, [], {}
    simulated = {}
    for year in [*YEARS, YEARS[-1] + 1]:
        if (date(year + 1, 10, 5) + timedelta(days=params['soy_shift_days'])).isoformat() not in weather:
            break
        rows, storage, dates = simulate_year(weather, year, scenario, params, storage)
        simulated[year] = (rows, dates)
    for year in YEARS:
        if year not in simulated:
            break  # weather ends before the next sowing: fewer valid years, never a truncated one
        rows, dates = simulated[year]
        next_rows = simulated.get(year + 1, (None, None))[0]
        years.append({'agricultural_year': f'{year}/{year + 1}', **indicators(rows, dates, next_rows)})
        series[f'{year}/{year + 1}'] = rows
    return years, series


def mean(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    return round(sum(values) / len(values), 2) if values else None


def summary(years):
    keys = ('covered_days', 'off_season_water_use_mm', 'off_season_drainage_mm', 'storage_at_next_sowing_mm', 'soy_deficit_mm',
            'maize_deficit_mm', 'maize_stress_days', 'next_soy_establishment_deficit_mm')
    return {k: mean(years, k) for k in keys}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    weather, _ = v5.daily_weather(CODE, root=V6_RAW)
    OUT.mkdir(parents=True, exist_ok=True)
    scenarios, sensitivity = {}, []
    for scenario in ('reference_fallow', 'brachiaria'):
        years, series = run(weather, scenario, CENTRAL)
        scenarios[scenario] = {'years': years, 'mean': summary(years)}
        write_csv(OUT / f'{scenario}_daily.csv', [{'agricultural_year': y, **r} for y, rows in series.items() for r in rows])
        write_csv(OUT / f'{scenario}_by_year.csv', years)
        scenarios[scenario]['series_last_year'] = series[f'{YEARS[-1]}/{YEARS[-1] + 1}']
    for key, values in SENSITIVITY.items():
        for value in values:
            params = {**CENTRAL, key: value}
            pair = {s: summary(run(weather, s, params)[0]) for s in ('reference_fallow', 'brachiaria')}
            sensitivity.append({'parameter': key, 'value': value,
                                **{f'difference_{k}': round(pair['brachiaria'][k] - pair['reference_fallow'][k], 2)
                                   for k in pair['brachiaria'] if pair['brachiaria'][k] is not None},
                                'reference_storage_at_next_sowing_mm': pair['reference_fallow']['storage_at_next_sowing_mm'],
                                'brachiaria_storage_at_next_sowing_mm': pair['brachiaria']['storage_at_next_sowing_mm']})
    write_csv(OUT / 'sensitivity.csv', sensitivity)
    central_difference = {k: round(scenarios['brachiaria']['mean'][k] - scenarios['reference_fallow']['mean'][k], 2)
                          for k in scenarios['brachiaria']['mean'] if scenarios['brachiaria']['mean'][k] is not None}
    response = {
        'schema_version': '1.0.0', 'product': 'field_shift_scenario', 'generated_at': datetime.now(timezone.utc).isoformat(),
        'municipality_code': CODE, 'municipality': 'Sorriso - MT', 'simulated': True,
        'sowing': {'soy': {'date_pattern': '10-05', 'origin': 'hypothetical_fixed_calendar_per_state (MT, 5 October)'},
                   'maize': {'rule': f'day after the soy cycle ({SOY_DAYS} days)', 'origin': 'derived_from_assumed_soy_cycle'},
                   'brachiaria': {'rule': 'sown with the maize; desiccated 15 days before the next soy', 'origin': 'assumed_management'}},
        'weather': {'source': 'NASA POWER daily point (IMERG rain), retrospective', 'agricultural_years': [f'{y}/{y + 1}' for y in YEARS]},
        'soil': {'cad_class': CENTRAL['cad_class'], 'capacity_mm': CROPS['soy']['cad_mm'][CENTRAL['cad_class']],
                 'basis': 'fixed hypothesis, not a measured profile'},
        'producer_priority': 'keep the soil covered between harvests without raising the water deficit of the next soy at establishment',
        'hypotheses': {'central': CENTRAL, 'ranges_tested': SENSITIVITY,
                       'sources': {'cover_kc': 'FAO-56 grazing pasture Kc 0.75-0.85', 'straw_factor': 'assumed reduction of soil evaporation by straw',
                                   'fallow_kc': 'bare soil 0.30'}},
        'scenarios': {s: {'mean': v['mean'], 'by_year': v['years']} for s, v in scenarios.items()},
        'difference_brachiaria_minus_reference': central_difference,
        'sensitivity': sensitivity,
        'daily_series_last_year': {s: v['series_last_year'] for s, v in scenarios.items()},
        'not_simulated': ['yield', 'soil carbon', 'infiltration', 'weeds', 'nitrogen'],
        'yield_effect': None, 'yield_estimate_note': 'The municipal yield estimate is the separate yield_estimate response.',
        'evidence': 'Simulation under stated hypotheses; no field measurement of these effects in Sorriso.'}
    save_json(OUT / 'sorriso_field_shift.json', response)
    save_json(ROOT / 'contracts/examples/field-shift-sorriso.json', response)
    for s, v in scenarios.items():
        print(s, v['mean'])
    print('difference', central_difference)
    for row in sensitivity:
        print(row['parameter'], row['value'], {k: v for k, v in row.items() if k.startswith('difference_') and k.split('_', 1)[1] in
                                               ('covered_days', 'off_season_water_use_mm', 'storage_at_next_sowing_mm', 'next_soy_establishment_deficit_mm')})


if __name__ == '__main__':
    from data.ingest.field_shift_case import main as entry
    entry()
