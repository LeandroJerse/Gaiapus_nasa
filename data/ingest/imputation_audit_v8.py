"""Cases affected by the state-level reference fill (review item 3).

Rule (train_v8.reference_predictor): a row without the published reference gets the mean yield of
its state over the training rows of the same fold (internal fit rows during selection; fold
training rows for the external prediction); a state absent from those rows gets the mean of all
of them. Counts per scope, pool, fold and stage, for every reference column of the crop.
"""
from data.ingest.cohort_v8 import load_development
from data.ingest.train_v8 import OUT, REF_COLUMNS, SCOPES, fold_split, inner_split
from data.ingest.water_balance_run import write_csv


def main():
    import pandas as pd
    rows = []
    for name, scope in SCOPES.items():
        frame = load_development(scope['crop'])
        states = scope['states']
        mask = (lambda f: f.state_code.isin(states)) if states else (lambda f: f.state_code == f.state_code)
        columns = [c for c in REF_COLUMNS.values() if c in frame]
        for pool in scope['pools']:
            pool_frame = frame if pool == 'national' else frame[mask(frame)]
            for year in (2019, 2020, 2021):
                train, target = fold_split(pool_frame, frame, year, mask)
                fit, val, _ = inner_split(train)
                for stage, source, rows_ in (('selection', fit, pd.concat([fit, val])), ('external', train, pd.concat([train, target]))):
                    known = set(source.state_code)
                    for column in columns:
                        missing = rows_[rows_[column].isna()]
                        rows.append({'scope': name, 'pool': pool, 'fold': year, 'stage': stage, 'reference': column,
                                     'rows': len(rows_), 'filled_rows': len(missing),
                                     'filled_target_rows': int(missing.harvest_year.eq(year).sum()) if stage == 'external' else None,
                                     'filled_with_state_mean': int(missing.state_code.isin(known).sum()),
                                     'filled_with_pool_mean_state_absent': int((~missing.state_code.isin(known)).sum())})
    write_csv(OUT / 'imputation_audit.csv', rows)
    return pd.DataFrame(rows)


if __name__ == '__main__':
    out = main()
    print(out.groupby(['scope', 'pool', 'stage', 'reference'])[['rows', 'filled_rows', 'filled_target_rows', 'filled_with_pool_mean_state_absent']].sum().to_string())
