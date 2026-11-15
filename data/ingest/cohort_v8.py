"""v8 temporal contract: explicit-allowlist loader and availability checks.

load_development reads only files listed (with sha256) in the v8 audit manifest and fails
with a diagnostic on: a harvest year after 2021, duplicated keys, a cutoff not before the
decision, or a lag that the IBGE had not published at the decision date.
"""
import json
from datetime import date, datetime

from data.ingest.audit_v8 import lag_available
from data.ingest.water_balance_run import ROOT, sha

MANIFEST = ROOT / 'data/processed/training_v8/audit.json'
DEV_LAST_YEAR = 2021
SOURCES = {'soy': 'data/processed/training_v6/samples.parquet', 'maize2': 'data/processed/training_v6/samples.parquet',
           'coffee_arabica': 'data/processed/training_v7/samples.parquet', 'coffee_total_mg': 'data/processed/training_v7/samples.parquet',
           'coffee_conilon': 'data/processed/training_v7/samples.parquet'}


class ContaminationError(ValueError):
    pass


def validate(frame, crop, last_year=DEV_LAST_YEAR):
    import pandas as pd
    late = frame[frame.harvest_year > last_year]
    if len(late):
        raise ContaminationError(f'{crop}: {len(late)} rows after {last_year} (e.g. {late.sample_id.iloc[0]}); development data must stop at {last_year}')
    duplicated = frame.duplicated(['sample_id', 'horizon_days'])
    if duplicated.any():
        raise ContaminationError(f'{crop}: duplicated keys, e.g. {frame[duplicated].sample_id.iloc[0]}')
    decision = pd.to_datetime(frame.feature_cutoff) + pd.Timedelta(days=1)
    if (pd.to_datetime(frame.feature_cutoff) >= decision).any():
        raise ContaminationError(f'{crop}: cutoff not before the decision')
    if crop.startswith('coffee'):
        minimum = frame.milestone.map({'pre_flowering': 3, 'post_flowering': 2, 'grain_filling': 2})
        lag_columns = {k: f'yield_lag{k}_kg_ha' for k in range(2, 7)}
        for k, column in lag_columns.items():
            used = frame[column].notna() & (k >= minimum)
            for (year, when) in zip(frame.harvest_year[used] - k, decision[used].dt.date):
                if not lag_available(int(year), when):
                    raise ContaminationError(f'{crop}: lag {k} (PAM {year}) used before its publication on {when}')
    else:
        used = frame.yield_lag_count > 0
        for year, when in zip(frame.harvest_year[used] - 2, decision[used].dt.date):
            if not lag_available(int(year), when):
                raise ContaminationError(f'{crop}: lag PAM {year} used before its publication at decision {when}')
    return frame


def load_development(crop, *, manifest_path=MANIFEST, files=None):
    """Load one crop from the allowlisted file only; checksums must match the audit manifest."""
    import pandas as pd
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))['development_manifest']
    relative = (files or SOURCES)[crop]
    if relative not in manifest:
        raise ContaminationError(f'{relative} is not in the development allowlist')
    path = ROOT / relative
    if sha(path) != manifest[relative]['sha256']:
        raise ContaminationError(f'{relative} changed since the audit (checksum mismatch)')
    frame = pd.read_parquet(path, filters=[('crop', '==', crop)]).reset_index(drop=True)
    if 'milestone' not in frame:
        frame['milestone'] = 'day_' + frame.horizon_days.astype(str)
    if not crop.startswith('coffee'):
        frame['ref_lag_mean_kg_ha'] = frame.yield_lag2to4_mean_kg_ha
    return validate(frame, crop)


def check_forecast(issued_at, available_at, decision_at):
    """A forecast may enter a decision only if it was issued and available at or before the decision."""
    issued, available, decision = (datetime.fromisoformat(str(x)) for x in (issued_at, available_at, decision_at))
    if issued > decision:
        raise ContaminationError(f'forecast issued at {issued} after the decision {decision}')
    if available > decision:
        raise ContaminationError(f'forecast available at {available} after the decision {decision}')
    return True


def check_observation(observation_date, decision):
    """Observed weather after the decision date cannot be used."""
    if date.fromisoformat(str(observation_date)) >= decision:
        raise ContaminationError(f'observation {observation_date} not before the decision {decision}')
    return True
