import calendar
import hashlib
import json
import math
import re
import struct
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

source = Path("data/raw/imerg/ncar-monthly-kerchunk.json")
out = source.parent / "monthly"
out.mkdir(exist_ok=True)
refs = json.loads(source.read_bytes())["refs"]
metadata = json.loads(refs["precipitation/.zarray"])
assert metadata["chunks"] == [1, 145, 1800] and metadata["dtype"] == "<f4"
assert metadata["filters"][0]["id"] == "zlib" and metadata["compressor"] is None
assert json.loads(refs["precipitation/.zattrs"])["units"] == "mm/hr"
lon_i, lat_i = 1317, 710
chunk_i, lon_within = divmod(lon_i, 145)
offset_in_chunk = 4 * (lon_within * 1800 + lat_i)
base = "https://data.gdex.ucar.edu/d736000"


def get_range(url, offset, length):
    expected = f"bytes {offset}-{offset + length - 1}"
    headers = {"Range": expected.replace(" ", "="), "Accept-Encoding": "identity"}
    with urlopen(Request(url, headers=headers), timeout=15) as reply:
        if reply.status != 206 or not reply.headers.get("Content-Range", "").startswith(expected + "/"):
            raise ValueError("Unexpected HTTP range")
        raw = reply.read(length + 1)
    if len(raw) != length:
        raise ValueError("Unexpected compressed chunk length")
    return raw


for axis, index, center in (("lon", lon_i, -48.25), ("lat", lat_i, -18.95)):
    spec = json.loads(refs[f"{axis}/.zarray"])
    assert spec["dtype"] == "<f4" and spec["filters"][0]["id"] == "zlib"
    file, offset, length = refs[f"{axis}/0"]
    axis_path = out / f"{axis}.chunk"
    raw = axis_path.read_bytes() if axis_path.exists() else get_range(
        base + "/" + file.split("/d736000/", 1)[1], offset, length
    )
    assert len(raw) == length and abs(struct.unpack_from("<f", zlib.decompress(raw), index * 4)[0] - center) < 0.0001
    if not axis_path.exists():
        axis_path.write_bytes(raw)

jobs = {}
for key, value in refs.items():
    if not re.fullmatch(rf"precipitation/\d+\.{chunk_i}\.0", key):
        continue
    file, offset, length = value
    found = re.search(r"\.((?:201[6-9]|202[0-4])\d{2})01-S000000-E235959\.\d{2}\.V07B\.HDF5$", file)
    if found:
        month = found[1][:4] + "-" + found[1][4:]
        assert month not in jobs
        jobs[month] = (file, offset, length)
assert len(jobs) == 108

def fetch(item):
    month, (file, offset, length) = item
    url = base + "/" + file.split("/d736000/", 1)[1]
    path = out / (month + ".chunk")
    headers = {"Range": f"bytes={offset}-{offset + length - 1}", "Accept-Encoding": "identity"}
    try:
        if path.exists():
            raw = path.read_bytes()
        else:
            for attempt in range(3):
                try:
                    raw = get_range(url, offset, length)
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
        if len(raw) != length:
            raise ValueError("Unexpected compressed chunk length")
        chunk = zlib.decompress(raw)
        if len(chunk) != 145 * 1800 * 4:
            raise ValueError("Unexpected decompressed chunk length")
        rate = struct.unpack_from("<f", chunk, offset_in_chunk)[0]
        if not math.isfinite(rate) or (rate < 0 and abs(rate + 9999.9) > .01):
            raise ValueError("Invalid precipitation")
        if not path.exists():
            path.write_bytes(raw)
        year, mo = map(int, month.split("-"))
        total = rate * 24 * calendar.monthrange(year, mo)[1] if rate >= 0 else None
        return {"month":month,"reference_monthly_mm":total,"url":url,"range":headers["Range"],
                "sha256":hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, zlib.error) as error:
        return {"month":month,"reference_monthly_mm":None,"url":url,"error":str(error)}

with ThreadPoolExecutor(max_workers=8) as pool:
    rows = list(pool.map(fetch, sorted(jobs.items())))
(out / "reference.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
print("Monthly granules:", sum(r["reference_monthly_mm"] is not None for r in rows), "/", len(rows))
raise SystemExit(0 if all(row["reference_monthly_mm"] is not None for row in rows) else 2)
