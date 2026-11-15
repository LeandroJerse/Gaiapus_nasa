"""Irrigation infrastructure attribute for second-crop maize (registered v8 experiment).

Source: IBGE Censo Agropecuário 2017, SIDRA 6857 (irrigated area, total and center pivot),
reference year 2016/17, definitive results published on 2019-10-25. It describes equipment
in 2017, not irrigation of maize in each season; usable only for decisions after its
publication (v8 folds 2020 and 2021). Missing census values stay missing (not zero).
"""
from datetime import date

from data.ingest.irrigation_census import fetch_state, parse
from data.ingest.water_balance_run import ROOT

PUBLISHED = date(2019, 10, 25)
STATES = ('29', '31', '35', '41', '50', '51', '52', '53')
FEATURES = ['pivot_share_of_maize2_area', 'irrigated_share_of_maize2_area']


def attribute_table():
    import json
    import pandas as pd
    from data.ingest import cohort_v5 as v5
    census = {}
    for state in STATES:
        census.update(parse(fetch_state(state)))
    area = {}
    for state in STATES:
        path = ROOT / f'data/raw/training_v6/pam_maize2_{state}.json'
        if not path.exists():
            continue
        for row in json.loads(path.read_bytes())[1:]:
            if row['D2C'] == '216' and 2003 <= int(row['D3C']) <= 2016 and row['V'] not in v5.MISSING:
                area.setdefault(row['D1C'], []).append(float(row['V']))
    rows = []
    for code, values in census.items():
        mean_area = sum(area.get(code, [])) / len(area[code]) if area.get(code) else None
        rows.append({'municipality_code': code,
                     'pivot_share_of_maize2_area': (values['pivot_area_ha'] / mean_area) if mean_area and values['pivot_area_ha'] is not None else None,
                     'irrigated_share_of_maize2_area': (values['irrigated_area_ha'] / mean_area) if mean_area and values['irrigated_area_ha'] is not None else None})
    return pd.DataFrame(rows)


def attach(frame):
    table = attribute_table()
    merged = frame.merge(table, on='municipality_code', how='left', validate='many_to_one')
    return merged


def extra_sets(year):
    from data.ingest.train_v8 import feature_sets
    if date(year, 1, 5) <= PUBLISHED:  # second-crop maize is sown in January: decisions before publication cannot use it
        return {}
    _, sets = feature_sets('annual')
    return {'F4_irrigation': [*sets['F3_full'], *FEATURES]}
