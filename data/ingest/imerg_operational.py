"""Archive NASA CMR inventory and attempt authenticated immutable rain acquisitions.

Requests uses the user's standard netrc authentication when configured. No credentials
are accepted, persisted or printed by this program. No automatic recurring job is set.
"""
import argparse
import csv
import hashlib
import json
import os
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from data.ingest.water_balance_run import ROOT,save_json,write_csv,sha

RAW=ROOT/'data/raw/imerg_operational_v1'
OUT=ROOT/'data/processed/imerg_operational_v1'
PRODUCTS={'Early':'GPM_3IMERGDE','Late':'GPM_3IMERGDL','Final':'GPM_3IMERGDF'}


def now():return datetime.now(timezone.utc).isoformat()


def inventory(start,end):
    RAW.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');records=[]
    for run,product in PRODUCTS.items():
        page=1;count=0
        while True:
            response=requests.get('https://cmr.earthdata.nasa.gov/search/granules.json',params={'short_name':product,'version':'07','temporal':f'{start}T00:00:00Z,{end}T23:59:59Z','page_size':2000,'page_num':page,'sort_key':'start_date'},timeout=90)
            response.raise_for_status();seen=now();payload=response.content
            path=RAW/f'cmr_{run}_{start}_{end}_{page}_{stamp}.json';path.write_bytes(payload)
            entries=response.json()['feed']['entry']
            for entry in entries:
                downloads=[link['href'] for link in entry.get('links',[]) if not link.get('inherited') and link.get('rel','').endswith('/data#') and link['href'].startswith('https://')]
                records.append({'run':run,'product':product,'version':'07','granule_id':entry['id'],'filename':entry['producer_granule_id'],
                                'observation_start':entry['time_start'],'observation_end':entry['time_end'],'cmr_updated_at':entry['updated'],
                                'catalog_seen_at':seen,'data_first_seen_at':'','download_url':downloads[0] if downloads else '',
                                'catalog_path':path.relative_to(ROOT).as_posix(),'catalog_sha256':hashlib.sha256(payload).hexdigest(),
                                'availability_evidence':'catalog_only_not_data_availability'})
            count+=len(entries)
            if len(entries)<2000:break
            page+=1
        print(f'{run}: {count} catalog granules',flush=True)
    write_csv(OUT/'catalog.csv',records)
    return records


def extract(path,record,receipt):
    import netCDF4
    import numpy as np
    locations=json.loads((ROOT/'data/raw/training_v1/locations.json').read_text(encoding='utf-8'))
    results=[]
    # netCDF C library on Windows cannot reliably open this Unicode workspace path.
    with netCDF4.Dataset('snapshot',memory=path.read_bytes()) as ds:
        if record['product'] not in ds.title or ds.BeginDate!=record['observation_start'][:10] or ds.EndDate!=ds.BeginDate:
            raise ValueError('Product identity or daily interval mismatch')
        rain=ds.variables['precipitation']
        if rain.dimensions!=('time','lon','lat') or rain.units not in ('mm/day','mm d-1'):raise ValueError('Unexpected daily rain schema')
        time_var=ds.variables['time'];day=netCDF4.num2date(time_var[0],time_var.units).strftime('%Y-%m-%d')
        if day!=record['observation_start'][:10]:raise ValueError('Granule time mismatch')
        lon=ds.variables['lon'][:];lat=ds.variables['lat'][:]
        for loc in locations:
            i=int(np.argmin(abs(lon-loc['longitude'])));j=int(np.argmin(abs(lat-loc['latitude'])))
            value=rain[0,i,j]
            amount=None if np.ma.is_masked(value) else float(value)
            if amount is not None and (not np.isfinite(amount) or amount<0):raise ValueError('Invalid rainfall')
            count=ds.variables['precipitation_cnt'][0,i,j]
            count=None if np.ma.is_masked(count) else int(count)
            results.append({'municipality_code':loc['municipality_code'],'run':record['run'],'product':record['product'],'day_utc':day,
                            'cell_lon':float(lon[i]),'cell_lat':float(lat[j]),'rain_mm':amount,'units':'mm/day',
                            'valid_half_hours':count,'complete_48_half_hours':count==48,'production_time':ds.ProductionTime,
                            'observation_end':record['observation_end'],'first_seen_at':receipt['download_completed_at'],
                            'sha256':receipt['sha256'],'snapshot_path':receipt['path'],'catalog_updated_at':record['cmr_updated_at'],
                            'provider_first_published_at':None,'availability_basis':'first_successful_local_download_only'})
    return results


