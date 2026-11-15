"""Scientific result envelope of the crop simulation product (ARQUITETURA.md §4).

Physical, statistical and economic results are separate sections. v1 has no calibrated yield method, so yield is
`unavailable` and economics report cost and water only. Status is never promoted by a successful run.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from data.models.crop_simulation.economics import EconomicsSpec, ensemble_economics
from data.models.crop_simulation.engine import ENGINE_VERSION
from data.models.crop_simulation.ensemble import EnsembleRequest, run_ensemble
from data.models.crop_simulation.soil import DAILY_ORDER

SCHEMA_VERSION = 'crop-simulation-result-v1'
LIMITATIONS = ('SINGLE_BUCKET_NO_ROOT_PROFILE', 'SOIL_HYPOTHESIS', 'FALLOW_PARAMETERS_HYPOTHESIS', 'HISTORICAL_FREQUENCY_NOT_PROBABILITY', 'NOT_FIELD_VALIDATED',
               'NO_HEAT_FROST_PEST_EFFECTS')


def _input_hash(location_id, mode, request: EnsembleRequest, forcing_manifests: dict, economics) -> str:
    ident = {'location_id': location_id, 'mode': mode, 'request': asdict(request), 'forcing': {str(k): v.get('content_sha256') for k, v in sorted(forcing_manifests.items())},
             'economics': asdict(economics) if economics else None, 'engine': ENGINE_VERSION}
    return hashlib.sha256(json.dumps(ident, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def build_envelope(*, location_id: str, mode: str, request: EnsembleRequest, members: dict, forcing_manifests: dict, economics: EconomicsSpec | None, created_at: str) -> dict:
    result = run_ensemble(request, members)
    econ = None
    if economics:
        e = ensemble_economics({y: (None, result.metrics['irrigation_gross_mm'][y]) for y in result.member_ids}, economics)
        econ = {'currency': e['currency'], 'price_reference': e['price_reference'], 'price_date': e['price_date'], 'negative_margin_frequency': e['negative_margin_frequency'],
                'members': {str(k): asdict(v) for k, v in e['members'].items()}}
    any_traj = next(iter(result.trajectories.values()))
    return {
        'schema_version': SCHEMA_VERSION, 'created_at': created_at, 'location_id': location_id, 'mode': mode,
        'input_hash': _input_hash(location_id, mode, request, forcing_manifests, economics),
        'engine': {'version': ENGINE_VERSION, 'parameter_set_id': any_traj.parameter_set_id, 'soil_profile_id': any_traj.soil_profile_id, 'daily_order': list(DAILY_ORDER)},
        'forcing': {'members': {str(k): v.get('content_sha256') for k, v in sorted(forcing_manifests.items())}},
        'scenario': result.scenario,
        'status': {'technical': 'ready', 'evidence': 'physical_experimental'},
        'members': {'included': [str(i) for i in result.member_ids], 'excluded': {str(k): v for k, v in result.excluded.items()}},
        'results': {
            'physical': {'summary': result.summary, 'representative': result.representative,
                         'members': {str(i): {k: result.metrics[k][i] for k in result.metrics} for i in result.member_ids}},
            'yield': {'status': 'unavailable', 'method': None, 'reason_code': 'NO_CALIBRATED_YIELD_METHOD'},
            'economics': econ,
        },
        'limitations': list(LIMITATIONS),
        # Daily rows are not part of the first payload; member_daily() serves one member at a time.
        '_daily': {str(i): t.rows for i, t in result.trajectories.items()},
    }


def member_daily(envelope: dict, member_id: str) -> list[dict]:
    return envelope['_daily'][member_id]
