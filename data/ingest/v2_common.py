"""Version-isolated, provenance-checked IO for the second research dataset."""
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from data.ingest.training_foundation import decode_json, save_json

RAW = Path('data/raw/training_v2')
OUT = Path('data/processed/training_v2')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(name, url):
    path = RAW / name
    manifest = path.with_suffix(path.suffix+'.manifest.json')
    if path.exists() and manifest.exists():
        meta = json.loads(manifest.read_bytes())
        if meta['url'] != url or meta['sha256'] != sha(path):
            raise ValueError(f'Cache mismatch {path}')
        return decode_json(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={'User-Agent':'GaiapusResearch/0.2','Accept':'application/json'}), timeout=90) as response:
                raw = response.read(20_000_001)
                if len(raw)>20_000_000:
                    raise ValueError('JSON budget exceeded')
                payload = decode_json(raw)
            path.write_bytes(raw)
            save_json(manifest, {'url':url, 'sha256':sha(path), 'bytes':len(raw), 'retrieved_at':datetime.now(timezone.utc).isoformat()})
            return payload
        except Exception:
            if attempt==3: raise
            time.sleep(2**attempt)


def write_csv(name, rows):
    rows = list(rows)
    if not rows: raise ValueError(f'No rows: {name}')
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def download_inmet(year):
    import zipfile
    path=RAW/'inmet'/f'{year}.zip'; path.parent.mkdir(parents=True,exist_ok=True)
    url=f'https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip'
    if path.exists():
        meta=json.loads(path.with_suffix('.manifest.json').read_bytes())
        if meta['url']!=url or sha(path)!=meta['sha256']:raise ValueError('INMET archive cache mismatch')
    if not path.exists():
        temporary=path.with_suffix('.part'); size=0
        with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=120) as response, temporary.open('wb') as target:
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>1_000_000_000: raise ValueError('INMET archive budget exceeded')
                target.write(block)
        with zipfile.ZipFile(temporary) as check: check.infolist()
        temporary.replace(path)
        save_json(path.with_suffix('.manifest.json'),{'url':url,'sha256':sha(path),'bytes':size,'retrieved_at':datetime.now(timezone.utc).isoformat()})
        print('INMET',year,'downloaded',size,flush=True)
    return path
