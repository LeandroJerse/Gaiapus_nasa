"""Build a local crop atlas from the FAOSTAT and SIDRA snapshots."""

import csv
import gzip
import html
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

RAW = Path("data/raw/crop_atlas")
OUT = Path("data/processed/crop_atlas")
YEAR = "2024"
FAO_NAMES = {"56": "Milho", "236": "Soja", "656": "Café verde", "15": "Trigo",
             "27": "Arroz", "44": "Cevada", "83": "Sorgo", "156": "Cana-de-açúcar",
             "486": "Banana", "242": "Amendoim", "176": "Mandioca", "116": "Batata"}
REGIONS = {"1": "Norte", "2": "Nordeste", "3": "Sudeste", "4": "Sul", "5": "Centro-Oeste"}
COLORS = {"Soja": "#63b494", "Milho": "#e9ba68", "Café verde": "#db957a",
          "Trigo": "#dbabdb", "Arroz": "#88bded", "Cana-de-açúcar": "#afd973"}


def load(name):
    raw = (RAW / (name + ".raw")).read_bytes()
    return json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)


def fao_rows(domain, element):
    with zipfile.ZipFile(RAW / (domain + ".raw")) as archive:
        source = next(name for name in archive.namelist() if "(Normalized)" in name)
        with io.TextIOWrapper(archive.open(source), encoding="latin1") as stream:
            for line in stream:
                if f',"{YEAR}","{YEAR}",' not in line:
                    continue
                row = next(csv.reader([line]))
                if row[6] == element and row[11] and row[4].lstrip("'").startswith("01"):
                    yield row


def world_data():
    places, rows = {}, {}
    for domain, element, column in [("QCL", "5312", "area_ha"), ("QV", "152", "gross_kint")]:
        for row in fao_rows(domain, element):
            area, m49, name, crop, _, label = row[:6]
            if domain == "QCL" and row[10] != "ha":
                raise ValueError("Unexpected area unit")
            if domain == "QV" and row[10] != "1000 Int$":
                raise ValueError("Unexpected value unit")
            if float(row[11]) <= 0:
                continue
            places[area] = (name, m49.lstrip("'").zfill(3))
            record = rows.setdefault((area, crop), {"place": name, "code": area, "m49": places[area][1],
                                                     "crop": FAO_NAMES.get(crop, label), "crop_code": crop})
            if column in record:
                raise ValueError(f"Duplicate FAOSTAT key: {area}, {crop}, {column}")
            record[column] = float(row[11])
            record[column + "_flag"] = row[12]
    return places, list(rows.values())


def brazil_data():
    output = {}
    for level in ("n1", "n2", "n3", "n6"):
        for source in (level + "_all", level):
            for row in load(source)[1:]:
                crop = row["D4C"]
                if crop == "0" or row["V"] in {"-", "...", "..", "X", "x", ""}:
                    continue
                column = {"216": "area_ha", "214": "production_t", "112": "yield_kg_ha",
                          "215": "gross_kbrl"}[row["D2C"]]
                key = (level, row["D1C"], crop)
                output.setdefault(key, {"level": level, "code": row["D1C"], "place": row["D1N"],
                                        "crop_code": crop, "crop": row["D4N"]})[column] = float(row["V"])
    return list(output.values())


def ranked(rows, key):
    return sorted(
        (r for r in rows if r.get(key, 0) > 0 and r["crop_code"] not in {"40140", "40141"}),
        key=lambda r: (-r[key], r["crop"]),
    )


def path_data(geometry, project):
    polygons = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        polygons = [polygons]
    return " ".join("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (project(*p[:2]) for p in ring)) + " Z"
                    for polygon in polygons for ring in polygon)


