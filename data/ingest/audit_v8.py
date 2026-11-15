"""v8 audit: inventory, development allowlist with checksums, lag availability and baseline definitions.

Reads only development partitions (harvests up to 2021). The 2022-2024 test tables and
predictions are listed as forbidden and never opened here.
"""
import json
from datetime import date, datetime, timezone

from data.ingest.water_balance_run import ROOT, save_json, sha

OUT = ROOT / 'data/processed/training_v8'
# IBGE release calendar (servicodados.ibge.gov.br/api/v3/calendario/9117), PAM year -> publication date.
PAM_RELEASE = {2016: date(2017, 9, 21), 2017: date(2018, 9, 13), 2018: date(2019, 9, 5), 2019: date(2020, 10, 1),
               2020: date(2021, 9, 22), 2021: date(2022, 9, 15), 2022: date(2023, 9, 14), 2023: date(2024, 9, 12),
               2024: date(2025, 9, 11), 2025: date(2026, 9, 17)}
UNKNOWN_RELEASE_RULE = 'PAM years before 2016: assumed published on 31 October of the following year (latest observed release was 1 October)'
ALLOWED = ['data/processed/training_v6/samples.parquet', 'data/processed/training_v7/samples.parquet',
           'data/raw/training_v7/labels_all.csv', 'data/raw/training_v6/locations.json', 'data/raw/training_v7/locations.json',
           'data/processed/training_v7/validation/soy/cohort.json', 'data/processed/training_v7/validation/maize2/cohort.json',
           'data/processed/training_v7/validation/coffee_arabica/cohort.json', 'data/processed/training_v7/validation/coffee_total_mg/cohort.json',
           'data/processed/training_v7/validation/coffee_conilon/cohort.json', 'data/frozen/regional_rule_soy_mg_mt_g30.json']
FORBIDDEN = ['data/processed/training_v6/test_samples.parquet', 'data/processed/training_v7/test_samples.parquet',
             'data/processed/training_v7/test_predictions_*.parquet', 'data/processed/training_v7/test/*',
             'data/raw/training_v6/sealed_test/*', 'data/raw/training_v7/sealed_test/*']
CROPS = ('soy', 'maize2', 'coffee_arabica', 'coffee_total_mg', 'coffee_conilon')


def release_date(pam_year):
    return PAM_RELEASE.get(pam_year, date(pam_year + 1, 10, 31))


def lag_available(pam_year, decision):
    return release_date(pam_year) <= decision


def main():
    import pandas as pd
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {p: {'sha256': sha(ROOT / p), 'bytes': (ROOT / p).stat().st_size} for p in ALLOWED}
    v6 = pd.read_parquet(ROOT / 'data/processed/training_v6/samples.parquet')
    v7 = pd.read_parquet(ROOT / 'data/processed/training_v7/samples.parquet')
    frame = pd.concat([v6[v6.crop.isin(['soy', 'maize2'])], v7[v7.crop.isin(CROPS[2:])]], ignore_index=True)
    if frame.harvest_year.max() > 2021:
        raise SystemExit('Development tables contain sealed years')
    inventory = {}
    for crop, part in frame.groupby('crop'):
        one = part.drop_duplicates('sample_id')
        inventory[crop] = {'rows': len(part), 'harvests': int(one.sample_id.nunique()), 'municipalities': int(one.municipality_code.nunique()),
                           'states': sorted(one.state_code.unique()), 'years': [int(one.harvest_year.min()), int(one.harvest_year.max())],
                           'cutoffs': sorted(int(h) for h in part.horizon_days.unique()),
                           'mg_mt_harvests': int(one[one.state_code.isin(['31', '51'])].sample_id.nunique()),
                           'held_out_municipalities': int(one[one.spatial_holdout].municipality_code.nunique()),
                           'duplicate_keys': int(part.duplicated(['sample_id', 'horizon_days']).sum()),
                           'cutoffs_per_harvest_min': int(part.groupby('sample_id').horizon_days.nunique().min())}
    # Lag availability under the real release calendar (decision = day after the feature cutoff).
    lag_audit = []
    for crop, part in frame.groupby('crop'):
        decision = pd.to_datetime(part.feature_cutoff).dt.date + pd.Timedelta(days=1)
        if crop.startswith('coffee'):
            minimum = part.milestone.map({'pre_flowering': 3, 'post_flowering': 2, 'grain_filling': 2})
        else:
            minimum = pd.Series(2, index=part.index)
        lag_year = part.harvest_year - minimum
        ok = [lag_available(int(y), d) for y, d in zip(lag_year, decision)]
        bad = part[[not x for x in ok]]
        lag_audit.append({'crop': crop, 'rows': len(part), 'rows_with_unpublished_first_lag': len(bad),
                          'examples': bad[['sample_id', 'horizon_days', 'feature_cutoff']].head(5).astype(str).to_dict('records')})
    baselines = {
        'annual ref_lag_mean': 'mean of the published municipal yields of harvests Y-2, Y-3 and Y-4 that exist (cohort_v5.lagged_yield)',
        'annual history (fitted)': 'Ridge on harvest_year, yield_lag2to4_mean and yield_lag_count, one model per cutoff (train_v7.History)',
        'coffee ref_last_available': 'most recent published yield with lag >= the milestone minimum (3 at pre-flowering, 2 otherwise)',
        'coffee ref_same_phase': 'yield of the most recent even lag (Y-2, Y-4 or Y-6) allowed at the milestone: same biennial phase',
        'coffee ref_biennial_mean': 'mean of the two most recent allowed even lags',
        'coffee ref_cycle_mean': 'mean of the two most recent allowed lags, i.e. one high and one low year of the biennial cycle',
        'coffee high_year': 'label-derived (uses Y-1 and Y+1 yields): diagnostic only, never a feature (checked below)'}
    from data.ingest import train_v7
    feature_columns = {c for _, cols in train_v7.feature_sets('coffee')[1].items() for c in cols} | set(train_v7.COFFEE_BASE)
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'development_manifest': manifest, 'forbidden_inputs': FORBIDDEN,
              'inventory': inventory, 'lag_availability': lag_audit, 'pam_release_calendar': {str(k): str(v) for k, v in PAM_RELEASE.items()},
              'unknown_release_rule': UNKNOWN_RELEASE_RULE, 'baselines': baselines,
              'high_year_in_features': 'high_year' in feature_columns,
              'note_2022_2024': 'Opened once for the v7 test; it motivated v8 and can no longer serve as an independent test.'}
    save_json(OUT / 'audit.json', report)
    print(json.dumps({k: report[k] for k in ('inventory', 'high_year_in_features')}, indent=1))
    for row in lag_audit:
        print(row['crop'], 'unpublished first lag rows:', row['rows_with_unpublished_first_lag'], 'of', row['rows'])


if __name__ == '__main__':
    main()
