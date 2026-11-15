"""Smoke test of the Gaiapus integration package.

    python smoke_test.py          # offline: models, catalog and contracts
    python smoke_test.py --live   # also one live estimate and one water projection (network + EARTHDATA_TOKEN)
"""
import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def check(name, ok, detail=''):
    print(f'[{"OK" if ok else "FALHOU"}] {name} {detail}')
    return ok


def offline():
    from jsonschema import Draft202012Validator, FormatChecker
    from data.serving.yield_model import YieldModel
    results = []
    catalog = json.loads((ROOT / 'dist/gaiapus-models-v7/catalog.json').read_text(encoding='utf-8'))
    results.append(check('catálogo', len(catalog['crops']) == 10, f'{len(catalog["crops"])} culturas'))
    for crop, item in catalog['crops'].items():
        for scope in item['models']:
            model = YieldModel(ROOT / 'dist/gaiapus-models-v7/models' / scope['artifact'])
            cutoff = model.cutoffs[len(model.cutoffs) // 2]
            ranges = scope['training_ranges'][str(cutoff)]
            # Every required input; phases not reached at the cutoff (e.g. maturation) stay empty, as in real rows.
            row = {f: ((ranges[f][0] + ranges[f][1]) / 2 if f in ranges else None) for f in model.required_features(cutoff)}
            row.update(horizon_days=cutoff, harvest_year=2020)
            value = model.predict(row)
            results.append(check(f'modelo {crop} {scope["scope"]} {scope["model"]}', math.isfinite(value), f'{value:.0f} kg/ha'))
    schemas = {'yield-error': 'yield-error.schema.json', 'yield': 'yield-estimate.schema.json', 'water': 'water-projection.schema.json',
               'field-shift': 'field-shift.schema.json'}
    for path in sorted((ROOT / 'contracts/examples').glob('*.json')):
        schema = next(v for k, v in schemas.items() if path.name.startswith(k))
        validator = Draft202012Validator(json.loads((ROOT / 'contracts' / schema).read_text(encoding='utf-8')), format_checker=FormatChecker())
        errors = list(validator.iter_errors(json.loads(path.read_text(encoding='utf-8'))))
        results.append(check(f'contrato {path.name}', not errors, errors[0].message if errors else ''))
    return all(results)


def live():
    import os
    from data.serving.build_examples import water_example
    from data.serving.providers import Live
    from data.serving.yield_service import estimate_yield
    if not os.environ.get('EARTHDATA_TOKEN'):
        return check('EARTHDATA_TOKEN definido', False, '(necessário para a chuva IMERG Late)')
    catalog = json.loads((ROOT / 'dist/gaiapus-models-v7/catalog.json').read_text(encoding='utf-8'))
    provider = Live(ROOT / 'data/cache/live', mode='operational')
    answer = estimate_yield('soy', '5107925', date.today(), catalog=catalog, models_dir=ROOT / 'dist/gaiapus-models-v7/models',
                            get_weather=provider.weather, get_history=provider.history, mode='operational')
    ok = check('rendimento ao vivo soja Sorriso', answer.get('status') in ('ready', 'before_first_cutoff', 'outside_season'),
               f'status={answer.get("status")} estimativa={answer.get("estimate_kg_ha")} dados até={answer.get("observed_through")}')
    name, status, served, issued = water_example(code='5107925', crop='soy', horizon=7)
    ok &= check('projeção hídrica ao vivo soja Sorriso', status in ('ready', 'partially_validated', 'not_supported'),
                f'status={status} dias={served} emissão={issued}')
    return ok


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    passed = offline() and (live() if args.live else True)
    print('\nRESULTADO:', 'tudo certo' if passed else 'há falhas acima')
    sys.exit(0 if passed else 1)
