"""Minimal FastAPI example showing how a backend can serve the Gaiapus products.

    pip install fastapi uvicorn
    uvicorn example_api:app --reload

It reads the newest daily snapshot (dist/snapshots_live/latest.json). Only POST /v1/yield, used when
the producer reports a sowing date, computes on request. Adapt routes and storage to the real backend.
"""
import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / 'dist/gaiapus-models-v7'
SNAPSHOTS = Path(os.environ.get('GAIAPUS_SNAPSHOTS', ROOT / 'dist/snapshots_live'))
app = FastAPI(title='Gaiapus (exemplo)', version='1.1.0')
ERROR_STATUS = {'invalid_crop': 422, 'invalid_municipality_code': 422, 'municipality_not_covered': 404}


@lru_cache(maxsize=1)
def catalog():
    return json.loads((MODELS / 'catalog.json').read_text(encoding='utf-8'))


def latest_folder():
    pointer = SNAPSHOTS / 'latest.json'
    if not pointer.exists():
        raise HTTPException(503, 'No snapshot yet: run python -m data.serving.daily_job --mode live')
    return SNAPSHOTS / json.loads(pointer.read_text(encoding='utf-8'))['folder']


@lru_cache(maxsize=32)
def snapshot(folder, crop):
    path = Path(folder) / 'yield' / f'{crop}.json'
    if not path.exists():
        return {}
    return {r['municipality_code']: r for r in json.loads(path.read_text(encoding='utf-8'))['responses'] if 'municipality_code' in r}


@app.get('/v1/catalog')
def get_catalog():
    """Crops, covered municipalities, cutoffs, model per scope, validation and 2022-2024 test results."""
    return catalog()


@app.get('/v1/yield')
def get_yield(crop: str, municipality: str = Query(pattern='^[0-9]{7}$')):
    if crop not in catalog()['crops']:
        raise HTTPException(422, {'schema_version': '1.0.0', 'product': 'yield_estimate', 'error': {'code': 'invalid_crop'}})
    answer = snapshot(str(latest_folder()), crop).get(municipality)
    if answer is None:
        raise HTTPException(404, {'schema_version': '1.0.0', 'product': 'yield_estimate', 'error': {'code': 'municipality_not_covered'}})
    return answer


class YieldRequest(BaseModel):
    crop: str
    municipality: str
    sowing_date: date | None = None


@app.post('/v1/yield')
def post_yield(request: YieldRequest):
    """On-demand estimate, e.g. with the producer's sowing date (2-3 s with a warm cache)."""
    from data.serving.providers import Live
    from data.serving.yield_service import estimate_yield
    provider = Live(ROOT / 'data/cache/live', mode='operational')
    answer = estimate_yield(request.crop, request.municipality, date.today(), catalog=catalog(), models_dir=MODELS / 'models',
                            get_weather=provider.weather, get_history=provider.history, mode='operational', sowing_date=request.sowing_date)
    if 'error' in answer:
        raise HTTPException(ERROR_STATUS[answer['error']['code']], answer)
    return answer


@app.get('/v1/water')
def get_water(crop: str, municipality: str):
    path = latest_folder() / 'water' / f'{municipality}_{crop}.json'
    if not path.exists():
        raise HTTPException(404, 'No water projection for this municipality and crop in the snapshot')
    return json.loads(path.read_text(encoding='utf-8'))


@app.get('/v1/field-shift/sorriso')
def get_field_shift():
    return json.loads((ROOT / 'contracts/examples/field-shift-sorriso.json').read_text(encoding='utf-8'))


@app.get('/health')
def health():
    folder = latest_folder()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    return {'snapshot': folder.name, 'generated_at': manifest['generated_at'], 'complete': manifest['complete'],
            'catalog_version': catalog()['version'], 'operational_release': 'not_released'}