def svg(features, get_id, locations, project, viewbox, metric, scope):
    paths = []
    for feature in features:
        code = get_id(feature["properties"])
        if code is None:
            name, records = feature["properties"].get("ADMIN", "Sem dados"), []
        else:
            name, records = locations.get(code, ("Sem dados", []))
        top = ranked(records, metric)[:3]
        color = COLORS.get(top[0]["crop"], "#a39bc6") if top else "#344257"
        detail = name + " · " + (" / ".join(f"{r['crop']} ({number(r[metric])})" for r in top) or "Sem dados")
        d = path_data(feature["geometry"], project)
        paths.append(f'<path d="{d}" fill="{color}" data-scope="{scope}" data-code="{html.escape(code or "")}">'
                     f'<title>{html.escape(detail)}</title></path>')
    return f'<svg viewBox="{viewbox}" role="img" aria-label="Mapa de cultivos">{"".join(paths)}</svg>'


def number(value):
    return f"{value:,.0f}".replace(",", ".")


def write_csv(name, rows, cols):
    with (OUT / name).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(row, year=YEAR) for row in rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    places, global_rows = world_data()
    br_rows = brazil_data()
    global_index, brazil_index = defaultdict(list), defaultdict(list)
    for row in global_rows:
        global_index[row["code"]].append(row)
    for row in br_rows:
        brazil_index[row["level"] + ":" + row["code"]].append(row)
    indexed_world = {m49: code for code, (_, m49) in places.items() if int(code) < 5000}
    world_locations = {code: (name, global_index[code]) for code, (name, _) in places.items()}
    brazil_locations = {key: (rows[0]["place"], rows) for key, rows in brazil_index.items()}
    def world_id(p):
        return indexed_world.get(p.get("ISO_N3_EH")) or indexed_world.get(p.get("ISO_N3"))
    world = load("world")["features"]
    brazil = load("regions")["features"]
    states = json.loads(Path("gaiapus-mvp/data/states.geojson").read_bytes())["features"]
    global_map = svg(world, world_id, world_locations, lambda x, y: ((x + 180) * 3, (85 - y) * 3),
                     "0 0 1080 500", "area_ha", "world")
    regions_map = svg(brazil, lambda p: "n2:" + str(p["codarea"]), brazil_locations,
                      lambda x, y: ((x + 75) * 12, (7 - y) * 12), "0 0 430 505", "area_ha", "br")
    states_map = svg(states, lambda p: "n3:" + str(p["codarea"]), brazil_locations,
                     lambda x, y: ((x + 75) * 12, (7 - y) * 12), "0 0 430 505", "area_ha", "br")
    write_csv("faostat-2024.csv", global_rows,
              ["year", "place", "code", "m49", "crop_code", "crop", "area_ha", "area_ha_flag",
               "gross_kint", "gross_kint_flag"])
    write_csv("pam-2024.csv", br_rows, ["year", "level", "code", "place", "crop_code", "crop",
              "area_ha", "production_t", "yield_kg_ha", "gross_kbrl"])
    payload = {}
    for scope, locations, value_key in [("world", world_locations, "gross_kint"),
                                         ("br", brazil_locations, "gross_kbrl")]:
        payload[scope] = {code: {"name": name, "area": ranked(rows, "area_ha")[:8],
                                 "value": ranked(rows, value_key)[:8]}
                          for code, (name, rows) in locations.items()}
    template = Path(__file__).with_name("crop_atlas.html").read_text(encoding="utf-8")
    result = template.replace("<!-- WORLD -->", global_map).replace("<!-- REGIONS -->", regions_map)
    result = result.replace("<!-- STATES -->", states_map)
    result = result.replace("/* DATA */", json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"))
    (OUT / "atlas.html").write_text(result, encoding="utf-8")
    print(f"FAOSTAT: {len(places)} geographical entries and {len(global_rows)} crop rows; PAM: {len(br_rows)} crop rows.")
    for code in ("n1:1", "n2:3", "n3:31", "n6:3170206", "n6:5218805", "n6:2903201"):
        print(code, [(r["crop_code"], r["area_ha"]) for r in ranked(brazil_index[code], "area_ha")[:5]])


if __name__ == "__main__":
    main()
