"""Registered v8 side experiments (protocol 'registered_experiments'); development folds only.

irrigation     : maize2 with the Censo 2017 pivot attribute, folds 2020-2021 (after its publication)
learning_curve : soy MG+MT, the configuration selected in each fold refitted with fewer training years / municipalities
loss           : soy MG+MT, the selected MLP configuration with MSE instead of smooth_l1
"""
import argparse
import hashlib
import json

from data.ingest import gpu
from data.ingest.cohort_v8 import load_development
from data.ingest.train_v8 import OUT, fit_predict, fold_split, harvest_scores, run_scope
from data.ingest.water_balance_run import write_csv

MGMT = lambda f: f.state_code.isin(['31', '51'])


def irrigation(device):
    from data.ingest.maize_irrigation_v8 import attach, extra_sets
    for name in ('maize2_mgmt', 'maize2_national'):
        run_scope(name, device, pools=('national',), folds=(2020, 2021), extra_sets=extra_sets, tag=f'{name}_irrigation', attach=attach)


def chosen(name, pool, year):
    import pandas as pd
    selection = pd.read_csv(OUT / name / 'selection.csv')
    row = selection[(selection.pool == pool) & (selection.fold == year)].iloc[0].to_dict()
    row['params'] = json.loads(row['params']) if isinstance(row['params'], str) and row['params'] not in ('', 'null') else None
    if row['learner'] == 'ridge' and row['params'] is not None:
        row['params'] = float(row['params'])
    row['shrink'] = float(row['shrink'])
    return row


def learning_curve(device):
    frame = load_development('soy')
    rows = []
    for year in (2019, 2020, 2021):
        choice = chosen('soy_mgmt', 'national', year)
        train, target = fold_split(frame, frame, year, MGMT)
        known = target[~target.spatial_holdout]
        years = sorted(train.harvest_year.unique())
        for last in (5, 8, 11, len(years)):
            part = train[train.harvest_year.isin(years[-last:])]
            pred, _ = fit_predict(choice, part, known, 'annual', device, seeds=(101, 102, 103))
            rows.append({'fold': year, 'dimension': 'training_years', 'value': last, 'train_harvests': int(part.sample_id.nunique()),
                         'mae_kg_ha': harvest_scores(known, pred, 'state50')})
        for fraction in (0.25, 0.5, 1.0):
            keep = {c for c in train.municipality_code.unique() if int(hashlib.sha256(c.encode()).hexdigest(), 16) % 100 < fraction * 100}
            part = train[train.municipality_code.isin(keep)]
            pred, _ = fit_predict(choice, part, known, 'annual', device, seeds=(101, 102, 103))
            rows.append({'fold': year, 'dimension': 'municipality_fraction', 'value': fraction, 'train_harvests': int(part.sample_id.nunique()),
                         'mae_kg_ha': harvest_scores(known, pred, 'state50')})
        print('learning curve', year, rows[-1], flush=True)
    write_csv(OUT / 'soy_mgmt' / 'learning_curve.csv', rows)


def loss(device):
    import pandas as pd
    frame = load_development('soy')
    rows = []
    for year in (2019, 2020, 2021):
        choice = chosen('soy_mgmt', 'national', year)
        if choice['learner'] != 'mlp':
            top = pd.read_csv(OUT / 'soy_mgmt' / 'inner_top15.csv')
            top = top[(top.pool == 'national') & (top.fold == year) & (top.learner == 'mlp')].sort_values('score')
            if top.empty:
                continue
            choice = {**top.iloc[0].to_dict(), 'params': json.loads(top.iloc[0].params), 'shrink': float(top.iloc[0].shrink)}
        train, target = fold_split(frame, frame, year, MGMT)
        known = target[~target.spatial_holdout]
        for name in ('smooth_l1', 'mse'):
            pred, _ = fit_predict(choice, train, known, 'annual', device, loss=None if name == 'smooth_l1' else 'mse')
            rows.append({'fold': year, 'loss': name, 'config': json.dumps(choice['params']), 'mae_kg_ha': harvest_scores(known, pred, 'state50'),
                         'extreme_harvest_mae_kg_ha': harvest_scores(known[known.yield_kg_ha <= known.yield_kg_ha.quantile(0.1)],
                                                                      pred[(known.yield_kg_ha <= known.yield_kg_ha.quantile(0.1)).to_numpy()], 'state50')})
        print('loss', year, rows[-2:], flush=True)
    write_csv(OUT / 'soy_mgmt' / 'loss_experiment.csv', rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiments', default='irrigation,learning_curve,loss')
    args = parser.parse_args()
    device, _ = gpu.require_cuda()
    gpu.training_step_check(device)
    for name in args.experiments.split(','):
        globals()[name](device)


if __name__ == '__main__':
    from data.ingest.experiments_v8 import main as entry
    entry()
