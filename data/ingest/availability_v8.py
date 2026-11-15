"""Temporal availability audit of the 2025 evaluation inputs: available_at <= decision_at (plan v8, item 6).

For every crop, state and cutoff of harvest 2025, decision_at = cutoff + 1 day. Every input
actually used by the feature row gets an available_at with a status:
  proven    - documented release date (IBGE PAM release calendar 9117, Censo publication)
  estimated - documented typical latency, not the archived release of that day (IMERG Final ~3.5 months)
  unknown   - no release/vintage record (POWER CERES radiation, POWER meteorology)
PAM lags are computed from the lag rule of the feature builders and checked date by date; no
year is excluded by a hard-coded rule. Weather vintages are not archived, so the evaluation is
classified as retrospective whatever the PAM result.
"""
from datetime import date, timedelta

from data.ingest.audit_v8 import PAM_RELEASE, release_date
from data.ingest.water_balance_run import ROOT, save_json

OUT = ROOT / 'data/processed/training_v8/availability.json'
IMERG_FINAL_LATENCY_DAYS = 105  # ~3.5 months (GPM IMERG documentation); estimated, not per-day archive
STATES = {'soy': ('31', '51'), 'maize2': ('29', '31', '35', '41', '50', '51', '52', '53'), 'coffee_arabica': ('31',),
          'coffee_total_mg': ('31',), 'coffee_conilon': ('32',)}
REPRESENTATIVE = {'31': '3170206', '51': '5107925', '29': '2919553', '35': '3548708', '41': '4104808', '50': '5003702',
                  '52': '5208707', '53': '5300108', '32': '3203205'}


def unavailable_fraction(first, cutoff, latency_days):
    """Share of the days in [first, cutoff] not yet published at the decision (cutoff + 1)."""
    decision = cutoff + timedelta(days=1)
    days = (cutoff - first).days + 1
    return sum(1 for i in range(days) if first + timedelta(days=i + latency_days) > decision) / days


def pam_status(year):
    return 'proven' if year in PAM_RELEASE else 'estimated'


def lags_used(crop, year, milestone, history_years):
    """PAM years the feature builders actually read for this cutoff, from their own lag rules."""
    from data.ingest import cohort_v5 as v5
    from data.ingest import cohort_v7 as c7
    probe = {y: float(y) for y in history_years}
    if crop in c7.COFFEE:
        lags = c7.coffee_lags(probe, year, milestone)
        return sorted({int(v) for k, v in lags.items() if k[9:-6].isdigit() and k.startswith('yield_lag') and v is not None})
    return sorted(year - lag for lag in (2, 3, 4) if year - lag in probe) if v5.lagged_yield(probe, year)[1] else []


def windows(crop, year):
    """(label, horizon, state, weather window first day, cutoff day)."""
    from data.ingest.cohort_v7 import COFFEE
    from data.ingest.crops import horizons, sowing_date
    if crop in COFFEE:
        start = date(year - 1, 6, 1)
        for milestone, (month, day, offset) in COFFEE[crop]['milestones'].items():
            cutoff = date(year + offset, month, day)
            for state in STATES[crop]:
                yield milestone, (cutoff - start).days + 1, state, date(year - 2, 6, 1), cutoff
        return
    for horizon in horizons(crop):
        for state in STATES[crop]:
            start = sowing_date(crop, REPRESENTATIVE[state], year)
            yield f'day_{horizon}', horizon, state, start, start + timedelta(days=horizon - 1)


def audit(year=2025, history_years=range(2001, 2025)):
    rows, violations = [], {'proven': 0, 'estimated': 0, 'unknown': 0}
    for crop in STATES:
        for label, horizon, state, first, cutoff in windows(crop, year):
            decision = cutoff + timedelta(days=1)
            inputs = []
            for pam in lags_used(crop, year, label, history_years):
                available = release_date(pam)
                inputs.append({'input': f'PAM {pam}', 'available_at': str(available), 'status': pam_status(pam), 'ok': available <= decision})
            share = unavailable_fraction(first, cutoff, IMERG_FINAL_LATENCY_DAYS)
            last_day_available = cutoff + timedelta(days=IMERG_FINAL_LATENCY_DAYS)
            inputs.append({'input': 'IMERG Final rain (last window day)', 'available_at': str(last_day_available), 'status': 'estimated',
                           'ok': last_day_available <= decision, 'window_share_unpublished': round(share, 3)})
            inputs.append({'input': 'CERES radiation via POWER', 'available_at': None, 'status': 'unknown', 'ok': None})
            inputs.append({'input': 'POWER meteorology (T, RH, wind, dew point, pressure)', 'available_at': None, 'status': 'unknown', 'ok': None})
            for item in inputs:
                if item['ok'] is False:
                    violations[item['status']] += 1
            rows.append({'crop': crop, 'state': state, 'cutoff': label, 'horizon_days': horizon, 'window_first_day': str(first),
                         'cutoff_day': str(cutoff), 'decision_at': str(decision), 'inputs': inputs})
    pam_ok = all(i['ok'] for r in rows for i in r['inputs'] if i['input'].startswith('PAM'))
    result = {'year': year, 'rule': 'available_at <= decision_at for every input used; decision_at = cutoff + 1 day',
              'pam_lags_all_available': pam_ok,
              'violations_by_status': violations,
              'unknown_inputs': ['CERES radiation via POWER', 'POWER meteorology'],
              'classification': 'retrospective',
              'reason': 'PAM lags pass with proven dates; IMERG Final violates the rule at every annual cutoff (estimated latency) and '
                        'the POWER radiation/meteorology vintages are unknown, so weather inputs are not temporally eligible.',
              'pam_release_calendar_proven': {str(k): str(v) for k, v in PAM_RELEASE.items()},
              'cutoffs': rows}
    save_json(OUT, result)
    return result


if __name__ == '__main__':
    out = audit()
    print(out['classification'], 'PAM lags all available:', out['pam_lags_all_available'], 'violations:', out['violations_by_status'])
    for r in out['cutoffs']:
        pam = [f"{i['input']}@{i['available_at']}({i['status']})" for i in r['inputs'] if i['input'].startswith('PAM')]
        imerg = next(i for i in r['inputs'] if i['input'].startswith('IMERG'))
        print(r['crop'], r['state'], r['cutoff'], r['decision_at'], pam, 'IMERG unpublished share', imerg['window_share_unpublished'])
