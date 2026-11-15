"""Single opening of C1 (crop year 2025). The only module allowed to read sealed_confirmation_2025."""
from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/processed/us_training_v2'
SEALED = ROOT / 'data/raw/us_training_v2/sealed_confirmation_2025.jsonl.gz'
CONSUMED = OUT / 'c1_consumed.json'
SCORE = OUT / 'c1_score.json'
FAMILY = 53
REPS, SEED, GAIN_MIN, MIN_PAIRS = 2000, 42, 0.05, 30
SCOPES = ['IA_soy', 'IA_maize', 'NE_soy', 'NE_maize', 'KS_wheat_winter']


def sha(path):
    import hashlib
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def county_mae(err: pd.DataFrame, col: str) -> float:
    return float(err.groupby('fips')[col].mean().mean())


def paired_bootstrap(frame: pd.DataFrame, cand: str, comp: str, reps=REPS, seed=SEED, family=FAMILY) -> dict:
    """frame has fips, y, cand, comp columns; county-cluster paired bootstrap of MAE(cand) - MAE(comp)."""
    per = frame.assign(dc=(frame[cand] - frame.y).abs(), dp=(frame[comp] - frame.y).abs()).groupby('fips')[['dc', 'dp']].mean()
    diff = (per.dc - per.dp).to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diff), size=(reps, len(diff)))
    boots = diff[idx].mean(axis=1)
    q = 1 - 0.05 / (2 * family)
    return {'mae_candidate': float(per.dc.mean()), 'mae_comparator': float(per.dp.mean()), 'diff': float(diff.mean()),
            'ci95': [float(np.quantile(boots, .025)), float(np.quantile(boots, .975))], 'upper_adjusted': float(np.quantile(boots, q)),
            'gain_pct': float((per.dp.mean() - per.dc.mean()) / per.dp.mean()) if per.dp.mean() > 0 else None, 'counties': int(len(diff))}


def status(stats: dict | None, spatial_gain: float | None, spatial_pairs: int, candidate_is_reference: bool, executed: bool) -> str:
    if not executed:
        return 'not_executed'
    if candidate_is_reference:
        return 'reference_selected'
    if stats is None or stats['counties'] < MIN_PAIRS or spatial_pairs < MIN_PAIRS:
        return 'underpowered'
    ok = stats['gain_pct'] is not None and stats['gain_pct'] >= GAIN_MIN and stats['upper_adjusted'] < 0 and spatial_gain is not None and spatial_gain >= 0
    return 'research_confirmed' if ok else 'gain_not_confirmed'


def read_sealed() -> pd.DataFrame:
    rows = [json.loads(x) for x in gzip.open(SEALED, 'rt', encoding='utf-8')]
    f = pd.DataFrame(rows)
    f = f[(f.statistic == 'YIELD') & (f.practice == 'ALL PRODUCTION PRACTICES')]
    if f.duplicated(['fips', 'crop']).any():
        raise ValueError('duplicate 2025 keys')
    return f[['fips', 'crop', 'yield_kg_ha', 'value_raw']]


