"""Development errors split by how much published history each row had (plan v8 review, item 5).

For every scope, the walk-forward predictions (folds 2019-2021, municipalities used in training)
are joined to the development rows on the same keys and grouped by `yield_lag_count`
(0 = no published lag: the reference falls back to the mean yield of that fold's training pool;
the history Ridge imputes the lags with the training median). Row-level MAE per candidate.
"""
from data.ingest.cohort_v8 import load_development
from data.ingest.train_v8 import OUT, SCOPES
from data.ingest.water_balance_run import write_csv


def bucket(count):
    return '0 lags' if count == 0 else ('1-2 lags' if count <= 2 else '3+ lags')


def main():
    import pandas as pd
    rows = []
    for name, scope in SCOPES.items():
        path = OUT / name / 'predictions.parquet'
        if not path.exists():
            continue
        table = pd.read_parquet(path)
        table = table[~table.candidate.str.contains('_seed') & ~table.spatial_holdout]
        frame = load_development(scope['crop'])[['sample_id', 'horizon_days', 'yield_lag_count']]
        joined = table.merge(frame, on=['sample_id', 'horizon_days'], how='left', validate='many_to_one')
        if joined.yield_lag_count.isna().any():
            raise ValueError(f'{name}: predictions without a development row')
        joined['history_available'] = joined.yield_lag_count.map(bucket)
        joined['abs_error'] = (joined.predicted_kg_ha - joined.yield_kg_ha).abs()
        for (group, candidate), part in joined.groupby(['history_available', 'candidate']):
            rows.append({'scope': name, 'history_available': group, 'candidate': candidate, 'rows': len(part),
                         'harvests': part.sample_id.nunique(), 'mae_kg_ha': round(float(part.abs_error.mean()), 1)})
    write_csv(OUT / 'lag_availability_breakdown.csv', rows)
    return pd.DataFrame(rows)


if __name__ == '__main__':
    out = main()
    print(out.pivot_table(index=['scope', 'history_available'], columns='candidate', values='mae_kg_ha').round(0).to_string())
    print(out.groupby(['scope', 'history_available']).rows.max().to_string())
