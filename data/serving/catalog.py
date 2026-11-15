"""Serving catalog: crops, covered municipalities, cutoffs, model per scope and validation error.

Built from the v7 validation runs, the frozen guide 30 rule and the packaged models.
The backend uses it to answer coverage, cutoff and status questions without the research data.
"""
import json
from datetime import datetime, timezone

from data.ingest import train_v7 as v7
from data.ingest.cohort_v7 import COFFEE
from data.ingest.crops import REGISTRY
from data.ingest.water_balance_run import ROOT, save_json

DIST = ROOT / 'dist/gaiapus-models-v7'
NAMES = {**{k: v['name'] for k, v in REGISTRY.items()}, **{k: v['name'] for k, v in COFFEE.items()}}
SOURCE_NAMES = {'history': 'IBGE PAM published yields (lags)', 'weather': 'NASA POWER meteorology + IMERG rain',
                'hydro': 'FAO-56 water balance', 'site': 'municipal point and elevation'}


def scope_metrics(predictions, sources, model, states=None):
    rows = predictions[(predictions.sources == sources) & (predictions.model == model)]
    if states:
        rows = rows[rows.state_code.isin(states)]
    out = {}
    for horizon, part in rows.groupby('horizon_days'):
        entry = {}
        for held, sub in part.groupby('spatial_holdout'):
            error = sub.predicted_kg_ha - sub.yield_kg_ha
            entry['out_of_training' if held else 'in_training'] = {
                'mae_kg_ha': round(float(error.abs().mean()), 1), 'bias_kg_ha': round(float(error.mean()), 1),
                'label_families': int(sub.sample_id.nunique()), 'municipalities': int(sub.municipality_code.nunique())}
        out[str(int(horizon))] = entry
    return out


def test_result(name):
    """First and only evaluation on 2022-2024 of the frozen national choice (None if not scored)."""
    path = v7.OUT / f'test/{name}/cohort.json'
    if not path.exists():
        return None
    r = json.loads(path.read_text(encoding='utf-8'))
    confirmed = r['ci95'][1] < 0
    return {'mae_kg_ha': round(r['champion_mae'], 1), 'bias_kg_ha': round(r['champion_bias'], 1),
            'out_of_training_mae_kg_ha': round(r['champion_spatial_mae'], 1) if r['champion_spatial_mae'] else None,
            'history_mae_kg_ha': round(r['history_mae'], 1), 'difference_vs_history_kg_ha': round(r['champion_minus_history'], 1),
            'ci95': [round(x, 1) for x in r['ci95']], 'harvests': r['harvests'], 'municipalities': r['municipalities'],
            'confirmed': confirmed, 'outcome': 'confirmed' if confirmed else ('worse_than_history' if r['ci95'][0] > 0 else 'not_confirmed')}


def regional_test(scope):
    """Guide 30 step 4 on 2022-2024: frozen regional model vs history in the region (harvest-level MAE, 50% per state)."""
    import pandas as pd
    from data.ingest.regional_rule_g30 import bootstrap, harvest_errors, weighted_mae
    path = v7.OUT / 'test_predictions_soy.parquet'
    t = pd.read_parquet(path)
    t = t[t.state_code.isin(scope['states']) & ~t.spatial_holdout].copy()
    t['candidate'] = t.sources + '+' + t.model
    h = harvest_errors(t)
    model_mae, by_state = weighted_mae(h[h.candidate == scope['model']])
    history_mae, _ = weighted_mae(h[h.candidate == 'history+history'])
    point, low, high = bootstrap(h[h.candidate == scope['model']], h[h.candidate == 'history+history'])
    return {'mae_kg_ha': round(model_mae, 1), 'mae_by_state': {k: round(v, 1) for k, v in by_state.items()}, 'history_mae_kg_ha': round(history_mae, 1),
            'difference_vs_history_kg_ha': round(point, 1), 'ci95': [round(low, 1), round(high, 1)], 'confirmed': high < 0,
            'outcome': 'confirmed' if high < 0 else ('worse_than_history' if low > 0 else 'not_confirmed')}