def open_c1(comparisons: list[dict], dev_spatial: dict) -> dict:
    """comparisons: id, family, scope, cand_file, cand_col, comp_file, comp_col, keys ('seen'|'v1_seen'), issue_kind, candidate_is_reference, executed."""
    all_frozen = OUT / 'all_frozen.json'
    if not all_frozen.exists():
        raise ValueError('freeze everything before opening C1')
    manifest = json.loads(all_frozen.read_text())
    bad = [p for p, h in manifest['files'].items() if (ROOT / p).exists() and sha(ROOT / p) != h]
    if bad:
        raise ValueError(f'frozen files changed: {bad[:5]}')
    with CONSUMED.open('x', encoding='utf-8') as f:
        json.dump({'opened_at': datetime.now(timezone.utc).isoformat(), 'all_frozen_sha256': sha(all_frozen),
                   'rule': 'no refit/reselection presented as independent after this point'}, f, indent=1)
    labels = read_sealed()
    results = []
    for c in comparisons:
        res = {k: c[k] for k in ('id', 'family', 'scope', 'issue_kind', 'candidate', 'comparator')}
        if not c['executed']:
            res |= {'status': 'not_executed'}
            results.append(res)
            continue
        a = pd.read_csv(ROOT / c['cand_file'], dtype={'fips': str})
        b = pd.read_csv(ROOT / c['comp_file'], dtype={'fips': str})
        if 'in_v1_population' not in a:
            a['in_v1_population'] = True  # chain files carry no population flag; only 'seen' keys are used for them
        a = a[a.issue_kind == c['issue_kind']][['fips', 'crop', 'spatial_holdout', 'in_v1_population', c['cand_col']]].rename(columns={c['cand_col']: 'cand'})
        b = b[b.issue_kind == c['issue_kind']][['fips', 'crop', c['comp_col']]].rename(columns={c['comp_col']: 'comp'})
        m = a.merge(b, on=['fips', 'crop']).merge(labels, on=['fips', 'crop'], how='left')
        keys = m[~m.spatial_holdout]
        if c['keys'] == 'v1_seen':
            keys = keys[keys.in_v1_population]
        observed = keys[keys.yield_kg_ha.notna()].rename(columns={'yield_kg_ha': 'y'})
        hold = m[m.spatial_holdout & m.yield_kg_ha.notna()].rename(columns={'yield_kg_ha': 'y'})
        stats = paired_bootstrap(observed, 'cand', 'comp') if len(observed) else None
        sp = dev_spatial.get(c['id'], {})
        res |= {'stats_seen': stats, 'predicted_seen': int(len(keys)), 'observed_seen': int(len(observed)), 'missing_seen': int(len(keys) - len(observed)),
                'holdout_c1_descriptive': paired_bootstrap(hold, 'cand', 'comp') if len(hold) else None, 'holdout_c1_pairs': int(len(hold)),
                'dev_spatial_gain_pct': sp.get('gain'), 'dev_spatial_pairs': sp.get('pairs', 0),
                'status': status(stats, sp.get('gain'), sp.get('pairs', 0), c['candidate_is_reference'], True),
                'exposure_qualified': c['scope'].startswith('IA_')}
        res['by_state_metrics'] = metrics(observed)
        results.append(res)
    out = {'scored_at': datetime.now(timezone.utc).isoformat(), 'family_size': FAMILY, 'evaluation_class': 'retrospective_estimated_availability',
           'exposure_note': 'IA 2025: two county values seen in search snippets before v1; IA results exposure_qualified', 'results': results,
           'labels_sha256': sha(SEALED), 'all_frozen_sha256': sha(all_frozen),
           'coverage': labels.assign(has_value=labels.yield_kg_ha.notna()).groupby('crop')['has_value'].agg(['sum', 'count']).reset_index().to_dict('records')}
    with SCORE.open('x', encoding='utf-8') as f:
        json.dump(out, f, indent=1, default=float)
    return out


def released_labels_after_opening() -> pd.DataFrame:
    """2025 labels for post-confirmation product refits; refuses before the single C1 opening."""
    if not (CONSUMED.exists() and SCORE.exists()):
        raise ValueError('C1 not opened and scored yet; 2025 labels stay sealed')
    f = read_sealed()
    return f.assign(year=2025)


def metrics(frame: pd.DataFrame) -> dict | None:
    if frame.empty:
        return None
    e = frame.cand - frame.y
    return {'mae': float(e.abs().mean()), 'rmse': float(np.sqrt((e ** 2).mean())), 'bias': float(e.mean()),
            'mae_over_mean': float(e.abs().mean() / frame.y.mean()),
            'r2': float(1 - (e ** 2).sum() / ((frame.y - frame.y.mean()) ** 2).sum()) if frame.y.nunique() > 1 else None}
