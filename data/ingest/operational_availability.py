"""Conservative bitemporal eligibility from successful immutable acquisitions."""
from datetime import datetime,timezone,timedelta
import math
import re


def utc(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Timezone-aware timestamp required')
    return parsed.astimezone(timezone.utc)


def select_asof(records,decision_at):
    decision=utc(decision_at);selected={}
    for row in records:
        if not row.get('first_seen_at'):continue
        observed=utc(row['observation_end']);seen=utc(row['first_seen_at'])
        if observed>decision or seen>decision:continue
        if seen<observed:raise ValueError('Observed product cannot be acquired before observation ends')
        if not re.fullmatch('[0-9a-f]{64}',row.get('sha256','')):raise ValueError('Content hash required')
        value=row.get('value')
        if value is None or not isinstance(value,(float,int)) or isinstance(value,bool) or not math.isfinite(value):continue
        key=row['key']
        if key not in selected or utc(selected[key]['first_seen_at'])<seen:selected[key]=row
    return selected


def training_gate(records,required_keys,decision_at,label_available_at,fit_at):
    available=select_asof(records,decision_at)
    missing=sorted(set(required_keys)-available.keys())
    reasons=[]
    if utc(decision_at)>utc(fit_at):reasons.append('training_decision_after_fit')
    if missing:reasons.append('missing_asof_features')
    if not label_available_at or utc(label_available_at)>utc(fit_at):reasons.append('label_unavailable_at_fit')
    return {'eligible':not reasons,'reasons':reasons,'missing_keys':missing}


def build_asof_hydro_features(records,code,crop,sowing,horizon,decision_at,latitude,elevation):
    from data.models.water_balance import et0_daily
    from data.ingest.hydro_training_features import features_at_cutoff
    if horizon not in (60,90):raise ValueError('Unsupported horizon')
    selected=select_asof(records,decision_at);weather=[]
    fields=('rain_mm','t_min_c','t_max_c','radiation_mj_m2','rh2m_pct','ws2m_m_s','dewpoint_c','pressure_kpa')
    for i in range(horizon):
        day=sowing+timedelta(days=i);row={'day_utc':day.isoformat()}
        for field in fields:
            key=f'{code}:{day}:{field}'
            if key not in selected:raise ValueError(f'Missing as-of dependency: {key}')
            row[field]=selected[key]['value']
        et=et0_daily(day,latitude,elevation,row['t_min_c'],row['t_max_c'],row['radiation_mj_m2'],row['ws2m_m_s'],row['pressure_kpa'],dewpoint=row['dewpoint_c'])
        row['et0_mm']=et['et0_mm'];weather.append(row)
    # Publication times of all raw dependencies were checked above, before deriving ET0.
    return features_at_cutoff(weather,crop,sowing,horizon)
