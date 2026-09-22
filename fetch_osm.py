#!/usr/bin/env python3
"""Download raw OpenStreetMap data (Overpass API) for the coast Vigo -> Albufeira.

Output goes to data/raw/*.json; build_db.py turns it into landmarks.db.
Data (c) OpenStreetMap contributors, ODbL.
"""
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

RAW = pathlib.Path(__file__).parent / "raw"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "ThreePointFix-hobby/0.1 (personal navigation-training app)"

# south, west, north, east. Raw files are written per area: raw/<prefix><query>.json
AREAS = {
    "": "36.9,-9.6,42.35,-8.05",              # Ria de Vigo (incl. Cies) down and round to Albufeira/Vilamoura
    "madeira_": "32.35,-17.35,33.20,-16.20",  # Madeira, Porto Santo, Desertas
    "nl_": "52.0,4.0,53.6,7.25",              # Netherlands: Waddenzee, IJsselmeer/Markermeer, North Sea coast
}

# Lakes are not "coastline" in OSM; their outlines are fetched separately and used as shore.
LAKES = {
    "nl_": "IJsselmeer|Markermeer|IJmeer|Gooimeer|Eemmeer|Ketelmeer|Zwarte Meer|Veluwemeer|Wolderwijd|Nuldernauw|Nijkerkernauw",
}


def queries(BBOX):
  return {
    # lighthouses, breakwater/pier-head lights, charted landmarks
    "lights": f"""(
        nwr["man_made"="lighthouse"]({BBOX});
        nwr["seamark:type"~"^(light_major|light_minor|landmark)$"]({BBOX});
        nwr["seamark:type"~"^beacon_"][~"^seamark:light"~"."]({BBOX});
      );out center tags;""",
    "places": f"""node["place"~"^(city|town|village)$"]({BBOX});out body;""",
    "churches": f"""nwr["amenity"="place_of_worship"]["name"]({BBOX});out center tags;""",
    "other": f"""(
        nwr["historic"~"^(castle|fort|fortress|tower)$"]["name"]({BBOX});
        nwr["man_made"~"^(tower|chimney|water_tower|windmill|obelisk)$"]["name"]({BBOX});
      );out center tags;""",
    # which of the places are Spanish (everything else in the box is Portugal)
    "places_es": """area["ISO3166-1"="ES"][admin_level=2]->.es;
        node["place"~"^(city|town|village)$"](area.es)(41.8,-9.6,42.35,-8.05);out ids;""",
    # and which are German (the Ems box straddles the border)
    "places_de": """area["ISO3166-1"="DE"][admin_level=2]->.de;
        node["place"~"^(city|town|village)$"](area.de)(53.0,6.5,53.6,7.25);out ids;""",
    "coastline": f"""way["natural"="coastline"]({BBOX});out geom;""",
  }


def lake_query(names, BBOX):
    return f"""(
        relation["natural"="water"]["name"~"^({names})$"]({BBOX});
        way["natural"="water"]["name"~"^({names})$"]({BBOX});
      );out geom;"""


def fetch(name: str, body: str) -> None:
    out = RAW / f"{name}.json"
    if out.exists() and "--force" not in sys.argv:
        print(f"{name}: cached")
        return
    data = urllib.parse.urlencode({"data": f"[out:json][timeout:300];{body}"}).encode()
    for attempt in range(8):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=400) as r:
                payload = r.read()
            parsed = json.loads(payload)  # busy servers answer with an HTML error page
            if parsed.get("remark"):
                raise RuntimeError(parsed["remark"])
            out.write_bytes(payload)
            print(f"{name}: {len(parsed['elements'])} elements from {url}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"{name}: attempt {attempt + 1} failed ({e}); retrying")
            time.sleep(15 + 10 * attempt)
    sys.exit(f"{name}: giving up")


if __name__ == "__main__":
    RAW.mkdir(exist_ok=True)
    for prefix, bbox in AREAS.items():
        for n, q in queries(bbox).items():
            if (n == "places_es" and prefix) or (n == "places_de" and prefix != "nl_"):
                continue   # border queries only where a box straddles one
            fetch(prefix + n, q)
            time.sleep(3)
        if prefix in LAKES:
            fetch(prefix + "lakes", lake_query(LAKES[prefix], bbox))
