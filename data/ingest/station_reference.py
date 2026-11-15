"""Preserve official INMET 2024 archive and select nearby MG/MT stations."""
import csv
import hashlib
import io
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from data.ingest.training_foundation import OUT, RAW, save_json, write_csv


def distance(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    dlat, dlon = b-a, math.radians(lon2-lon1)
    return 6371 * 2 * math.asin(min(1, math.sqrt(math.sin(dlat/2)**2 + math.cos(a)*math.cos(b)*math.sin(dlon/2)**2)))


def main():
    root = RAW / 'inmet'
    root.mkdir(exist_ok=True)
    archive = root / '2024.zip'
    url = 'https://portal.inmet.gov.br/uploads/dadoshistoricos/2024.zip'
    if not archive.exists():
        temp = archive.with_suffix('.part')
        total = 0
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120) as response, temp.open('wb') as stream:
            while block := response.read(1024*1024):
                total += len(block)
                if total > 1_000_000_000:
                    raise ValueError('INMET archive exceeds 1 GB collection budget')
                stream.write(block)
                if total % (20*1024*1024) == 0:
                    print('INMET downloaded MB', total//1024//1024, flush=True)
        with zipfile.ZipFile(temp) as check:
            check.infolist()
        temp.replace(archive)
        save_json(root / 'manifest.json', {'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                  'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'bytes': total})
    sites = json.loads((RAW / 'locations.json').read_bytes())
    catalog, links, daily = [], [], []
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if not name.lower().endswith('.csv') or not any(f'_{uf}_' in name for uf in ('MG', 'MT')):
                continue
            with zipped.open(name) as member:
                header = [member.readline().decode('latin1').strip() for _ in range(8)]
            meta = {line.split(';')[0].rstrip(':'): line.split(';')[1] for line in header if ';' in line}
            try:
                catalog.append({'station_id': meta['CODIGO (WMO)'], 'name': meta['ESTACAO'], 'uf': meta['UF'],
                                'lat': float(meta['LATITUDE'].replace(',', '.')), 'lon': float(meta['LONGITUDE'].replace(',', '.')),
                                'archive_member': name})
            except (KeyError, ValueError) as exc:
                raise ValueError(f'Unexpected INMET header: {header}') from exc
        selected = {}
        for site in sites:
            candidates = [r for r in catalog if r['uf'] == ('MG' if site['state_code']=='31' else 'MT')]
            nearest = min(candidates, key=lambda r: distance(site['latitude'], site['longitude'], r['lat'], r['lon']))
            km = distance(site['latitude'], site['longitude'], nearest['lat'], nearest['lon'])
            links.append({'municipality_code': site['municipality_code'], 'station_id': nearest['station_id'],
                          'distance_km_to_weather_point': round(km, 2), 'within_75km': km <= 75,
                          'selection': 'nearest_geographic_NOT_quality_selected'})
            selected[nearest['station_id']] = nearest
        for station_id, station in selected.items():
            raw = zipped.read(station['archive_member'])
            (root / f'{station_id}_2024.csv').write_bytes(raw)
            lines = raw.decode('latin1').splitlines()
            reader = csv.DictReader(lines[8:], delimiter=';')
            groups = {}
            rain_key = next(k for k in reader.fieldnames if 'PRECIPITA' in k)
            for row in reader:
                day = row[reader.fieldnames[0]].replace('/', '-')
                hour = row[reader.fieldnames[1]]
                if (day, hour) in groups:
                    raise ValueError('Duplicate station hour')
                raw_value = row[rain_key].replace(',', '.')
                value = float(raw_value) if raw_value else None
                groups[(day, hour)] = value if value is not None and value >= 0 else None
            by_day = {}
            for (day, _), value in groups.items():
                by_day.setdefault(day, []).append(value)
            for day, values in sorted(by_day.items()):
                valid = [v for v in values if v is not None]
                daily.append({'station_id': station_id, 'day_utc': day, 'rain_mm': sum(valid) if len(valid)==24 else None,
                              'valid_hours': len(valid), 'expected_hours': 24,
                              'aggregation': 'reported_UTC_hour_calendar_day_interval_alignment_pending'})
    write_csv('inmet_station_catalog_2024.csv', catalog)
    write_csv('inmet_station_links.csv', links)
    write_csv('inmet_daily_2024.csv', daily)
    save_json(OUT / 'inmet_quality.json', {'links': links, 'days': len(daily),
              'complete_days': sum(r['valid_hours']==24 for r in daily),
              'status': 'collected_and_hourly_coverage_checked_NOT_satellite_accuracy_validated',
              'next': 'Confirm accumulation interval convention, query satellite at station coordinates, compare only complete aligned days.'})
    print('INMET stations', len(selected), 'daily rows', len(daily), flush=True)


if __name__ == '__main__':
    main()
