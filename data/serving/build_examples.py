"""Build contract examples from the real v6 artifacts and a live forecast request.

Yield examples are retrospective (harvest 2021, an MVP municipality) because the
estimate needs the validated model; the water projection example uses the forecast
issued before the request.
"""
import json
from datetime import date, datetime, timedelta, timezone

from data.ingest.cohort_v6 import sowing_date
from data.ingest.train_v6 import DATA, MODELS
from data.ingest.water_balance_run import ROOT, save_json, sha
from data.ingest.water_projection import OUT as PROJECTION, climatology
from data.serving.products import fetch_forcing, water_response, yield_response

EXAMPLES = ROOT / 'contracts/examples'
SOURCE_NAMES = {'history': 'IBGE PAM lagged yield (harvest year-2 and earlier)', 'weather': 'NASA POWER daily meteorology and IMERG rain',
                'hydro': 'FAO-56 water balance by phase', 'site': 'IBGE municipal point and POWER elevation'}


def load_bundle(champion):
    import joblib
    group = 'weather' if champion['champion_model'] == 'baseline' else champion['champion_sources']
    kind = 'ridge' if champion['champion_model'] == 'baseline' else champion['champion_model']
    path = MODELS / f'{champion["crop"]}_{group}_{kind}_validation_2021.joblib'
    return joblib.load(path), path


def validation_table(champion, samples):
    import pandas as pd
    table = pd.read_csv(DATA / 'validation_metrics_by_horizon.csv')
    rows = table[(table.crop == champion['crop']) & (table.sources == champion['champion_sources']) & (table.model == champion['champion_model'])]
    years = [2019, 2020, 2021]
    pool = samples[(samples.crop == champion['crop']) & samples.harvest_year.isin(years)]
    out = {}
    for cutoff in champion['supported_horizons']:
        temporal = rows[(rows.scope == 'temporal') & (rows.horizon_days == cutoff)].iloc[0]
        spatial = rows[(rows.scope == 'spatiotemporal') & (rows.horizon_days == cutoff)].iloc[0]
        inside = pool[(pool.horizon_days == cutoff) & ~pool.spatial_holdout]
        out[cutoff] = {'years': years, 'cutoff_days': int(cutoff), 'mae_kg_ha': round(float(temporal.mae_kg_ha), 1),
                       'bias_kg_ha': round(float(temporal.bias_kg_ha), 1), 'label_families': int(temporal.n),
                       'municipalities': int(inside.municipality_code.nunique()), 'scope': 'walk-forward, municipalities used in training',
                       'out_of_training_municipalities': {'mae_kg_ha': round(float(spatial.mae_kg_ha), 1), 'label_families': int(spatial.n)}}
    return out


def yield_examples(*, write=True):
    import pandas as pd
    samples = pd.read_parquet(DATA / 'samples.parquet')
    champions = {c['crop']: c for c in json.loads((DATA / 'validation_champions.json').read_text(encoding='utf-8'))}
    written = []
    for crop, code, day in (('soy', '5107925', 75), ('maize2', '5107925', 75), ('soy', '3170206', 20)):
        champion = champions[crop]
        bundle, path = load_bundle(champion)
        validation = validation_table(champion, samples)
        supported = champion['supported_horizons'] or [None]
        cutoff = max([c for c in supported if c is not None and c <= day], default=None)
        row = samples[(samples.municipality_code == code) & (samples.crop == crop) & (samples.harvest_year == 2021)
                      & (samples.horizon_days == (cutoff or min(c for c in samples.horizon_days.unique())))]
        estimate = None
        if cutoff is not None:
            estimate = float(bundle['baseline'].predict(row)[0])
            if champion['champion_model'] != 'baseline':
                estimate += float(bundle['model'].predict(row)[0])
        sources = ['history'] + ([] if champion['champion_model'] == 'baseline' else
                                 ['weather', 'hydro', 'site'][:['weather', 'hydro', 'site'].index(champion['champion_sources']) + 1])
        response = yield_response(
            municipality_code=code, crop=crop, harvest_year=2021, sowing_date=sowing_date(code, crop, 2021),
            days_after_sowing=day, supported_cutoffs=[c for c in supported if c is not None] or [30],
            estimate_kg_ha=estimate, observed_through=row.feature_cutoff.iloc[0] if cutoff else None,
            model={'version': 'research_v6_walk_forward', 'type': champion['champion_model'], 'sources': [SOURCE_NAMES[s] for s in sources],
                   'artifact_sha256': sha(path), 'trained_through_harvest': 2019},
            validation_by_cutoff=validation,
            coverage={'forcing_days_expected': int(cutoff or 0), 'forcing_days_present': int(cutoff or 0), 'sources': ['NASA POWER daily point']},
            warnings=['Retrospective example: harvest 2021 label is known and was used only for validation',
                      'Municipal yield, not field yield; sowing date is a fixed state calendar'])
        name = f'yield-{crop}-{code}-day{day}.json'
        if write:
            save_json(EXAMPLES / name, response)
        written.append((name, response, float(row.yield_kg_ha.iloc[0])))
    return written


def water_example(code='5107925', crop='soy', horizon=10):
    import pandas as pd
    locations = {l['municipality_code']: l for l in json.loads((ROOT / 'data/raw/training_v1/locations.json').read_text(encoding='utf-8'))}
    location = locations[code]
    decision_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    decision = datetime.fromisoformat(decision_at).date()
    sowing = sowing_date(code, crop, decision.year + 1 if crop == 'soy' and decision.month >= 9 else decision.year)
    series, issue = fetch_forcing(location['latitude'], location['longitude'], sowing, decision + timedelta(days=16))
    observed = [series.get((sowing + timedelta(days=i)).isoformat(), {'rain_mm': None, 'et0_mm': None}) for i in range((decision - sowing).days)]
    forecast = [series.get((decision + timedelta(days=i)).isoformat(), {'rain_mm': None, 'et0_mm': None}) for i in range(horizon)]
    normal = climatology(code)
    reference = [normal[(decision + timedelta(days=i)).isoformat()[5:]] for i in range(horizon)]
    limit = json.loads((PROJECTION / 'operational_limit.json').read_text(encoding='utf-8'))
    table = pd.read_csv(PROJECTION / 'backtest_by_lead.csv')
    by_lead = [{'lead_days': int(r.lead_days), 'storage_mae_mm': round(float(r.storage_mae_mm), 2),
                'reference_storage_mae_mm': round(float(r.reference_storage_mae_mm), 2), 'stress_accuracy': round(float(r.stress_accuracy), 3),
                'coverage': round(float(r.coverage), 3), 'passed': bool(r.meets_error and r.beats_reference and r.meets_coverage)}
               for r in table[table.scope == crop].itertuples()]
    response = water_response(municipality_code=code, latitude=location['latitude'], longitude=location['longitude'], crop=crop,
                              sowing_date=sowing, decision_at=decision_at, horizon_days=horizon, observed=observed, forecast=forecast,
                              issue=issue, limit=limit, backtest_by_lead=by_lead, reference=reference)
    name = f'water-{crop}-{code}-N{horizon}.json'
    save_json(EXAMPLES / name, response)
    return name, response['status'], response['horizon_days_served'], response['forecast']['issued_at']


def main():
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    from data.serving.build_v7_examples import main as build_v7
    build_v7()
    print('water', water_example())


if __name__ == '__main__':
    main()
