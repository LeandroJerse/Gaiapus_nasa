"""Crop x region matrix from the v7 validation runs: coverage, error by model and publication status."""
import json

from data.ingest.water_balance_run import ROOT, save_json, write_csv

RUNS = ROOT / 'data/processed/training_v7/validation'
OUT = ROOT / 'data/processed/training_v7'
NOT_RELEASED = {'operational_release': 'not_released',
                'release_blockers': 'input and label publication vintages unverified; sealed 2022-2024 test not yet run'}
STATE = {'11': 'RO', '15': 'PA', '17': 'TO', '21': 'MA', '22': 'PI', '29': 'BA', '31': 'MG', '32': 'ES', '35': 'SP', '41': 'PR',
         '42': 'SC', '43': 'RS', '50': 'MS', '51': 'MT', '52': 'GO', '53': 'DF'}


def best_learned(metrics, scope, models=('ridge', 'hgb', 'mlp')):
    rows = metrics[(metrics.scope == scope) & (metrics.breakdown == 'all') & metrics.model.isin(models)]
    return rows.sort_values('mae_kg_ha').iloc[0] if len(rows) else None


def main():
    import pandas as pd
    crops, regions, phases = [], [], []
    for folder in sorted(p for p in RUNS.iterdir() if p.is_dir()):
        info = json.loads((folder / 'cohort.json').read_text(encoding='utf-8'))
        name = info['cohort']
        base = {'cohort': name, 'harvests': info['harvests'], 'municipalities': info['municipalities'],
                'states': '/'.join(STATE.get(s, s) for s in info['states']), 'training_municipalities': info['training_municipalities'],
                'held_out_municipalities': info['held_out_municipalities'], 'years': f"{info['years'][0]}-{info['years'][1]}",
                'cutoffs': '/'.join(map(str, info['cutoffs']))}
        if not info.get('evaluable') or not (folder / 'metrics.csv').exists():
            crops.append({**base, 'validation_status': 'unavailable', 'validation_choice': None, **NOT_RELEASED})
            continue
        metrics = pd.read_csv(folder / 'metrics.csv')
        overall = metrics[metrics.breakdown == 'all']
        pick = lambda sources, model, scope: overall[(overall.sources == sources) & (overall.model == model) & (overall.scope == scope)]
        champion_sources, champion_model = info['champion'].split('+')
        history = pick('history', 'history', 'temporal').iloc[0]
        champion_row = pick(champion_sources, champion_model, 'temporal').iloc[0]
        mlp = metrics[(metrics.breakdown == 'all') & (metrics.model == 'mlp') & (metrics.scope == 'temporal')].sort_values('mae_kg_ha').iloc[0]
        ridge = best_learned(metrics, 'temporal', ('ridge',))
        hgb = best_learned(metrics, 'temporal', ('hgb',))
        refs = overall[(overall.model == 'reference') & (overall.scope == 'temporal')].sort_values('mae_kg_ha')
        crops.append({**base, 'validation_harvests_temporal': int(history.label_families),
                      'validation_harvests_spatial': int(pick('history', 'history', 'spatiotemporal').iloc[0].label_families),
                      'mean_yield_kg_ha': round(info['mean_yield']),
                      'best_reference': refs.iloc[0].sources if len(refs) else None,
                      'best_reference_mae': round(refs.iloc[0].mae_kg_ha, 1) if len(refs) else None,
                      'history_mae': round(history.mae_kg_ha, 1), 'ridge_mae': round(ridge.mae_kg_ha, 1), 'hgb_mae': round(hgb.mae_kg_ha, 1),
                      'mlp_mae': round(mlp.mae_kg_ha, 1), 'mlp_sources': mlp.sources, 'mlp_seed_win_share': info['best_mlp_seed_win_share'],
                      'validation_choice': info['champion'], 'recommended_mae': round(champion_row.mae_kg_ha, 1),
                      'recommended_mae_pct': round(100 * champion_row.mae_kg_ha / info['mean_yield'], 1),
                      'recommended_bias': round(champion_row.bias_kg_ha, 1),
                      'recommended_spatial_mae': round(info['champion_spatial_mae'], 1),
                      'gain_vs_history': round(info['gain_vs_history'], 1),
                      'gain_ci95': f"[{info['gain_ci95'][0]:.1f}, {info['gain_ci95'][1]:.1f}]", 'validation_status': {'published': 'validated'}.get(info['status'], info['status']), **NOT_RELEASED})
        by_state = metrics[(metrics.breakdown == 'state_code') & (metrics.scope == 'temporal')]
        for state, part in by_state.groupby('value'):
            state = str(state).zfill(2)
            row = part[(part.sources == champion_sources) & (part.model == champion_model)]
            hist = part[(part.sources == 'history') & (part.model == 'history')]
            best_mlp = part[part.model == 'mlp'].sort_values('mae_kg_ha').head(1)
            if row.empty:
                continue
            regions.append({'cohort': name, 'state': STATE.get(state, state), 'validation_harvests': int(row.iloc[0].label_families),
                            'municipalities': int(row.iloc[0].municipalities), 'history_mae': round(hist.iloc[0].mae_kg_ha, 1),
                            'recommended_mae': round(row.iloc[0].mae_kg_ha, 1), 'recommended_bias': round(row.iloc[0].bias_kg_ha, 1),
                            'best_mlp_mae': round(best_mlp.iloc[0].mae_kg_ha, 1) if len(best_mlp) else None,
                            'recommended_beats_history': bool(row.iloc[0].mae_kg_ha < hist.iloc[0].mae_kg_ha)})
        for column in ('milestone', 'high_year'):
            part = metrics[(metrics.breakdown == column) & (metrics.scope.isin(['temporal', 'spatiotemporal']))]
            for (scope, value), group in part.groupby(['scope', 'value']):
                keep = group[((group.sources == champion_sources) & (group.model == champion_model)) | (group.model.isin(['history', 'reference']))
                             | (group.model == 'mlp')]
                for _, r in keep.iterrows():
                    phases.append({'cohort': name, 'breakdown': column, 'value': value, 'scope': scope, 'model': f'{r.sources}+{r.model}',
                                   'harvests': int(r.label_families), 'mae_kg_ha': round(r.mae_kg_ha, 1), 'bias_kg_ha': round(r.bias_kg_ha, 1),
                                   'coverage': round(r.coverage, 3)})
    write_csv(OUT / 'crop_matrix.csv', crops)
    if regions:
        write_csv(OUT / 'crop_region_matrix.csv', regions)
    if phases:
        write_csv(OUT / 'crop_phase_matrix.csv', phases)
    save_json(OUT / 'crop_matrix.json', {'crops': crops, 'regions': regions})
    print(pd.DataFrame(crops).to_string(index=False))


if __name__ == '__main__':
    main()