def training_ranges(frame, features):
    """0.5% and 99.5% of each model input in the training cohort, per cutoff, to flag inputs outside what the model saw."""
    out = {}
    for horizon, part in frame[~frame.spatial_holdout].groupby('horizon_days'):
        bounds = {}
        for feature in features:
            if feature in part and part[feature].notna().any():
                q = part[feature].astype(float).quantile([0.005, 0.995])
                bounds[feature] = [round(float(q.iloc[0]), 4), round(float(q.iloc[1]), 4)]
        out[str(int(horizon))] = bounds
    return out


def build():
    import pandas as pd
    manifest = json.loads((DIST / 'models_manifest.json').read_text(encoding='utf-8'))
    crops = {}
    for name in v7.COHORTS:
        info = json.loads((v7.OUT / f'validation/{name}/cohort.json').read_text(encoding='utf-8'))
        frame, kind = v7.load_cohort(name)
        predictions = pd.read_parquet(v7.OUT / f'predictions_{name}.parquet')
        municipalities = (frame.drop_duplicates('municipality_code')[['municipality_code', 'state_code', 'spatial_holdout']]
                          .sort_values('municipality_code'))
        milestones = None
        if name in COFFEE:
            milestones = {}
            for milestone, (month, day, offset) in COFFEE[name]['milestones'].items():
                milestones[milestone] = {'month': month, 'day': day, 'year_offset': offset}
        scopes = []
        for entry in (e for e in manifest['entries'] if e['cohort'] == name):
            sources, model = entry['model'].split('+')
            scopes.append({'scope': entry['scope'], 'states': entry['states'], 'model': entry['model'], 'type': model,
                           'artifact': entry['artifact'], 'files': entry['files'],
                           'sources': [SOURCE_NAMES['history']] + ([] if model == 'reference' else
                                       [SOURCE_NAMES[s] for s in ('weather', 'hydro', 'site')[:('weather', 'hydro', 'site').index(sources) + 1]]),
                           'validation_by_cutoff': scope_metrics(predictions, sources, model, entry['states'])})
        from data.serving.yield_model import YieldModel
        for scope in scopes:
            loaded = YieldModel(DIST / 'models' / scope['artifact'])
            features = sorted({f for h in loaded.cutoffs for f in loaded.required_features(h)} - {'horizon_days'})
            scope['training_ranges'] = training_ranges(frame, features)
        test = test_result(name)
        if test:
            for scope in scopes:
                if scope['scope'] != 'national':
                    scope['test'] = regional_test(scope)
        crops[name] = {
            'name': NAMES[name], 'kind': kind, 'validation_status': {'published': 'validated'}.get(info['status'], info['status']),
            'test_2022_2024': test,
            'operational_release': 'not_released', 'cutoffs_days': info['cutoffs'], 'milestones': milestones,
            'sowing_calendar': None if name in COFFEE else {s: f'{m:02d}-{d:02d}' for s, (m, d) in REGISTRY[name]['sowing'].items()},
            'sowing_year_offset': None if name in COFFEE else REGISTRY[name]['year_offset'],
            'validation_years': list(v7.VALIDATION_YEARS), 'mean_yield_kg_ha': round(info['mean_yield']),
            'municipalities': [{'code': r.municipality_code, 'state': r.state_code, 'held_out_in_validation': bool(r.spatial_holdout)}
                               for r in municipalities.itertuples()],
            'models': scopes}
    catalog = {'schema_version': '1.0.0', 'version': 'research_v7', 'created_at': datetime.now(timezone.utc).isoformat(),
               'operational_release': 'not_released', 'test_2022_2024_evaluated': all(c['test_2022_2024'] for c in crops.values()),
               'test_note': 'Scored once with the frozen choices; models were not reselected from the test. A change now needs a new independent window (PAM 2025).',
               'regional_rule': manifest['regional_rule'], 'crops': crops,
               'source_modes': {'retrospective': 'POWER with IMERG Final rain and CERES radiation (training inputs)',
                                'operational': 'POWER meteorology + IMERG Late rain + ERA5 radiation (live; validated on MVP 2019-2021)'},
               'operational_data_lag_days': 6}
    save_json(DIST / 'catalog.json', catalog)
    return catalog


from data.serving.yield_service import model_for  # noqa: E402  (kept for backwards compatibility)


if __name__ == '__main__':
    built = build()
    for crop, item in built['crops'].items():
        print(crop, item['validation_status'], len(item['municipalities']), [s['scope'] + ':' + s['model'] for s in item['models']])
