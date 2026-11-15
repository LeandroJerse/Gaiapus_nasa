"""Crop-area weighted climate using historical ESA CCI land cover, not crop species maps."""
import argparse
import csv
import json
import math
import time
from functools import lru_cache
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date,timedelta
from pathlib import Path
from urllib.parse import urlencode,urlsplit

from data.ingest.v2_common import RAW,OUT,fetch,write_csv,save_json,sha
from data.ingest.training_foundation import decode_json,summarize

STAC='https://planetarycomputer.microsoft.com/api/stac/v1'
GRIDS={'rain':(.1,.1,-179.95,-89.95),'met':(.625,.5,-180.,-90.),'solar':(1.,1.,-179.5,-89.5)}
PARAMS={'rain':'IMERG_PRECTOT','met':'T2M_MIN,T2M_MAX,RH2M,WS2M','solar':'ALLSKY_SFC_SW_DWN'}
FIELDS={'IMERG_PRECTOT':'rain_mm','T2M_MIN':'t_min_c','T2M_MAX':'t_max_c','RH2M':'rh2m_pct','WS2M':'ws2m_m_s','ALLSKY_SFC_SW_DWN':'radiation_mj_m2'}


@lru_cache(maxsize=4)
def transient_token(account,container):
    from urllib.request import urlopen
    from urllib.error import HTTPError
    for attempt in range(4):
        try:
            with urlopen(f'https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}',timeout=30) as response:
                return json.load(response)['token'].lstrip('?')
        except HTTPError as exc:
            if exc.code!=429 or attempt==3:raise
            time.sleep(min(60,int(exc.headers.get('Retry-After','20'))))


def weighted_value(values,weights):
    if len(values)!=len(weights) or not weights or any(not math.isfinite(w) or w<0 for w in weights) or sum(weights)<=0:
        raise ValueError('Invalid spatial weights')
    if any(v is None and w>0 for v,w in zip(values,weights)):return None
    return sum(v*w for v,w in zip(values,weights) if w>0)/sum(weights)


def validate_daily_fields(fields):
    for field,values in fields.items():
        for value in values:
            if math.isnan(value):continue  # Missing coverage propagates and blocks sample publication.
            if not math.isfinite(value):raise ValueError('Infinite meteorology')
            if field in {'rain_mm','radiation_mj_m2','ws2m_m_s'} and value<0:raise ValueError(f'Negative {field}')
            if field=='rh2m_pct' and not 0<=value<=100:raise ValueError('Humidity outside 0-100')
    if 't_min_c' in fields and 't_max_c' in fields:
        if any(a>b for a,b in zip(fields['t_min_c'],fields['t_max_c'])):raise ValueError('Tmin above Tmax')


def weighted_matrix(matrix,weights):
    import numpy as np
    matrix=np.asarray(matrix,dtype=float);weights=np.asarray(weights,dtype=float)
    if matrix.ndim!=2 or matrix.shape[0]!=len(weights) or not np.isfinite(weights).all() or (weights<=0).any() or abs(weights.sum()-1)>1e-8:
        raise ValueError('Invalid normalized spatial weights')
    return np.sum(matrix*weights[:,None],axis=0)


def grid_key(lon,lat,grid):
    dx,dy,x0,y0=GRIDS[grid]
    return math.floor((lon-x0)/dx+.5),math.floor((lat-y0)/dy+.5)


def coordinates(grid,ix,iy):
    dx,dy,x0,y0=GRIDS[grid]
    return round(x0+ix*dx,6),round(y0+iy*dy,6)


