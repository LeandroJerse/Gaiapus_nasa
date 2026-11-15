"""Verify normalized spatial weights and preserve a factual Campo Novo diagnosis."""
import csv
import json
from collections import defaultdict
from pathlib import Path
from data.ingest.training_foundation import decode_json
from data.ingest.v2_common import OUT,RAW,save_json


def main():
    import rasterio
    from shapely.geometry import shape,Point
    with (OUT/'crop_cell_weights.csv').open(encoding='utf-8-sig') as stream:weights=list(csv.DictReader(stream))
    totals=defaultdict(float);areas=defaultdict(float)
    for r in weights:
        key=(r['municipality_code'],r['mask_year'],r['grid']);totals[key]+=float(r['weight']);areas[key]+=float(r['crop_area_ha'])
        if float(r['weight'])<=0 or float(r['crop_area_ha'])<=0:raise ValueError('Nonpositive crop area weight')
    if any(abs(x-1)>1e-8 for x in totals.values()):raise ValueError('Unnormalized weight group')
    for code,year,grid in totals:
        if abs(areas[(code,year,grid)]-areas[(code,year,'rain')])>.01:raise ValueError('Crop area differs between grids')
    soil=json.loads((OUT/'campo_soil_resolution.json').read_bytes());original=soil['original_point']
    boundary=shape(decode_json(Path('data/raw/training_v1/boundary_5102637.geojson').read_bytes())['features'][0]['geometry'])
    points=[('original',original['longitude'],original['latitude'])]
    if soil['nearby_context_available']:
        point=next(p for p in soil['attempts'] if p['complete_layers']==20)
        points.append(('soil_neighbor',point['longitude'],point['latitude']))
    agricultural=json.loads((OUT/'campo_agricultural_soil_resolution.json').read_bytes())
    if agricultural['agricultural_context_available']:
        point=next(p for p in agricultural['attempts'] if p['valid_layers']==20)
        points.append(('preferred_agricultural_soil',point['longitude'],point['latitude']))
    result=[]
    with rasterio.open(RAW/'esa/5102637_2020.tif') as source:
        for name,lon,lat in points:
            inside=boundary.covers(Point(lon,lat))
            if not inside:raise ValueError('Soil candidate outside municipality')
            landclass=int(next(source.sample([(lon,lat)]))[0])
            if name=='preferred_agricultural_soil' and landclass not in {10,11,20}:raise ValueError('Preferred soil support is not mapped cropland')
            result.append({'point':name,'longitude':lon,'latitude':lat,'inside_municipality':inside,
                           'esa_2020_class':landclass})
    save_json(OUT/'spatial_audit.json',{'normalized_groups':len(totals),'weight_rows':len(weights),
              'max_weight_sum_error':max(abs(x-1) for x in totals.values()),'area_consistency_passed':True,
              'campo_points':result,'note':'ESA class 190 is urban; association with SoilGrids nodata does not prove its cause.'})
    print(json.dumps(result))


if __name__=='__main__':main()