def acquire(records,day,session=None,cached=None):
    # Small probe across all runs; after authentication the same routine accepts each day.
    attempts=[];points=[]
    for run in PRODUCTS:
        candidates=[r for r in records if r['run']==run and r['observation_start'][:10]==day]
        if len(candidates)!=1:raise ValueError(f'Expected one daily granule for {run} {day}')
        record=candidates[0];url=record['download_url'];started=now()
        if cached is not None and record['filename'] in cached:
            continue
        response=(session or requests).get(url,timeout=(15,90),stream=True)
        attempt={'run':run,'day_utc':day,'attempted_at':started,'http_status':response.status_code,'url':url,
                 'final_endpoint':response.url.split('?')[0],'status':'blocked' if response.status_code!=200 else 'pending_validation'}
        if response.status_code!=200:
            response.close();attempts.append(attempt);print(f'{run}: HTTP {attempt["http_status"]}',flush=True);continue
        raw=response.content;completed=now()
        if raw[:8]!=b'\x89HDF\r\n\x1a\n':
            attempt['status']='not_netcdf_hdf5';attempts.append(attempt);response.close();continue
        digest=hashlib.sha256(raw).hexdigest();folder=RAW/'snapshots'/digest;folder.mkdir(parents=True,exist_ok=True)
        path=folder/record['filename'];receipt_path=folder/'receipt.json'
        if not path.exists():path.write_bytes(raw)
        if not receipt_path.exists():
            save_json(receipt_path,{'url':url,'path':path.relative_to(ROOT).as_posix(),'sha256':digest,'download_started_at':started,'download_completed_at':completed,
                                    'last_modified_header':response.headers.get('Last-Modified'),'etag':response.headers.get('ETag'),
                                    'provider_first_published_at':None,'last_modified_is_not_first_availability':True})
        receipt=json.loads(receipt_path.read_text(encoding='utf-8'));points.extend(extract(path,record,receipt));response.close()
        attempt.update(status='downloaded_and_extracted',sha256=digest,bytes=len(raw));attempts.append(attempt)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    save_json(RAW/f'access_attempts_{stamp}.json',attempts)
    if points:
        destination=OUT/'extractions';destination.mkdir(exist_ok=True);write_csv(destination/f'points_{day}_{stamp}.csv',points)
    return attempts,points