def masks():
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds,Window
    from rasterio.features import geometry_mask
    from shapely.geometry import shape,mapping
    from pyproj import Geod
    sites=json.loads(Path('data/raw/training_v1/locations.json').read_bytes())
    geod=Geod(ellps='WGS84'); weights=[];audit=[]
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_TIMEOUT='90',GDAL_HTTP_MAX_RETRY='3'):
        for year in range(2000,2021):
            query=urlencode({'collections':'esa-cci-lc','datetime':f'{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z','bbox':'-62,-25,-40,-5','limit':100})
            payload=fetch(f'esa/items_{year}.json',STAC+'/search?'+query)
            items=payload['features']
            if not items:raise ValueError(f'No ESA mask for {year}')
            for site in sites:
                code=site['municipality_code']; boundary=decode_json(Path(f'data/raw/training_v1/boundary_{code}.geojson').read_bytes())
                geometry=shape(boundary['features'][0]['geometry'])
                item=next(x for x in items if shape(x['geometry']).covers(geometry))
                asset=item['assets']['lccs_class']; crop_classes={c['value'] for c in asset['classification:classes'] if c['name'] in {'cropland-1','cropland-1a','cropland-2'}}
                if crop_classes!={10,11,20}:raise ValueError('Unexpected ESA classes')
                clip=RAW/'esa'/f'{code}_{year}.tif'; meta=clip.with_suffix('.manifest.json')
                if not clip.exists():
                    href=asset['href'];parts=urlsplit(href);account=parts.hostname.split('.')[0];container=parts.path.split('/')[1]
                    # SAS is transient and never persisted in source manifests.
                    token=transient_token(account,container)
                    with rasterio.open(href+'?'+token.lstrip('?')) as source:
                        if source.crs.to_epsg()!=4326:raise ValueError('Expected geographic ESA raster')
                        window=from_bounds(*geometry.bounds,source.transform)
                        left,top=math.floor(window.col_off),math.floor(window.row_off)
                        window=Window(left,top,math.ceil(window.col_off+window.width)-left,math.ceil(window.row_off+window.height)-top)
                        data=source.read(1,window=window)
                        transform=source.window_transform(window)
                        profile={'driver':'GTiff','height':data.shape[0],'width':data.shape[1],'count':1,'dtype':data.dtype,'crs':source.crs,'transform':transform,'compress':'deflate','nodata':0}
                    with rasterio.open(clip,'w',**profile) as target:target.write(data,1)
                    save_json(meta,{'url':href,'item_id':item['id'],'sha256':sha(clip),'native_resolution_m':300,'read':'native-resolution crop, no overview or resampling'})
                elif sha(clip)!=json.loads(meta.read_bytes())['sha256']:raise ValueError('Changed mask crop')
                with rasterio.open(clip) as source:
                    data=source.read(1);transform=source.transform
                inside=geometry_mask([mapping(geometry)],out_shape=data.shape,transform=transform,invert=True)
                cropped=inside & np.isin(data,list(crop_classes));rr,cc=np.where(cropped)
                if not len(rr):raise ValueError(f'No pure cropland: {code}/{year}')
                lon=transform.c+(cc+.5)*transform.a;lat=transform.f+(rr+.5)*transform.e
                area_by_row={}
                for row in np.unique(rr):
                    west=transform.c;east=west+transform.a;north=transform.f+row*transform.e;south=north+transform.e
                    area_by_row[row]=abs(geod.polygon_area_perimeter([west,east,east,west],[south,south,north,north])[0])/10000
                areas=np.array([area_by_row[r] for r in rr]);total=float(areas.sum())
                for grid,(dx,dy,x0,y0) in GRIDS.items():
                    ix=np.floor((lon-x0)/dx+.5).astype(int);iy=np.floor((lat-y0)/dy+.5).astype(int)
                    pairs,inverse=np.unique(np.stack([ix,iy],axis=1),axis=0,return_inverse=True)
                    sums=np.bincount(inverse,weights=areas)
                    for (x,y),area in zip(pairs,sums):
                        query_lon,query_lat=coordinates(grid,int(x),int(y))
                        weights.append({'municipality_code':code,'mask_year':year,'grid':grid,'cell_id':f'{grid}_{x}_{y}',
                                        'longitude':query_lon,'latitude':query_lat,'crop_area_ha':float(area),'weight':float(area/total)})
                audit.append({'municipality_code':code,'mask_year':year,'cropland_ha':total,'cropland_pixels':len(rr),
                              'inside_pixels':int(inside.sum()),'nodata_pixels':int((inside&(data==0)).sum()),
                              'classes':'10,11,20','species_specific':False})
            print('ESA masks and weights',year,flush=True)
    write_csv('crop_cell_weights.csv',weights);write_csv('crop_mask_audit.csv',audit)
    save_json(OUT/'mask_manifest.json',{'source':'ESA CCI/C3S Land Cover via Planetary Computer','classes':[10,11,20],
             'weight_support':'pure cropland classes at native 300 m; excludes mixed classes 30 and 40',
             'species_specific':False,'mask_year_rule':'min(harvest_year-2,2020)',
             'grids':GRIDS,'area_method':'WGS84 geodesic pixel area, pixel centers inside IBGE simplified boundary',
             'limitations':['generic agriculture, not soy/maize specific','revised historical masks, not operational vintages',
                            '300m pixels and simplified boundaries','MERRA2 and CERES nominal grid assumptions; POWER query is nearest-point extraction']})


def weather():
    with (OUT/'crop_cell_weights.csv').open(encoding='utf-8-sig') as f:weights=list(csv.DictReader(f))
    cells={r['cell_id']:r for r in weights}
    def task(item):
        cid,r=item
        query=urlencode({'parameters':PARAMS[r['grid']],'community':'AG','longitude':r['longitude'],'latitude':r['latitude'],
                         'start':'20010101','end':'20241231','format':'JSON','time-standard':'UTC'})
        fetch(f'cells/{cid}.json','https://power.larc.nasa.gov/api/temporal/daily/point?'+query)
        return cid
    completed=0
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs={executor.submit(task,item):item[0] for item in cells.items()}
        failures=[]
        for future in as_completed(jobs):
            try:future.result()
            except Exception as exc:failures.append({'cell_id':jobs[future],'error':str(exc)})
            completed+=1
            if completed%20==0:print('Climate cells',completed,'/',len(cells),flush=True)
    save_json(OUT/'cell_download_status.json',{'expected_cells':len(cells),'failures':failures})
    if failures:raise ValueError(f'{len(failures)} failed cells; rerun uses verified cache')


