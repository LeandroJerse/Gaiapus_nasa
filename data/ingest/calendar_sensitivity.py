"""How much a different sowing date changes the water features and the v6 Ridge estimate.

The v6 models were trained with a fixed state calendar. When a producer reports another
sowing date, the features move; this measures the size of that shift for the MVP
municipalities, harvests 2019-2021, at the 60 and 90-day cutoffs. It is a sensitivity
check of the deployed model, not a validation of the shifted calendar.
"""
from datetime import timedelta

from data.ingest import cohort_v5 as v5
from data.ingest.cohort_v6 import RAW as V6_RAW, sowing_date
from data.ingest.hydro_training_features import features_at_cutoff
from data.ingest.water_balance_run import ROOT, save_json, write_csv

SHIFTS = (-30, -15, 0, 15, 30)
OUT = ROOT / 'data/processed/training_v6/calendar_sensitivity'


def main():
    import joblib
    import pandas as pd
    samples = pd.read_parquet(ROOT / 'data/processed/training_v6/samples.parquet')
    rows = []
    for crop in ('soy', 'maize2'):
        bundle = joblib.load(ROOT / f'data/models/research_v6/{crop}_site_ridge_validation_2021.joblib')
        target = samples[(samples.crop == crop) & samples.mvp & samples.harvest_year.isin([2019, 2020, 2021]) & samples.horizon_days.isin([60, 90])]
        for code in target.municipality_code.unique():
            weather, _ = v5.daily_weather(code, root=V6_RAW)
            for record in target[target.municipality_code == code].itertuples():
                for shift in SHIFTS:
                    start = sowing_date(code, crop, record.harvest_year) + timedelta(days=shift)
                    prefix = [weather[(start + timedelta(days=i)).isoformat()] for i in range(record.horizon_days)]
                    features = features_at_cutoff(prefix, crop, start, record.horizon_days)
                    frame = pd.DataFrame([{**record._asdict(), **features}])
                    estimate = float(bundle['baseline'].predict(frame)[0] + bundle['model'].predict(frame)[0])
                    rows.append({'crop': crop, 'municipality_code': code, 'harvest_year': record.harvest_year,
                                 'horizon_days': record.horizon_days, 'shift_days': shift, 'estimate_kg_ha': estimate,
                                 'actual_kg_ha': record.yield_kg_ha, 'storage_end_mm': features['hydro_storage_end_mm'],
                                 'rain_sum_mm': features['rain_sum_mm'],
                                 'deficit_mm': sum(v for k, v in features.items() if k.endswith('_deficit_mm') and v is not None)})
    table = pd.DataFrame(rows)
    reference = table[table.shift_days == 0].set_index(['crop', 'municipality_code', 'harvest_year', 'horizon_days'])
    table = table.join(reference[['estimate_kg_ha', 'storage_end_mm', 'deficit_mm']], on=['crop', 'municipality_code', 'harvest_year', 'horizon_days'], rsuffix='_ref')
    table['estimate_change_kg_ha'] = table.estimate_kg_ha - table.estimate_kg_ha_ref
    table['storage_change_mm'] = table.storage_end_mm - table.storage_end_mm_ref
    table['deficit_change_mm'] = table.deficit_mm - table.deficit_mm_ref
    table['abs_error_kg_ha'] = (table.estimate_kg_ha - table.actual_kg_ha).abs()
    summary = table.groupby(['crop', 'horizon_days', 'shift_days']).agg(
        cases=('estimate_kg_ha', 'size'), mean_abs_estimate_change_kg_ha=('estimate_change_kg_ha', lambda s: s.abs().mean()),
        max_abs_estimate_change_kg_ha=('estimate_change_kg_ha', lambda s: s.abs().max()),
        mean_abs_storage_change_mm=('storage_change_mm', lambda s: s.abs().mean()),
        mean_abs_deficit_change_mm=('deficit_change_mm', lambda s: s.abs().mean()), mae_kg_ha=('abs_error_kg_ha', 'mean')).reset_index()
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / 'cases.csv', table.to_dict('records'))
    write_csv(OUT / 'summary.csv', summary.to_dict('records'))
    save_json(OUT / 'manifest.json', {'model': 'research_v6 site+ridge, fold 2021 (trained through 2019)', 'shifts_days': list(SHIFTS),
                                      'scope': 'MVP municipalities, harvests 2019-2021, cutoffs 60 and 90', 'meaning': 'sensitivity, not validation'})
    print(summary.round(1).to_string(index=False))


if __name__ == '__main__':
    from data.ingest.calendar_sensitivity import main as entry
    entry()
