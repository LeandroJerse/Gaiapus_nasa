"""US v2 county universe and spatial holdout, from development labels only (crop years <= 2024)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from data.ingest.us_v2_data import load_yields
from data.ingest.us_v2_features import label_available_at

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/processed/us_training_v2'
V1_COUNTIES = ROOT / 'data/processed/us_training_v1/counties.json'
CROPS = {'IA': ['soy', 'maize'], 'NE': ['soy', 'maize'], 'KS': ['wheat_winter']}
ANCHORS = {'IA': ['19169', '19015', '19127'], 'NE': ['31185', '31081', '31047'], 'KS': ['20191', '20113']}
MIN_OBS, MIN_RECENT, RECENT_PUBLISHED = 12, 3, 5
HOLDOUT_FRACTION = 0.2


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def recent_published_years(crop: str, last_year: int = 2024, n: int = RECENT_PUBLISHED) -> list[int]:
    years, y = [], last_year
    while len(years) < n and y > 1990:
        at, _ = label_available_at(crop, y)
        if at is not None:
            years.append(y)
        y -= 1
    return sorted(years)


def select(yields: pd.DataFrame) -> list[dict]:
    if (yields.year > 2024).any():
        raise ValueError('confirmation labels must not inform county selection')
    valid = yields[yields.yield_kg_ha.notna()]
    v1 = {c['fips'] for c in json.loads(V1_COUNTIES.read_text())}
    result = []
    for state, crops in CROPS.items():
        sets, rates = [], {}
        for crop in crops:
            part = valid[(valid.state == state) & (valid.crop == crop)]
            n_obs = part[(part.year >= 2001)].groupby('fips').year.nunique()
            recent = recent_published_years(crop)
            n_recent = part[part.year.isin(recent)].groupby('fips').year.nunique()
            ok = set(n_obs[n_obs >= MIN_OBS].index) & set(n_recent[n_recent >= MIN_RECENT].index)
            sets.append(ok)
            rates[crop] = (n_recent / len(recent)).to_dict()
        eligible = sorted(set.intersection(*sets))
        non_anchor = sorted((f for f in eligible if f not in ANCHORS[state]), key=lambda f: _h(f'us_training_v2:spatial:{f}'))
        holdout = set(non_anchor[:round(HOLDOUT_FRACTION * len(eligible))])
        for f in eligible:
            result.append({'fips': f, 'state': state, 'spatial_holdout': f in holdout, 'in_v1_population': f in v1,
                           'recent_publication_rate': {c: round(rates[c].get(f, 0.0), 4) for c in crops}})
    return result


def expected_pairs(counties: list[dict], years: int = 1) -> dict:
    out = {}
    for state, crops in CROPS.items():
        for crop in crops:
            scope = f'{state}_{crop}'
            rows = [c for c in counties if c['state'] == state]
            out[scope] = {'seen': round(years * sum(c['recent_publication_rate'][crop] for c in rows if not c['spatial_holdout']), 1),
                          'spatial': round(years * sum(c['recent_publication_rate'][crop] for c in rows if c['spatial_holdout']), 1)}
    return out


def main() -> None:
    counties = select(load_yields())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'counties_v2.json').write_text(json.dumps(counties, indent=1), encoding='utf-8')
    summary = {s: {'eligible': sum(c['state'] == s for c in counties), 'holdout': sum(c['state'] == s and c['spatial_holdout'] for c in counties),
                   'in_v1': sum(c['state'] == s and c['in_v1_population'] for c in counties)} for s in CROPS}
    print(json.dumps({'by_state': summary, 'expected_2025_pairs': expected_pairs(counties)}, indent=1))


if __name__ == '__main__':
    main()
