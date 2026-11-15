"""Prefix-only research features; publication-time eligibility is a separate gate."""
from datetime import date,timedelta
from data.models.water_balance import CROPS,PHASES,bucket_day,finite,phase_kc

WEATHER_FEATURES=['rain_sum_mm','dry_days','max_dry_spell','t_min_mean_c','t_max_mean_c','heat_days','radiation_sum_mj_m2','rh_mean_pct','wind_mean_m_s']
HYDRO_FEATURES=[f'hydro_{phase}_{metric}' for phase in PHASES for metric in ('observed_days','deficit_mm','eta_etc_ratio','stress_days')]+['hydro_storage_end_mm']


def require_asof_availability(rows,decision):
    for row in rows:
        if not row.get('available_at') or date.fromisoformat(row['available_at'])>decision or date.fromisoformat(row['day_utc'])>decision:
            raise ValueError('Observation or verified product vintage unavailable at decision time')


def weather_summary(rows):
    """Weather features of an already validated, contiguous prefix."""
    horizon=len(rows);spell=0;max_spell=0
    for r in rows:
        finite(*(r[k] for k in ('rain_mm','t_min_c','t_max_c','radiation_mj_m2','rh2m_pct','ws2m_m_s')))
        spell=spell+1 if r['rain_mm']<1 else 0;max_spell=max(max_spell,spell)
    return {'rain_sum_mm':sum(r['rain_mm'] for r in rows),'dry_days':sum(r['rain_mm']<1 for r in rows),'max_dry_spell':max_spell,
            't_min_mean_c':sum(r['t_min_c'] for r in rows)/horizon,'t_max_mean_c':sum(r['t_max_c'] for r in rows)/horizon,
            'heat_days':sum(r['t_max_c']>35 for r in rows),'radiation_sum_mj_m2':sum(r['radiation_mj_m2'] for r in rows),
            'rh_mean_pct':sum(r['rh2m_pct'] for r in rows)/horizon,'wind_mean_m_s':sum(r['ws2m_m_s'] for r in rows)/horizon}


def features_at_cutoff(weather,crop,sowing,horizon,*,operational=False,config=None,weather_only=False):
    """`config` gives water parameters for crops outside the calibrated set; `weather_only` leaves hydro empty."""
    if config is None and not weather_only:
        if crop not in CROPS:raise ValueError('Unsupported crop or horizon')
        config=CROPS[crop]
    limit=sum(config['phase_days']) if config else 400
    if not isinstance(horizon,int) or not 15<=horizon<=limit:raise ValueError('Unsupported crop or horizon')
    cutoff=sowing+timedelta(days=horizon-1)
    rows=sorted((r for r in weather if sowing.isoformat()<=r['day_utc']<=cutoff.isoformat()),key=lambda r:r['day_utc'])
    expected=[(sowing+timedelta(days=i)).isoformat() for i in range(horizon)]
    if [r['day_utc'] for r in rows]!=expected:raise ValueError('Observed prefix incomplete or duplicated')
    if operational:require_asof_availability(rows,cutoff)
    result=weather_summary(rows)
    if weather_only:
        result.update({f'hydro_{phase}_{m}':None for phase in PHASES for m in ('observed_days','deficit_mm','eta_etc_ratio','stress_days')})
        result['hydro_storage_end_mm']=None
        return result
    capacity=config['cad_mm']['AD4'];storage=.7*capacity;daily=[]
    for i,r in enumerate(rows):
        finite(r['et0_mm'])
        phase,kc=phase_kc(config,i);etc=kc*r['et0_mm'];p=min(.8,max(.1,config['p_base']+.04*(5-etc)))
        item=bucket_day(storage,capacity,r['rain_mm'],etc,p);storage=item['storage_end_mm']
        daily.append({'phase':phase,**item})
    for phase in PHASES:
        part=[r for r in daily if r['phase']==phase];prefix=f'hydro_{phase}_'
        result[prefix+'observed_days']=len(part)
        result[prefix+'deficit_mm']=sum(r['et_deficit_mm'] for r in part) if part else None
        etc=sum(r['etc_mm'] for r in part)
        result[prefix+'eta_etc_ratio']=sum(r['eta_mm'] for r in part)/etc if etc else None
        result[prefix+'stress_days']=sum(r['ks']<1-1e-9 for r in part) if part else None
    result['hydro_storage_end_mm']=storage
    return result
