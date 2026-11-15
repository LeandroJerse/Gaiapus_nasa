"""Find documented nearby soil context; never overwrite original nodata pixels."""
import json
from pathlib import Path
from urllib.parse import urlencode
from data.ingest.v2_common import RAW,OUT,fetch,write_csv,save_json
from data.ingest.station_reference import distance


def main():
    sites=json.loads(Path('data/raw/training_v1/locations.json').read_bytes())
    site=next(s for s in sites if s['municipality_code']=='5102637')
    lon,lat=site['longitude'],site['latitude'];attempts=[];rows=[]
    # Candidate offsets are declared, not silently substituted in the original point dataset.
    for index,(dx,dy) in enumerate([(0,.0025),(.0025,0),(0,-.0025),(-.0025,0),(0,.01),(.01,0)]):
        x,y=round(lon+dx,6),round(lat+dy,6)
        query=[('lon',x),('lat',y)]+[('property',p) for p in ('clay','sand','soc','bdod','phh2o')]
        query += [('depth',d) for d in ('0-5cm','5-15cm','15-30cm','30-60cm')]
        query += [('value',v) for v in ('mean','Q0.05','Q0.95')]
        payload=fetch(f'soil/campo_neighbor_{index}.json','https://rest.isric.org/soilgrids/v2.0/properties/query?'+urlencode(query))
        candidate=[]
        for layer in payload['properties']['layers']:
            unit=layer['unit_measure'];factor=unit['d_factor']
            for depth in layer['depths']:
                values=depth['values']
                candidate.append({'municipality_code':site['municipality_code'],'longitude':x,'latitude':y,
                                  'distance_from_original_m':distance(lat,lon,y,x)*1000,'property':layer['name'],'depth_cm':depth['label'],
                                  'unit':unit['target_units'],**{key:values[v]/factor if values.get(v) is not None else None for key,v in [('mean','mean'),('q05','Q0.05'),('q95','Q0.95')]},
                                  'support':'nearby_pixel_proxy_NOT_original_location','used_in_yield_model':False})
        count=sum(all(r[k] is not None for k in ('mean','q05','q95')) for r in candidate)
        attempts.append({'candidate':index,'longitude':x,'latitude':y,'complete_layers':count})
        print('Campo soil candidate',index,'complete',count,flush=True)
        if count==20:
            rows=candidate;break
    if rows:write_csv('campo_soil_neighbor.csv',rows)
    save_json(OUT/'campo_soil_resolution.json',{'original_point':site,'original_nodata_preserved':True,
             'attempts':attempts,'nearby_context_available':bool(rows),'exact_point_gap_resolved':False,
             'status':'nearby_context_only' if rows else 'no_valid_nearby_context',
             'distance_m':rows[0]['distance_from_original_m'] if rows else None})


if __name__=='__main__':main()
