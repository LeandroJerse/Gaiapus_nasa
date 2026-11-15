"""Select an explicitly agricultural nearby soil support, keeping the original point missing."""
import json
from pathlib import Path
from urllib.parse import urlencode
from data.ingest.v2_common import RAW,OUT,fetch,write_csv,save_json
from data.ingest.training_foundation import decode_json
from data.ingest.station_reference import distance


def main():
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from shapely.geometry import shape,Point
    site=next(x for x in json.loads(Path('data/raw/training_v1/locations.json').read_bytes()) if x['municipality_code']=='5102637')
    geom=decode_json(Path('data/raw/training_v1/boundary_5102637.geojson').read_bytes())['features'][0]['geometry']
    with rasterio.open(RAW/'esa/5102637_2020.tif') as raster:
        data=raster.read(1);transform=raster.transform
        inside=geometry_mask([geom],out_shape=data.shape,transform=transform,invert=True)
    rr,cc=np.where(inside & np.isin(data,[10,11,20]))
    xs=transform.c+(cc+.5)*transform.a;ys=transform.f+(rr+.5)*transform.e
    distances=(ys-site['latitude'])**2+((xs-site['longitude'])*np.cos(np.deg2rad(site['latitude'])))**2
    candidates=[]
    for idx in np.argsort(distances):
        x,y=float(xs[idx]),float(ys[idx])
        if all(distance(y,x,p['latitude'],p['longitude'])>.5 for p in candidates):
            candidates.append({'longitude':x,'latitude':y,'distance_km':distance(y,x,site['latitude'],site['longitude']),
                               'esa_2020_class':int(data[rr[idx],cc[idx]])})
        if len(candidates)==3:break
    attempts=[];selected=[]
    for index,point in enumerate(candidates):
        if point['distance_km']>25:break
        assert shape(geom).covers(Point(point['longitude'],point['latitude']))
        query=[('lon',point['longitude']),('lat',point['latitude'])]+[('property',p) for p in ('clay','sand','soc','bdod','phh2o')]
        query += [('depth',d) for d in ('0-5cm','5-15cm','15-30cm','30-60cm')]+[('value',v) for v in ('mean','Q0.05','Q0.95')]
        payload=fetch(f'soil/campo_agricultural_{index}.json','https://rest.isric.org/soilgrids/v2.0/properties/query?'+urlencode(query))
        rows=[]
        for layer in payload['properties']['layers']:
            unit=layer['unit_measure']
            for depth in layer['depths']:
                values=depth['values']
                rows.append({'municipality_code':'5102637',**point,'property':layer['name'],'depth_cm':depth['label'],'unit':unit['target_units'],
                             **{k:values[v]/unit['d_factor'] if values.get(v) is not None else None for k,v in [('mean','mean'),('q05','Q0.05'),('q95','Q0.95')]},
                             'support':'nearby_ESA_cropland_proxy_NOT_original_point','used_in_yield_model':False})
        valid=sum(all(r[k] is not None for k in ('mean','q05','q95')) for r in rows)
        attempts.append({**point,'valid_layers':valid})
        print('Agricultural soil support',index,'distance km',point['distance_km'],'valid layers',valid,flush=True)
        if valid==20:selected=rows;break
    if selected:write_csv('campo_soil_agricultural.csv',selected)
    save_json(OUT/'campo_agricultural_soil_resolution.json',{'original_point':site,'original_gap_preserved':True,
              'candidates':candidates,'attempts':attempts,'agricultural_context_available':bool(selected),
              'selection':'nearest ESA 2020 pure cropland pixel with valid SoilGrids, candidates >=500m apart, maximum 25km',
              'exact_point_imputed':False,'field_measurement':False})


if __name__=='__main__':main()
