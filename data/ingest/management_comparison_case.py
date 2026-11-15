"""Management comparison with real inputs: soy -> second-crop maize in Sorriso (MT), rainfed against irrigated maize.

Real inputs, each with its limit:
- Weather: NASA POWER daily point of Sorriso, 2001-2024 (cached), replayed as historical seasons. Past seasons, not a
  forecast and not a calibrated probability of the future.
- Yield: mean of the last five IBGE PAM harvests of the municipality. A municipal reference for a budget, not a
  forecast and not a response to management: both managements get the same value.
- Price and cost: Conab producer price and Conab operational cost panel of the 2023/24 season. A dated historical
  reference, not a current quotation and not a price forecast. The Conab panel has no irrigation cost, so the
  irrigated alternative is a partial budget (water cost not informed) and is labelled as such.
Hypotheses: soil AD4 with 55 mm, initial reserve 0.55 of capacity, fixed sowing dates, 20 mm net at 60 and 75 days
after maize sowing with 80 % efficiency. The irrigation events are an example to edit, not a recommendation.

Output: data/processed/management_comparison_v1/<comparison_id>/ with request.json, result.json and manifest.json.
The directory is never overwritten.
"""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone

from data.ingest.water_balance_run import ROOT, save_json
from data.models.crop_simulation import service as svc

CODE = '5107925'
SEASONS = range(2005, 2023)
OUT = ROOT / 'data/processed/management_comparison_v1'
MARGINS = ROOT / 'data/processed/margins'
ROTATION = [{'crop_id': 'soy', 'sowing_rule': '10-05', 'year_offset': 0}, {'crop_id': 'maize2', 'sowing_rule': '01-28', 'year_offset': 1}]
WEATHER_SOURCE = 'NASA POWER daily point (IMERG rain), retrospective; historical seasons replayed'


def _rows(name):
    with (MARGINS / name).open(encoding='utf-8-sig', newline='') as stream:
        return [r for r in csv.DictReader(stream) if r['municipality_code'] == CODE]


def pam_reference(crop, last_year=2024, n=5):
    rows = sorted((r for r in _rows('pam_history.csv') if r['crop'] == crop and r['yield_kg_ha'] and int(r['year']) <= last_year), key=lambda r: int(r['year']))[-n:]
    if len(rows) < n:
        raise SystemExit(f'fewer than {n} PAM harvests for {crop}')
    values = [float(r['yield_kg_ha']) for r in rows]
    return {'origin': 'historical_reference', 'kg_ha': round(sum(values) / n, 1), 'reference': 'IBGE PAM, municipal mean yield', 'validation_status': 'reference_not_a_forecast',
            'period': f'{rows[0]["year"]}-{rows[-1]["year"]} harvests (mean of {n})', 'scale': 'municipality'}


def conab_budget(crop, **extra):
    row = next(r for r in _rows('inputs.csv') if r['crop'] == crop)
    return {'currency': 'BRL', 'price_per_unit': float(row['price_brl_60kg']), 'price_unit': 'bag', 'unit_kg': 60.0,
            'price_reference': f'{row["price_basis"]} ({row["price_url"]})', 'price_date': row['quote_end'] or row['harvest_year'],
            'cost_per_ha': float(row['cost_operational_brl_ha']),
            'cost_basis': f'Conab operational cost panel, {row["cost_safra"]} ({row["cost_reference"]}); historical reference, not a current budget', **extra}


def build_request():
    irrigated_budget = conab_budget('maize2', costs_complete=False)
    irrigated_budget['cost_basis'] += '; irrigation water and energy cost not informed'
    return {'contract': svc.REQUEST_CONTRACT, 'location_id': f'BR:ibge:{CODE}', 'parameter_set_id': 'gaiapus-fao56-br-v1', 'soil': {'profile_id': 'AD4-hypothesis', 'capacity_mm': 55.0},
            'initial_storage_fraction': 0.55, 'reference_id': 'sequeiro', 'area_ha': None,
            'alternatives': [{'alternative_id': 'sequeiro', 'name': 'Rainfed', 'crops': ROTATION, 'hypotheses': ['fixed sowing dates of the demonstration']},
                             {'alternative_id': 'irrigado', 'name': 'Irrigated maize (example)', 'crops': ROTATION, 'irrigation_efficiency': 0.8,
                              'irrigation': [{'crop_id': 'maize2', 'days_after_sowing': 60, 'net_mm': 20.0}, {'crop_id': 'maize2', 'days_after_sowing': 75, 'net_mm': 20.0}],
                              'hypotheses': ['two 20 mm net irrigations at 60 and 75 days after maize sowing, 80 % efficiency: an example to edit, not a recommendation']}],
            'yield_sources': {'common': {'soy': pam_reference('soy'), 'maize2': pam_reference('maize2')}},
            'budgets': {'common': {'soy': conab_budget('soy'), 'maize2': conab_budget('maize2')}, 'by_alternative': {'irrigado': {'maize2': irrigated_budget}}}}


def load_members():
    from data.ingest import cohort_v5 as v5
    from data.ingest.cohort_v6 import RAW
    weather, _ = v5.daily_weather(CODE, root=RAW)
    return svc.members_from_daily(weather, SEASONS, start_month_day='09-01', days=365)


def run():
    request = build_request()
    return request, svc.run_request(request, load_members(), forcing_source=WEATHER_SOURCE)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    request, result = run()
    out = OUT / result['comparison_id']
    if out.exists():
        raise SystemExit(f'{out} already exists; packages are immutable')
    out.mkdir(parents=True)
    save_json(out / 'request.json', request); save_json(out / 'result.json', result)
    files = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in sorted(out.iterdir())}
    save_json(out / 'manifest.json', {'comparison_id': result['comparison_id'], 'identity': result['identity'], 'generated_at': datetime.now(timezone.utc).isoformat(), 'files': files})
    print(result['comparison_id'], result['coverage'])
    for name, alt in result['alternatives'].items():
        print(name, {c: (h['yield']['mean_kg_ha'], h['money']['margin_per_ha'], h['margin_kind']) for c, h in alt['harvests'].items()},
              {k: round(alt['water'][k]['mean'], 1) for k in ('maize2.deficit_mm', 'soy.deficit_mm', 'irrigation_gross_mm', 'drainage_mm')})
    print(json.dumps({k: round(v['mean'], 2) for k, v in result['differences']['irrigado']['water'].items() if k.startswith('maize2')}))


if __name__ == '__main__':
    from data.ingest.management_comparison_case import main as entry
    entry()
