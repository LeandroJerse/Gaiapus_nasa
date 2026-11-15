"""Download SoilGrids point priors with quantiles, keeping native conversion metadata."""
import json
from urllib.parse import urlencode

from data.ingest.training_foundation import RAW, OUT, fetch, save_json, write_csv


def main():
    rows, failures = [], []
    for site in json.loads((RAW / 'locations.json').read_bytes()):
        code = site['municipality_code']
        query = [('lon', site['longitude']), ('lat', site['latitude'])]
        query += [('property', p) for p in ('clay', 'sand', 'soc', 'bdod', 'phh2o')]
        query += [('depth', d) for d in ('0-5cm', '5-15cm', '15-30cm', '30-60cm')]
        query += [('value', q) for q in ('mean', 'Q0.05', 'Q0.95')]
        try:
            payload = fetch(f'soilgrids_{code}.json', 'https://rest.isric.org/soilgrids/v2.0/properties/query?' + urlencode(query))
            point = payload['geometry']['coordinates']
            if abs(point[0]-site['longitude']) > .0001 or abs(point[1]-site['latitude']) > .0001:
                raise ValueError('Soil point differs from requested point')
            for layer in payload['properties']['layers']:
                unit = layer['unit_measure']
                factor = unit['d_factor']
                if factor <= 0:
                    raise ValueError('Invalid scale factor')
                for depth in layer['depths']:
                    values = depth['values']
                    if all(values.get(k) is not None for k in ('Q0.05', 'Q0.95')) and values['Q0.05'] > values['Q0.95']:
                        raise ValueError('Invalid uncertainty interval')
                    rows.append({'municipality_code': code, 'property': layer['name'], 'depth_cm': depth['label'],
                                 'unit': unit['target_units'], 'd_factor': factor,
                                 **{k: values.get(q)/factor if values.get(q) is not None else None for k, q in [('mean', 'mean'), ('q05', 'Q0.05'), ('q95', 'Q0.95')]},
                                 'role': 'spatial_model_prior_NOT_field_measurement', 'used_in_baseline': False,
                                 'quality_status': 'complete' if all(values.get(q) is not None for q in ('mean', 'Q0.05', 'Q0.95')) else 'source_nodata'})
        except Exception as exc:
            failures.append({'municipality_code': code, 'error': str(exc)})
    if rows:
        write_csv('soil_context.csv', rows)
    save_json(OUT / 'soil_quality.json', {'rows': len(rows), 'failures': failures,
              'complete_rows': sum(r['quality_status']=='complete' for r in rows),
              'source_nodata_rows': [{'municipality_code': r['municipality_code'], 'property': r['property'], 'depth_cm': r['depth_cm']} for r in rows if r['quality_status']=='source_nodata'],
              'depth_limit_cm': 60, 'point_support_not_municipal_average': True,
              'limitations': ['not_lab_analysis', 'not_current_soil_moisture', 'not_direct_available_water_capacity',
                              'not_direct_fertilizer_recommendation', 'static_map_not_historical_management']})
    print('Soil context:', len(rows), 'rows;', len(failures), 'failures', flush=True)


if __name__ == '__main__':
    main()
