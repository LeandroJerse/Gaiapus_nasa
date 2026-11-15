"""Validate IMERG daily UTC against INMET preceding-hour accumulations."""
import calendar
import csv
import json
import math
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from data.ingest.station_reference import distance
from data.ingest.v2_common import RAW,OUT,fetch,download_inmet,write_csv,save_json,sha


def rain_day(day, hour):
    stamp=datetime.strptime(day.replace('/','-')+' '+hour[:4],'%Y-%m-%d %H%M')
    return (stamp-timedelta(hours=1)).date().isoformat()


def station_rows(archive, station_id):
    with zipfile.ZipFile(archive) as zipped:
        name=next((name for name in zipped.namelist() if f'_{station_id}_' in name and name.upper().endswith('.CSV')),None)
        if name is None: return []
        raw=zipped.read(name)
        target=RAW/'inmet'/f'{station_id}_{archive.stem}.csv';target.parent.mkdir(exist_ok=True);target.write_bytes(raw)
    lines=raw.decode('latin1').splitlines()
    reader=csv.DictReader(lines[8:],delimiter=';')
    key=next(k for k in reader.fieldnames if 'PRECIPITA' in k)
    output=[]
    for row in reader:
        day,hour=row[reader.fieldnames[0]],row[reader.fieldnames[1]]
        value=row[key].replace(',','.')
        rain=float(value) if value else None
        if rain is not None and (rain<0 or not math.isfinite(rain)): rain=None
        output.append({'station_id':station_id,'message_date':day.replace('/','-'),'message_hour':hour[:4],
                       'day_utc':rain_day(day,hour),'rain_mm':rain})
    return output


def aggregate_hours(rows):
    groups=defaultdict(list); seen=set()
    for row in rows:
        key=(row['station_id'],row['message_date'],row['message_hour'])
        if key in seen: raise ValueError('Duplicate station hour')
        seen.add(key);groups[(row['station_id'],row['day_utc'])].append(row['rain_mm'])
    return [{'station_id':key[0],'day_utc':key[1], 'rain_mm':sum(values) if len(values)==24 and None not in values else None,
             'valid_hours':sum(v is not None for v in values),'reported_hours':len(values)} for key,values in sorted(groups.items())]


def metrics(rows):
    matched=[r for r in rows if r['station_mm'] is not None and r['imerg_mm'] is not None]
    if not matched:return {'pairs':0,'mae_mm':None,'bias_mm':None,'rmse_mm':None,'correlation':None,'station_sum_mm':None,'imerg_sum_mm':None,'wet_day_hits':0,'wet_day_misses':0,'wet_day_false_alarms':0}
    x=[r['station_mm'] for r in matched]; y=[r['imerg_mm'] for r in matched]; d=[b-a for a,b in zip(x,y)]
    return {'pairs':len(d),'mae_mm':statistics.mean(map(abs,d)),'bias_mm':statistics.mean(d),
            'rmse_mm':math.sqrt(statistics.mean(v*v for v in d)),
            'correlation':statistics.correlation(x,y) if len(d)>1 and max(x)>min(x) and max(y)>min(y) else None,
            'station_sum_mm':sum(x),'imerg_sum_mm':sum(y),'wet_day_hits':sum(a>=1 and b>=1 for a,b in zip(x,y)),
            'wet_day_misses':sum(a>=1 and b<1 for a,b in zip(x,y)), 'wet_day_false_alarms':sum(a<1 and b>=1 for a,b in zip(x,y))}


def pair_days(observed,satellite,fill,start,end,station_id):
    lookup={r['day_utc']:r for r in observed};rows=[]
    day=datetime.strptime(start,'%Y-%m-%d').date();last=datetime.strptime(end,'%Y-%m-%d').date()
    while day<=last:
        value=satellite.get(day.strftime('%Y%m%d'))
        if value==fill:value=None
        if value is not None and (not math.isfinite(value) or value<0):raise ValueError('Invalid satellite rain')
        obs=lookup.get(day.isoformat(),{})
        rows.append({'station_id':station_id,'day_utc':day.isoformat(),'station_mm':obs.get('rain_mm'),
                     'imerg_mm':value,'valid_hours':obs.get('valid_hours',0)})
        day+=timedelta(days=1)
    return rows