def build():
    import numpy as np
    with (OUT/'crop_cell_weights.csv').open(encoding='utf-8-sig') as f:weights=list(csv.DictReader(f))
    with Path('data/processed/training_v1/research_samples.csv').open(encoding='utf-8-sig') as f:samples=list(csv.DictReader(f))
    days=[date(2001,1,1)+timedelta(days=i) for i in range((date(2024,12,31)-date(2001,1,1)).days+1)]
    keys=[d.strftime('%Y%m%d') for d in days];lookup={d.isoformat():i for i,d in enumerate(days)}
    cell_data={}
    for cid in sorted({r['cell_id'] for r in weights}):
        path=RAW/f'cells/{cid}.json'
        if sha(path)!=json.loads(path.with_suffix('.json.manifest.json').read_bytes())['sha256']:raise ValueError(f'Cell hash mismatch: {cid}')
        payload=decode_json(path.read_bytes())
        if payload['header']['time_standard']!='UTC':raise ValueError('Expected UTC')
        fill=payload['header']['fill_value']; fields={}
        if set(payload['properties']['parameter'])!=set(PARAMS[cid.split('_')[0]].split(',')):raise ValueError('Unexpected meteorology parameters')
        for param,series in payload['properties']['parameter'].items():
            units=payload['parameters'][param]['units'];factor=1
            expected={'IMERG_PRECTOT':'mm/day','T2M_MIN':'C','T2M_MAX':'C','RH2M':'%','WS2M':'m/s'}
            if param=='ALLSKY_SFC_SW_DWN':
                if units in {'kWh/m^2/day','kW-hr/m^2/day'}:factor=3.6
                elif units!='MJ/m^2/day':raise ValueError('Radiation unit')
            elif units!=expected[param]:raise ValueError('Meteorology unit')
            fields[FIELDS[param]]=np.array([float(series[k])*factor if series.get(k) is not None and series[k]!=fill else np.nan for k in keys])
        validate_daily_fields(fields)
        cell_data[cid]=fields
    grouped=defaultdict(list)
    for r in weights:grouped[(r['municipality_code'],int(r['mask_year']),r['grid'])].append(r)
    arrays={};daily_rows=[];output=[]
    for sample in samples:
        code=sample['municipality_code'];year=int(sample['harvest_year']);mask_year=min(year-2,2020)
        cachekey=(code,mask_year)
        if cachekey not in arrays:
            result={}
            for grid in GRIDS:
                group=grouped[(code,mask_year,grid)];w=np.array([float(r['weight']) for r in group])
                if abs(w.sum()-1)>1e-8:raise ValueError('Weights do not sum to 1')
                for field in cell_data[group[0]['cell_id']]:
                    matrix=np.stack([cell_data[r['cell_id']][field] for r in group])
                    # NaN propagates; never silently renormalize around an absent cell.
                    result[field]=weighted_matrix(matrix,w)
                    if field=='rain_mm':
                        result['dry_area_fraction']=np.where(np.isnan(matrix).any(axis=0),np.nan,np.sum((matrix<1)*w[:,None],axis=0))
            arrays[cachekey]=result
        fields=arrays[cachekey];start=lookup[sample['window_start']];end=lookup[sample['feature_cutoff']]+1
        selected=[]
        for index in range(start,end):
            row={'day_utc':days[index].isoformat(),**{k:float(v[index]) if np.isfinite(v[index]) else None for k,v in fields.items()}}
            selected.append(row)
            daily_rows.append({'sample_id':sample['sample_id'],'mask_year':mask_year,**row})
        metrics=summarize(selected)
        if not metrics['complete']:raise ValueError(f'Incomplete crop-weighted weather: {sample["sample_id"]}')
        output.append({**sample,**metrics,'mask_year':mask_year,'spatial_support':'ESA_generic_cropland_area_weighted',
                       'species_specific_mask':False,'mean_dry_area_fraction':float(np.mean(fields['dry_area_fraction'][start:end]))})
    write_csv('research_samples.csv',output);write_csv('weighted_season_daily.csv',daily_rows)
    save_json(OUT/'weighted_build_manifest.json',{'samples':len(output),'season_daily_rows':len(daily_rows),
             'input_v1_sha256':sha(Path('data/processed/training_v1/research_samples.csv')),
             'weights_sha256':sha(OUT/'crop_cell_weights.csv'),'all_required_values_finite':True,
             'rain_bias_corrected':False,'crop_mask_is_species_specific':False,'final_yield_test_evaluated':False})
    print('Built weighted samples',len(output),'season days',len(daily_rows),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['masks','weather','build']);args=parser.parse_args()
    {'masks':masks,'weather':weather,'build':build}[args.stage]()
