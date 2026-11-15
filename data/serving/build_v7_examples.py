"""Generate national v7 fixtures offline; do not read the sealed test years."""
from data.ingest import train_v7 as v7
from data.ingest.water_balance_run import save_json, sha
from data.serving.build_examples import EXAMPLES, yield_examples
from data.serving.yield_model import YieldModel


def main():
    import math

    import pandas as pd

    # v6 is still the source of contract metadata shared by these two cohorts.
    for name, response, _ in yield_examples(write=False):
        path = EXAMPLES / name
        crop = response['crop']
        code = response['municipality_code']
        artifact = v7.MODELS / f'{crop}_site_ridge_2021.json'
        model = YieldModel(artifact)
        response['supported_cutoffs_days'] = model.cutoffs
        response['model'].update(version='research_v7_national_walk_forward', artifact_sha256=sha(artifact))
        response['warnings'].append('National validation artifact; no regional model selection is implied')
        horizon = response['cutoff_days_used']
        if horizon is not None:
            frame, _ = v7.load_cohort(crop)
            row = frame.loc[frame.municipality_code.astype(str).eq(code) &
                            frame.harvest_year.eq(2021) & frame.horizon_days.eq(horizon)]
            if len(row) != 1:
                raise RuntimeError(f'Expected one feature row for {crop}/{code}/{horizon}')
            prediction = model.predict(row.iloc[0].to_dict())
            predictions = pd.read_parquet(v7.OUT / f'predictions_{crop}.parquet')
            predictions = predictions.loc[predictions.sources.eq('site') & predictions.model.eq('ridge')]
            golden = predictions.loc[predictions.sample_id.eq(row.sample_id.iloc[0]) & predictions.horizon_days.eq(horizon)]
            if len(golden) != 1 or not math.isclose(prediction, float(golden.predicted_kg_ha.iloc[0]), abs_tol=1e-6, rel_tol=0):
                raise RuntimeError(f'v7 parity mismatch for {crop}/{code}/{horizon}')
            response['estimate_kg_ha'] = round(prediction, 1)
            metrics = {}
            cohort = predictions[predictions.horizon_days.eq(horizon)]
            for scope, part in cohort.groupby('spatial_holdout'):
                error = part.predicted_kg_ha - part.yield_kg_ha
                metrics[bool(scope)] = {
                    'mae_kg_ha': round(float(error.abs().mean()), 1),
                    'bias_kg_ha': round(float(error.mean()), 1),
                    'label_families': int(part.sample_id.nunique()),
                    'municipalities': int(part.municipality_code.nunique()),
                }
            response['validation'] = {'years': list(v7.VALIDATION_YEARS), 'cutoff_days': horizon,
                                      'scope': 'national walk-forward, municipalities used in training',
                                      **metrics[False],
                                      'out_of_training_municipalities': {k: metrics[True][k] for k in ('mae_kg_ha', 'label_families')}}
        save_json(path, response)
        print(name, response['status'], response['estimate_kg_ha'])


if __name__ == '__main__':
    main()