def main():
    archives=[download_inmet(2023),Path('data/raw/training_v1/inmet/2024.zip'),download_inmet(2025)]
    sites=json.loads(Path('data/raw/training_v1/locations.json').read_bytes())
    with Path('data/processed/training_v1/inmet_station_catalog_2024.csv').open(encoding='utf-8-sig') as f: catalog=list(csv.DictReader(f))
    chosen={'A507','A568','A542','A571','A904','A917','A903','A905','A911','A928','A902','A927'}
    daily=[];comparisons=[];monthly=[];summaries=[]
    for station in catalog:
        sid=station['station_id']
        if sid not in chosen:continue
        all_hours=[]
        for archive in archives:
            raw=station_rows(archive,sid)
            if raw:
                header=(RAW/'inmet'/f'{sid}_{archive.stem}.csv').read_text(encoding='latin1').splitlines()[:8]
                metadata={line.split(';')[0].rstrip(':'):line.split(';')[1] for line in header if ';' in line}
                if any(abs(float(metadata[key].replace(',','.'))-float(station[field]))>1e-5 for key,field in [('LATITUDE','lat'),('LONGITUDE','lon')]):
                    raise ValueError(f'Station moved: {sid}/{archive.stem}; query each coordinate period separately')
            # Only Jan 1 midnight from 2025 is required to close 2024 UTC.
            all_hours.extend(r for r in raw if archive.stem!='2025' or (r['message_date']=='2025-01-01' and r['message_hour']=='0000'))
        observed=[r for r in aggregate_hours(all_hours) if '2023-01-01'<=r['day_utc']<='2024-12-31']
        daily.extend(observed)
        query=urlencode({'parameters':'IMERG_PRECTOT','community':'AG','longitude':station['lon'],'latitude':station['lat'],
                         'start':'20230101','end':'20241231','format':'JSON','time-standard':'UTC'})
        payload=fetch(f'stations/power_{sid}.json','https://power.larc.nasa.gov/api/temporal/daily/point?'+query)
        if payload['header']['time_standard']!='UTC' or payload['parameters']['IMERG_PRECTOT']['units']!='mm/day':raise ValueError('Rain unit or time mismatch')
        satellite=payload['properties']['parameter']['IMERG_PRECTOT']; fill=payload['header']['fill_value']
        rows=pair_days(observed,satellite,fill,'2023-01-01','2024-12-31',sid)
        comparisons.extend(rows)
        for year in (2023,2024):
            selected=[r for r in rows if r['day_utc'].startswith(str(year))]
            m=metrics(selected)
            summaries.append({'station_id':sid,'station_name':station['name'],'year':year,'latitude':station['lat'],'longitude':station['lon'],
                              'expected_days':len(selected),'coverage_fraction':m['pairs']/len(selected), **m})
            for month in range(1,13):
                month_key=f'{year}-{month:02d}'; selected=[r for r in rows if r['day_utc'].startswith(month_key)]
                m=metrics(selected)
                monthly.append({'station_id':sid,'month':month_key,'expected_days':len(selected),
                                'full_month':m['pairs']==len(selected),**m})
        print('Validated',sid,metrics(rows)['pairs'],'days',flush=True)
    write_csv('station_daily_utc.csv',daily);write_csv('rain_pairs.csv',comparisons)
    write_csv('rain_monthly_validation.csv',monthly);write_csv('rain_station_validation.csv',summaries)
    candidates=[]
    site=next(s for s in sites if s['municipality_code']=='5102637')
    for r in summaries:
        if r['station_id'] not in {'A905','A911','A928','A902','A927','A903'}:continue
        km=distance(site['latitude'],site['longitude'],float(r['latitude']),float(r['longitude']))
        candidates.append({**r,'distance_to_campo_point_km':km,'local_station':r['station_id']=='A905',
                           'usable_for_annual_diagnostic':r['coverage_fraction']>=.9,
                           'replaces_local_missing_measurements':False})
    write_csv('campo_station_alternatives.csv',candidates)
    save_json(OUT/'rain_validation_manifest.json',{'interval':'INMET message t measures preceding hour; subtract one hour before daily UTC grouping',
              'source':'https://portal.inmet.gov.br/glossario/tempo','period':'2023-2024','station_count':len(chosen),
              'comparison':'IMERG via NASA POWER at actual station coordinates, not municipal representative points',
              'independence_caveat':'IMERG Final gauge adjustment can share gauge information; not guaranteed wholly independent of all gauge inputs',
              'missing_imputation':False,'bias_correction_applied':False,
              'satellite_validation_does_not_open_yield_test':True,'archives':{str(p):sha(p) for p in archives}})


if __name__=='__main__':main()
