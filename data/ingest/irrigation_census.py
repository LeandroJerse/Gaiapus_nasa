"""Center-pivot and total irrigated area per municipality from Censo Agropecuário 2017 (SIDRA 6857).

Infrastructure only: a pivot area says equipment existed in the 2016/17 reference year,
not which crop was irrigated in which season or how much water was applied. Used here to
examine the second-crop maize error in Unaí and Paracatu against the other MG/MT
municipalities of the v6 cohort.
"""
import json

from data.ingest import cohort_v5 as v5
from data.ingest.water_balance_run import ROOT, save_json, write_csv

RAW = ROOT / 'data/raw/irrigation_census_2017'
OUT = ROOT / 'data/processed/irrigation_census_2017'
CLASSES = 'c829/46302/c12604/118477,45923/c12564/41145/c12771/45951/c309/10969'


def fetch_state(state):
    url = f'https://apisidra.ibge.gov.br/values/t/6857/n6/in%20n3%20{state}/v/2373/p/2017/{CLASSES}'
    return v5.fetch(f'irrigated_area_{state}.json', url, root=RAW)


def parse(payload):
    out = {}
    for row in payload[1:]:
        code, method, value = row['D1C'], row['D5C'], row['V']
        key = 'pivot_area_ha' if method == '45923' else 'irrigated_area_ha'
        out.setdefault(code, {'municipality_code': code, 'municipality': row['D1N'], 'pivot_area_ha': None, 'irrigated_area_ha': None})
        out[code][key] = None if value in v5.MISSING else (0.0 if value == '0' else float(value))
    return out


def main():
    import pandas as pd
    areas = {}
    for state in ('31', '51'):
        areas.update(parse(fetch_state(state)))
    census = pd.DataFrame(areas.values())
    samples = pd.read_parquet(ROOT / 'data/processed/training_v6/samples.parquet')
    maize = samples[(samples.crop == 'maize2') & samples.state_code.isin(['31', '51']) & (samples.horizon_days == 90)]
    labels = maize.groupby('municipality_code').agg(harvests=('sample_id', 'nunique'), mean_yield=('yield_kg_ha', 'mean')).reset_index()
    area = {}
    for state in ('31', '51'):
        for row in json.loads((ROOT / f'data/raw/training_v6/pam_maize2_{state}.json').read_bytes())[1:]:
            if row['D2C'] == '216' and row['D3C'] == '2017' and row['V'] not in v5.MISSING:
                area[row['D1C']] = float(row['V'])
    labels['maize2_area_2017_ha'] = labels.municipality_code.map(area)
    table = labels.merge(census, on='municipality_code', how='left')
    table['pivot_share_of_maize2_area'] = table.pivot_area_ha / table.maize2_area_2017_ha
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / 'mg_mt_maize2_irrigation.csv', table.to_dict('records'))
    focus = table[table.municipality_code.isin(['3170404', '3147006', '3170206', '3170107', '5107925', '5107909', '5105259', '5102637'])]
    summary = {'source': 'IBGE Censo Agropecuário 2017, SIDRA 6857, variable 2373 (área irrigada), methods total and center pivot',
               'reference_year': 2017, 'meaning': 'equipment present in 2016/17; not crop-specific irrigation or water applied',
               'municipalities': len(table), 'mvp': focus.to_dict('records'),
               'pivot_share_quartiles_all_mg_mt': table.pivot_share_of_maize2_area.describe().round(3).to_dict()}
    save_json(OUT / 'summary.json', summary)
    print(focus[['municipality', 'harvests', 'mean_yield', 'maize2_area_2017_ha', 'pivot_area_ha', 'irrigated_area_ha', 'pivot_share_of_maize2_area']].round(2).to_string(index=False))


if __name__ == '__main__':
    main()
