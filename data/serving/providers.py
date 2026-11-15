"""Data providers for yield_service: offline (cached research files) and live (APIs with an incremental cache).

Live sources: NASA POWER daily meteorology (~2 days late), IMERG Late V07 rain from GES DISC
(~1 day late, needs EARTHDATA_TOKEN), ERA5 radiation from the Open-Meteo archive (~6 days
late) and SIDRA/PAM published yields. Everything fetched is cached under `cache_dir`, so a
daily run only asks for the new days.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path

from data.ingest import cohort_v5 as v5
from data.ingest.cohort_v7 import power_root
from data.ingest.water_balance_run import ROOT
from data.serving import live_features as live


def locations():
    out = {}
    for path in (ROOT / 'data/raw/training_v6/locations.json', ROOT / 'data/raw/training_v7/locations.json'):
        if path.exists():
            for item in json.loads(path.read_text(encoding='utf-8')):
                out[item['municipality_code']] = {'latitude': item['latitude'], 'longitude': item['longitude']}
    return out


class Offline:
    """Cached research inputs (labels only up to 2021: the sealed test years are never read)."""

    def __init__(self):
        import pandas as pd
        self.locations = locations()
        self.labels = {}
        for state_file in (ROOT / 'data/raw/training_v6').glob('pam_*_*.json'):
            if state_file.name.endswith('.manifest.json'):
                continue
            crop = state_file.stem.split('_')[1]
            for item in v5.parse_labels(json.loads(state_file.read_bytes()), crop):
                if item['label_valid']:
                    self.labels.setdefault((crop, item['municipality_code']), {})[item['harvest_year']] = item['yield_kg_ha']
        v7 = pd.read_csv(ROOT / 'data/raw/training_v7/labels_all.csv', dtype={'municipality_code': str})
        for (crop, code), part in v7[v7.label_valid].groupby(['crop', 'municipality_code']):
            self.labels.setdefault((crop, code), {}).update(dict(zip(part.harvest_year, part.yield_kg_ha)))

    def weather(self, code, first, last):
        """Only the requested window is parsed (ET0 is the costly part); the cached file covers 2001-2024."""
        payload = json.loads((power_root(code) / f'power/power_{code}.json').read_bytes())
        weather, elevation = v5.parse_power(payload, code, max(first, date(2001, 1, 1)), min(last, date(2024, 12, 31)))
        return weather, elevation, self.locations[code]

    def history(self, crop, code, years):
        series = self.labels.get((crop, code), {})
        return {y: series[y] for y in years if y in series}


class Live:
    """Live inputs with a per-municipality cache; `mode='operational'` swaps rain and radiation."""

    def __init__(self, cache_dir, mode='operational', token=None):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.token = token or os.environ.get('EARTHDATA_TOKEN')
        self.locations = locations()
        self.labels = {}
        self.history_times = {}
        self.granules = {}  # IMERG Late granule (with minor version) used for each day

    @staticmethod
    def _stamp(path, **extra):
        """Sidecar with the retrieval time of a cache file (provenance for the prospective registry)."""
        from datetime import datetime, timezone
        meta = path.with_name(path.name + '.retrieved.json')
        meta.write_text(json.dumps({'retrieved_at': datetime.now(timezone.utc).isoformat(), **extra}), encoding='utf-8')

    @staticmethod
    def _retrieved(path):
        """Sidecar retrieval time; files cached before the registry started fall back to the file modification time."""
        from datetime import datetime, timezone
        meta = path.with_name(path.name + '.retrieved.json')
        if meta.exists():
            return {**json.loads(meta.read_text(encoding='utf-8')), 'basis': 'recorded'}
        return {'retrieved_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), 'basis': 'file_mtime'}

    def _power(self, code, first, last):
        path = self.cache / 'power' / f'{code}_{first}_{last}.json'
        if not path.exists():
            location = self.locations[code]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(live.fetch_power(location['latitude'], location['longitude'], first, last)), encoding='utf-8')
            self._stamp(path)
        return json.loads(path.read_text(encoding='utf-8'))

    def _late(self, days):
        """IMERG Late for every cached location at once, one request per day, cached per day."""
        points = sorted({(v['latitude'], v['longitude']) for v in self.locations.values()})
        out = {}
        for day in days:
            path = self.cache / 'imerg_late' / f'{day}.json'
            if not path.exists():
                values = live.fetch_imerg_late(points, day, self.token)
                granule = values.pop('_granule')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'granule': granule, 'values': {f'{k[0]},{k[1]}': v for k, v in values.items()}}), encoding='utf-8')
                self._stamp(path, granule=granule)
            cached = json.loads(path.read_text(encoding='utf-8'))
            self.granules[day.isoformat()] = cached['granule']
            out[day.isoformat()] = cached['values']
        return out

    def _era5(self, code, days):
        """ERA5 radiation for `days` only, from one growing cache file per municipality (fetches just the missing days)."""
        path = self.cache / 'era5' / f'{code}.json'
        cached = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        missing = sorted(d for d in days if cached.get(d) is None)
        if missing:
            location = self.locations[code]
            first, last = date.fromisoformat(missing[0]), date.fromisoformat(missing[-1])
            values = live.fetch_era5_radiation([(location['latitude'], location['longitude'])], first, last)
            new = {d: v for d, v in next(iter(values.values())).items() if v is not None}
            cached.update(new)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cached, sort_keys=True), encoding='utf-8')
            from datetime import datetime, timezone
            times_path = path.with_name(path.name + '.retrieved.json')
            times = json.loads(times_path.read_text(encoding='utf-8')) if times_path.exists() else {}
            times.update({d: datetime.now(timezone.utc).isoformat() for d in new})
            times_path.write_text(json.dumps(times, sort_keys=True), encoding='utf-8')
        return {d: cached.get(d) for d in days}

    def provenance(self, crop, code, first, last, history_years):
        """Product, version and retrieval time of every input of one estimate (read-only, from the caches)."""
        import hashlib
        out = []
        power = self.cache / 'power' / f'{code}_{first}_{last}.json'
        if power.exists():
            stamp = self._retrieved(power)
            out.append({'source': 'NASA POWER daily (T2M_MIN, T2M_MAX, RH2M, WS2M, T2MDEW, PS, ALLSKY_SFC_SW_DWN)',
                        'period': [str(first), str(last)], 'retrieved_at': stamp['retrieved_at'], 'retrieved_at_basis': stamp['basis'],
                        'sha256': hashlib.sha256(power.read_bytes()).hexdigest()})
        if self.mode == 'operational':
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            granules, times, bases = {}, [], set()
            for day in days:
                path = self.cache / 'imerg_late' / f'{day}.json'
                if path.exists():
                    granules[day.isoformat()] = json.loads(path.read_text(encoding='utf-8'))['granule']
                    stamp = self._retrieved(path)
                    times.append(stamp['retrieved_at'])
                    bases.add(stamp['basis'])
            versions = sorted({g.split('.')[-2] for g in granules.values() if g})
            out.append({'source': 'GPM IMERG Late daily (GES DISC)', 'period': [str(first), str(last)], 'versions': versions,
                        'granules_sha256': hashlib.sha256(json.dumps(granules, sort_keys=True).encode()).hexdigest(),
                        'days': len(granules), 'retrieved_at_basis': 'recorded' if bases == {'recorded'} else 'file_mtime',
                        'retrieved_at_min': min([t for t in times if t], default=None),
                        'retrieved_at_max': max([t for t in times if t], default=None)})
            era5 = self.cache / 'era5' / f'{code}.json'
            times_path = era5.with_name(era5.name + '.retrieved.json')
            if era5.exists():
                times = json.loads(times_path.read_text(encoding='utf-8')) if times_path.exists() else {}
                used = [d for d in times if str(first) <= d <= str(last)]
                out.append({'source': 'ERA5 shortwave radiation (Open-Meteo archive), only days without POWER radiation',
                            'days': len(used), 'retrieved_at_basis': 'recorded',
                            'retrieved_at_max': max((times[d] for d in used), default=None)})
        labels = self.labels.get((crop, code, tuple(history_years)))
        out.append({'source': 'IBGE PAM published yields (SIDRA)', 'years': sorted(labels) if labels else [],
                    'retrieved_at': self.history_times.get((crop, code, tuple(history_years))), 'retrieved_at_basis': 'recorded',
                    'sha256': hashlib.sha256(json.dumps(labels or {}, sort_keys=True).encode()).hexdigest()})
        return out

    def weather(self, code, first, last):
        payload = self._power(code, first, last)
        weather, elevation = v5.parse_power(payload, code, first, last)
        location = self.locations[code]
        if self.mode != 'operational':
            return weather, elevation, location
        key = f'{location["latitude"]},{location["longitude"]}'
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        late = self._late(days)
        rain = {day: values.get(key) for day, values in late.items()}
        # POWER radiation (CERES) where it exists, ERA5 only for the recent days CERES has not reached yet.
        gaps = [d for d, row in weather.items() if row['radiation_mj_m2'] is None]
        radiation = {d: row['radiation_mj_m2'] for d, row in weather.items()}
        radiation.update(self._era5(code, gaps) if gaps else {})
        self.radiation_era5_days = len(gaps)
        return live.swap_sources(weather, location['latitude'], elevation, rain=rain, radiation=radiation), elevation, location

    def history(self, crop, code, years):
        from datetime import datetime, timezone
        key = (crop, code, tuple(years))
        if key not in self.labels:
            self.labels[key] = live.fetch_pam_history(crop, code, years)
            self.history_times[key] = datetime.now(timezone.utc).isoformat()
        return self.labels[key]