def batch(records,start,end):
    start,end=date.fromisoformat(start),date.fromisoformat(end)
    if start>end:
        raise ValueError('Start must precede end')
    by_day={}
    names={}
    for record in records:
        day=record['observation_start'][:10]
        by_day.setdefault(day,[]).append(record)
        names[(day,record['run'])]=record['filename']
    days=[(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    for day in days:
        if sorted(r['run'] for r in by_day.get(day,[]))!=sorted(PRODUCTS):
            raise ValueError(f'Incomplete or ambiguous catalog for {day}')
        for record in by_day[day]:
            url=urlsplit(record['download_url'])
            if url.scheme!='https' or url.hostname not in {
                'data.gesdisc.earthdata.nasa.gov','gpm1.gesdisc.eosdis.nasa.gov'
            } or url.username or url.password:
                raise ValueError('Unexpected GES DISC download URL')
    cached=set()
    for path in (OUT/'extractions').glob('*.csv'):
        with path.open(encoding='utf-8',newline='') as stream:
            rows=list(csv.DictReader(stream))
        groups={}
        for row in rows:
            groups.setdefault(row['snapshot_path'],set()).add(row['municipality_code'])
        for relative,codes in groups.items():
            snapshot=ROOT/relative
            if len(codes)==8 and snapshot.is_file() and sha(snapshot)==snapshot.parent.name:
                cached.add(snapshot.name)
    with requests.Session() as session:
        token=os.environ.get('EARTHDATA_TOKEN')
        if token:
            session.auth=lambda request: request
            session.headers['Authorization']=f'Bearer {token}'
        elif not requests.utils.get_netrc_auth('https://urs.earthdata.nasa.gov'):
            raise SystemExit('Earthdata credential unavailable. Configure EARTHDATA_TOKEN or Earthdata netrc.')
        session.mount('https://',HTTPAdapter(max_retries=Retry(
            total=3,backoff_factor=1,status_forcelist=(429,500,502,503,504))))
        failures=[]
        for day in days:
            if all(names[(day,run)] in cached for run in PRODUCTS):
                continue
            attempts,_=acquire(by_day[day],day,session,cached)
            failures.extend(a for a in attempts if a['status']!='downloaded_and_extracted')
            if any(a['http_status'] in (401,403) or a['status']=='not_netcdf_hdf5' for a in attempts):
                break
        save_json(OUT/'batch_status.json',{'start':str(start),'end':str(end),
                  'expected_granules':len(days)*len(PRODUCTS),'failures':failures,
                  'finished_at':now(),'complete':not failures})
        if failures:
            raise SystemExit('Acquisition incomplete. See batch_status.json and retry after resolving access.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',default='2016-01-01');parser.add_argument('--end',default='2021-12-31')
    parser.add_argument('--probe-day',default='2021-01-01');parser.add_argument('--reuse-catalog',action='store_true')
    parser.add_argument('--batch',action='store_true',help='Resume all three products in the selected date range')
    parser.add_argument('--import-file',type=Path,help='Import a NASA file downloaded through an authenticated browser')
    args=parser.parse_args()
    if args.reuse_catalog:
        with (OUT/'catalog.csv').open(encoding='utf-8',newline='') as stream:records=list(csv.DictReader(stream))
    else:records=inventory(args.start,args.end)
    if args.batch:
        batch(records,args.start,args.end)
        return
    if args.import_file:
        source=args.import_file;matches=[r for r in records if r['filename']==source.name]
        if len(matches)!=1:raise ValueError('File must match exactly one catalogued granule')
        record=matches[0];raw=source.read_bytes()
        if raw[:8]!=b'\x89HDF\r\n\x1a\n':raise ValueError('Not a NetCDF HDF5 file')
        digest=hashlib.sha256(raw).hexdigest();folder=RAW/'snapshots'/digest;folder.mkdir(parents=True,exist_ok=True)
        path=folder/source.name;receipt_path=folder/'receipt.json'
        if not path.exists():path.write_bytes(raw)
        if not receipt_path.exists():save_json(receipt_path,{'url':record['download_url'],'path':path.relative_to(ROOT).as_posix(),'sha256':digest,'download_completed_at':now(),'availability_basis':'first_local_verified_import_conservative','provider_first_published_at':None})
        receipt=json.loads(receipt_path.read_text(encoding='utf-8'));points=extract(path,record,receipt)
        destination=OUT/'extractions';destination.mkdir(exist_ok=True);write_csv(destination/f'points_{record["run"]}_{record["observation_start"][:10]}_{digest[:12]}.csv',points)
        print(f'Imported {record["run"]}: {len(points)} cells; {len(raw)} bytes')
        return
    attempts,points=acquire(records,args.probe_day)
    save_json(OUT/'collection_status.json',{'checked_at':now(),'catalog_granules':len(records),'probe_day':args.probe_day,'attempts':attempts,
                                          'extracted_point_rows':len(points),'historical_available_at_reconstructed':False,'operational_training_ready':False})


if __name__=='__main__':main()
