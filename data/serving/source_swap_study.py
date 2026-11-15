"""Effect of operational sources on the packaged models (MVP municipalities, harvests 2019-2021).

Same harvests, cutoffs, labels and models; only the inputs change:
retrospective = NASA POWER with IMERG Final rain and CERES radiation (as in training);
operational   = IMERG Late rain (GES DISC subset already validated) and ERA5 radiation
                (Open-Meteo archive), with ET0 recomputed. Temperature, humidity, wind,
                dew point and pressure stay from POWER in both.
"""
import json

from data.ingest import cohort_v5 as v5
from data.ingest.cohort_v6 import RAW as V6_RAW
from data.ingest.crops import horizons
from data.ingest.water_balance_run import ROOT, save_json, write_csv
from data.serving.live_features import fetch_era5_radiation, feature_row, swap_sources
from data.serving.yield_model import YieldModel

DIST = ROOT / 'dist/gaiapus-models-v7'
OUT = ROOT / 'data/processed/source_swap_v1'
YEARS = (2019, 2020, 2021)


def main():
    import pandas as pd
    from datetime import date
    locations = json.loads((ROOT / 'data/raw/training_v1/locations.json').read_text(encoding='utf-8'))
    manifest = json.loads((DIST / 'models_manifest.json').read_text(encoding='utf-8'))
    models = {}
    for entry in manifest['entries']:
        if entry['cohort'] in ('soy', 'maize2'):
            models[(entry['cohort'], entry['scope'])] = (entry['model'], YieldModel(DIST / 'models' / entry['artifact']))
    points = pd.read_csv(ROOT / 'data/processed/imerg_subset_v1/points.csv', dtype={'municipality_code': str})
    late = {(r.municipality_code, r.day_utc): (r.rain_mm if r.complete_48_half_hours else None)
            for r in points[points.run == 'Late'].itertuples()}
    radiation = fetch_era5_radiation([(l['latitude'], l['longitude']) for l in locations], date(2016, 1, 1), date(2021, 12, 31))
    rows = []
    for location in locations:
        code = location['municipality_code']
        weather, elevation = v5.daily_weather(code, root=V6_RAW)
        operational = swap_sources(weather, location['latitude'], elevation,
                                   rain={day: late.get((code, day)) for day in weather if day >= '2016-01-01'},
                                   radiation=radiation[(location['latitude'], location['longitude'])])
        for crop in ('soy', 'maize2'):
            scope = 'MG+MT' if (crop, 'MG+MT') in models else 'national'
            name, model = models[(crop, scope)]
            history = {}
            for item in v5.parse_labels(json.loads((V6_RAW / f'pam_{crop}_{code[:2]}.json').read_bytes()), crop):
                if item['municipality_code'] == code and item['label_valid']:
                    history[item['harvest_year']] = item['yield_kg_ha']
            for year in YEARS:
                if year not in history:
                    continue
                for horizon in horizons(crop):
                    a = feature_row(crop, code, year, horizon, weather, history, location, elevation)
                    b = feature_row(crop, code, year, horizon, operational, history, location, elevation)
                    if a is None or b is None:
                        rows.append({'crop': crop, 'municipality_code': code, 'harvest_year': year, 'horizon_days': horizon, 'complete': False})
                        continue
                    ea, eb = model.predict(a), model.predict(b)
                    rows.append({'crop': crop, 'municipality_code': code, 'harvest_year': year, 'horizon_days': horizon, 'complete': True,
                                 'model': name, 'scope': scope, 'actual_kg_ha': history[year], 'retrospective_kg_ha': ea,
                                 'operational_kg_ha': eb, 'estimate_change_kg_ha': eb - ea,
                                 'rain_retrospective_mm': a['rain_sum_mm'], 'rain_operational_mm': b['rain_sum_mm'],
                                 'radiation_retrospective': a['radiation_sum_mj_m2'], 'radiation_operational': b['radiation_sum_mj_m2']})
    table = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / 'cases.csv', table.to_dict('records'))
    done = table[table.complete]
    summary = []
    for (crop, horizon), part in [((c, 'all'), g) for c, g in done.groupby('crop')] + list(done.groupby(['crop', 'horizon_days'])):
        summary.append({'crop': crop, 'horizon_days': horizon, 'cases': len(part), 'coverage': float(len(part) / len(table[(table.crop == crop) & ((table.horizon_days == horizon) if horizon != 'all' else True)])),
                        'mae_retrospective_kg_ha': float((part.retrospective_kg_ha - part.actual_kg_ha).abs().mean()),
                        'mae_operational_kg_ha': float((part.operational_kg_ha - part.actual_kg_ha).abs().mean()),
                        'mean_abs_estimate_change_kg_ha': float(part.estimate_change_kg_ha.abs().mean()),
                        'max_abs_estimate_change_kg_ha': float(part.estimate_change_kg_ha.abs().max()),
                        'mean_rain_change_pct': float(100 * ((part.rain_operational_mm - part.rain_retrospective_mm) / part.rain_retrospective_mm).mean()),
                        'mean_radiation_change_pct': float(100 * ((part.radiation_operational - part.radiation_retrospective) / part.radiation_retrospective).mean())})
    write_csv(OUT / 'summary.csv', summary)
    save_json(OUT / 'manifest.json', {'scope': '8 MVP municipalities, harvests 2019-2021, all cutoffs', 'models': {f'{k[0]}|{k[1]}': v[0] for k, v in models.items()},
                                      'retrospective': 'POWER IMERG Final + CERES', 'operational': 'IMERG Late V07 + ERA5 (Open-Meteo), ET0 recomputed'})
    print(pd.DataFrame(summary).round(1).to_string(index=False))


if __name__ == '__main__':
    from data.serving.source_swap_study import main as entry
    entry()
