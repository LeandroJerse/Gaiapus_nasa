"""Train research baselines; keep 2022-2024 test outcomes sealed from evaluation."""
import hashlib
import json
from pathlib import Path

FEATURES = ['harvest_year', 'historical_yield_lag2to4_mean', 'rain_sum_mm', 'dry_days_lt1mm',
            'max_dry_spell_days', 't_min_mean_c', 't_max_mean_c', 'heat_days_gt35c',
            'radiation_sum_mj_m2', 'rh_mean_pct', 'wind_mean_m_s']


def training_mask(row):
    return row['split_time'] == 'train' and row['spatial_holdout'] in (False, 'False')


def main():
    import joblib
    import numpy as np
    import pandas as pd
    import sklearn
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    root = Path('data/processed/training_v1')
    source = root / 'research_samples.csv'
    frame = pd.read_csv(source)
    models = Path('data/models/research_v1')
    models.mkdir(parents=True, exist_ok=True)
    reports, predictions = [], []
    for crop in ('soy', 'maize2'):
        cohort = frame[frame.crop == crop]
        train = cohort[cohort.apply(training_mask, axis=1)]
        assert train.harvest_year.max() <= 2018 and not train.spatial_holdout.any()
        candidates = {
            'ridge': make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), Ridge(alpha=10)),
            'hist_gradient_boosting': make_pipeline(SimpleImputer(strategy='median'), HistGradientBoostingRegressor(
                max_iter=100, max_leaf_nodes=7, min_samples_leaf=15, l2_regularization=10, random_state=42))}
        for name, model in candidates.items():
            model.fit(train[FEATURES], train.yield_kg_ha)
            joblib.dump(model, models / f'{crop}_{name}.joblib')
        for scope, evaluation in [
            ('temporal_validation', cohort[(cohort.split_time=='validation') & ~cohort.spatial_holdout]),
            ('spatiotemporal_validation', cohort[(cohort.split_time=='validation') & cohort.spatial_holdout])]:
            if evaluation.empty:
                continue
            all_predictions = {name: model.predict(evaluation[FEATURES]) for name, model in candidates.items()}
            all_predictions['lagged_mean'] = evaluation.historical_yield_lag2to4_mean.fillna(train.yield_kg_ha.mean()).to_numpy()
            for name, predicted in all_predictions.items():
                reports.append({'crop': crop, 'scope': scope, 'model': name, 'train_n': len(train),
                                'evaluation_n': len(evaluation),
                                'mae_kg_ha': float(mean_absolute_error(evaluation.yield_kg_ha, predicted)),
                                'rmse_kg_ha': float(np.sqrt(mean_squared_error(evaluation.yield_kg_ha, predicted))),
                                'bias_kg_ha': float(np.mean(predicted-evaluation.yield_kg_ha.to_numpy()))})
                for (_, row), value in zip(evaluation.iterrows(), predicted):
                    predictions.append({'sample_id': row.sample_id, 'scope': scope, 'model': name,
                                        'actual_kg_ha': row.yield_kg_ha, 'predicted_kg_ha': float(value)})
    pd.DataFrame(reports).to_csv(root / 'baseline_validation.csv', index=False)
    pd.DataFrame(predictions).to_csv(root / 'baseline_predictions_validation.csv', index=False)
    manifest = {'dataset_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'features': FEATURES,
                'test_evaluated': False, 'test_years': [2022, 2023, 2024],
                'spatial_holdout': ['3170107', '5107909'], 'sklearn': sklearn.__version__,
                'numpy': np.__version__, 'pandas': pd.__version__, 'production_ready': False,
                'models': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in models.glob('*.joblib')},
                'limitations': ['municipal_point_weather_proxy', 'revised_historical_data',
                                'fixed_season_window_not_observed_planting', 'small_sample_size',
                                'IMERG_Final_operational_latency_not_reconstructed',
                                'validation_errors_are_not_field_accuracy']}
    (models / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(pd.DataFrame(reports).to_string(index=False))


if __name__ == '__main__':
    main()
