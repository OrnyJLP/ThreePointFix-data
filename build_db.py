#!/usr/bin/env python3
"""Build landmarks.db (SQLite) from the raw OpenStreetMap extracts in data/raw/.

    python3 fetch_osm.py      # once, or with --force to refresh
    python3 build_db.py       # writes data/landmarks.db

Data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import collections
import datetime
import json
import math
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).parent
RAW = HERE / "raw"
OUT = HERE / "landmarks.db"

R = 6_371_008.8
NM = 1852.0

# How far from the waterline something may stand and still be offered as a landmark (metres).
MAX_COAST_M = {"lighthouse": 3000, "harbour_light": 3000, "landmark": 3000,
               "church": 700, "fort": 500, "tower": 1000}
TOWN_COAST_M = {"city": 5000, "town": 3000, "village": 1200}
TOWN_RADIUS_NM = 8          # keep in step with LandmarkDb.GetLandmarksNear
MIN_LANDMARKS_PER_TOWN = 3

# Harbours sailors look for that OSM has no coastal city/town/village node for (the city of Aveiro
# lies 8 km up the lagoon, Vilamoura is a resort). Positioned on a landmark: (town name, landmark name).
EXTRA_TOWNS = [("Aveiro (Barra)", "Farol de Aveiro"), ("Vilamoura", "Vilamoura")]

REGIONS = [  # id, name, country, sort
    (1, "Galicia – Rías Baixas", "ES", 1),
    (2, "Norte – Minho to Douro", "PT", 2),
    (3, "Centro – Aveiro to Peniche", "PT", 3),
    (4, "Lisboa & Setúbal", "PT", 4),
    (5, "Alentejo", "PT", 5),
    (6, "Algarve", "PT", 6),
    (7, "Madeira & Porto Santo", "PT", 7),
]


def region_for(lat: float, spanish: bool, lon: float = 0.0) -> int:
    if lon < -12:
        return 7
    if spanish:
        return 1
    if lat >= 40.95:
        return 2
    if lat >= 39.20:
        return 3
    if lat >= 38.40:
        return 4
    if lat >= 37.44:
        return 5
    return 6


def load(name):
    """All areas together: raw/lights.json + raw/madeira_lights.json + ..."""
    elements = []
    for f in sorted(RAW.glob(f"*{name}.json")):
        if f.name == f"{name}.json" or f.name.endswith(f"_{name}.json"):
            elements += json.loads(f.read_text())["elements"]
    return elements


def pos(e):
    if "lat" in e:
        return e["lat"], e["lon"]
    c = e.get("center")
    return (c["lat"], c["lon"]) if c else None


def dist_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------- coastline index
CELL = 0.01  # degrees


class Coast:
    def __init__(self, ways):
        self.grid = collections.defaultdict(list)
        n = 0
        for w in ways:
            g = w.get("geometry") or []
            for a, b in zip(g, g[1:]):
                # long segments are split so that indexing by end points is good enough
                steps = max(1, int(max(abs(a["lat"] - b["lat"]), abs(a["lon"] - b["lon"])) / (CELL / 2)))
                for i in range(steps):
                    la1 = a["lat"] + (b["lat"] - a["lat"]) * i / steps
                    lo1 = a["lon"] + (b["lon"] - a["lon"]) * i / steps
                    la2 = a["lat"] + (b["lat"] - a["lat"]) * (i + 1) / steps
                    lo2 = a["lon"] + (b["lon"] - a["lon"]) * (i + 1) / steps
                    seg = (la1, lo1, la2, lo2)
                    for key in {self._key(la1, lo1), self._key(la2, lo2)}:
                        self.grid[key].append(seg)
                    n += 1
        print(f"coastline: {len(ways)} ways, {n} segments")

    @staticmethod
    def _key(lat, lon):
        return int(math.floor(lat / CELL)), int(math.floor(lon / CELL))

    def distance(self, lat, lon, limit_m):
        """Distance to the nearest coastline segment, or None when further than limit_m."""
        reach = int(limit_m / (CELL * 111_000 * math.cos(math.radians(lat)))) + 2
        ky, kx = self._key(lat, lon)
        coslat = math.cos(math.radians(lat))
        best = None
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                for la1, lo1, la2, lo2 in self.grid.get((ky + dy, kx + dx), ()):
                    ax, ay = math.radians(lo1 - lon) * coslat * R, math.radians(la1 - lat) * R
                    bx, by = math.radians(lo2 - lon) * coslat * R, math.radians(la2 - lat) * R
                    vx, vy = bx - ax, by - ay
                    L2 = vx * vx + vy * vy
                    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / L2))
                    d = math.hypot(ax + t * vx, ay + t * vy)
                    if best is None or d < best:
                        best = d
        return best if best is not None and best <= limit_m else None


# ---------------------------------------------------------------- landmark helpers
def light_description(t):
    """'Fl(3) W 15s · 57 m · 24 NM' from seamark:light:* tags (first sector if sectored)."""
    def g(key):
        return t.get(f"seamark:light:{key}") or t.get(f"seamark:light:1:{key}")
    ch = g("character")
    if not ch:
        return None
    grp, col, per = g("group"), g("colour"), g("period")
    s = ch + (f"({grp})" if grp else "")
    if col:
        s += " " + "".join({"white": "W", "red": "R", "green": "G", "yellow": "Y", "blue": "Bu"}.get(c, c[:1].upper())
                            for c in col.split(";"))
    if per:
        s += f" {per}s"
    bits = [s]
    if g("height"):
        bits.append(f"{g('height')} m")
    if g("range"):
        bits.append(f"{g('range')} NM")
    return " · ".join(bits)


def light_range(t):
    best = 0.0
    for k, v in t.items():
        if k.startswith("seamark:light:") and k.endswith("range"):
            for part in str(v).replace(",", ";").split(";"):
                try:
                    best = max(best, float(part))
                except ValueError:
                    pass
    return best


def light_colour_word(t):
    col = t.get("seamark:light:colour") or t.get("seamark:light:1:colour") or ""
    return col.split(";")[0].capitalize()


def main():
    coast = Coast(load("coastline"))
    spanish_places = {e["id"] for e in load("places_es")}

    # ------------------------------------------------------------ towns
    towns = []
    for e in load("places"):
        t = e["tags"]
        name = t.get("name")
        if not name:
            continue
        d = coast.distance(e["lat"], e["lon"], TOWN_COAST_M[t["place"]])
        if d is None:
            continue
        towns.append({"name": name, "lat": e["lat"], "lon": e["lon"], "place": t["place"],
                      "region": region_for(e["lat"], e["id"] in spanish_places, e["lon"])})
    print(f"coastal towns (before landmark check): {len(towns)}")

    # ------------------------------------------------------------ landmarks
    marks = []
    seen = set()

    def add(e, kind, name, desc, rank):
        key = (e["type"], e["id"])
        p = pos(e)
        if key in seen or p is None or not name:
            return
        if coast.distance(p[0], p[1], MAX_COAST_M[kind]) is None:
            return
        seen.add(key)
        marks.append({"osm": f"{e['type']}/{e['id']}", "kind": kind, "name": name.strip(), "desc": desc,
                      "lat": p[0], "lon": p[1], "rank": rank})

    for e in load("lights"):
        t = e["tags"]
        name = t.get("name") or t.get("seamark:name")
        desc = light_description(t)
        st = t.get("seamark:type", "")
        if st == "landmark" and t.get("man_made") != "lighthouse":
            cat = (t.get("seamark:landmark:category") or t.get("man_made") or "landmark").replace("_", " ")
            add(e, "landmark", name or cat.capitalize(), cat if name else None, 2)
            continue
        # man_made=lighthouse without any seamark data is only trusted when it is called a lighthouse;
        # pier heads and marina moles are often tagged that way too
        called_lighthouse = any(w in (name or "").lower() for w in ("farol ", "faro ", "lighthouse"))
        major = st == "light_major" or light_range(t) >= 10 or \
            (t.get("man_made") == "lighthouse" and st == "" and (called_lighthouse or not name))
        kind = "lighthouse" if major else "harbour_light"
        if not name:
            if not desc:
                continue   # nothing to recognise it by
            name = f"{light_colour_word(t)} light".strip().capitalize() if kind == "harbour_light" else "Lighthouse"
        # rank: richer records win when de-duplicating
        add(e, kind, name, desc, (4 if major else 3) + (1 if desc else 0))

    for e in load("churches"):
        t = e["tags"]
        if t.get("religion", "christian") != "christian":
            continue
        if t.get("building") in ("wayside_shrine", "shrine") or t.get("place_of_worship") in ("wayside_shrine", "cross"):
            continue
        add(e, "church", t["name"], t.get("denomination", "").replace("_", " ") or None, 1)

    for e in load("other"):
        t = e["tags"]
        h, mm = t.get("historic"), t.get("man_made")
        if t.get("ruins") == "yes" and not mm:
            continue
        if h in ("castle", "fort", "fortress"):
            add(e, "fort", t["name"], h, 1)
        elif mm in ("tower", "chimney", "water_tower", "obelisk") or h == "tower":
            desc = (t.get("tower:type") or mm or "tower").replace("_", " ")
            if t.get("height"):
                desc += f" · {t['height']} m"
            add(e, "tower", t["name"], desc, 1)
        # windmills: mostly ruined stumps on this coast, left out

    # de-duplicate: the same lighthouse is often mapped as a seamark node *and* a building
    marks.sort(key=lambda m: -m["rank"])
    kept = []
    grid = collections.defaultdict(list)
    for m in marks:
        ky, kx = int(m["lat"] / 0.001), int(m["lon"] / 0.001)
        dup = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for o in grid[(ky + dy, kx + dx)]:
                    near = dist_m(m["lat"], m["lon"], o["lat"], o["lon"])
                    same_family = (m["kind"] in ("lighthouse", "harbour_light", "landmark")) == \
                                  (o["kind"] in ("lighthouse", "harbour_light", "landmark"))
                    if near < 40 and (same_family or m["name"] == o["name"]):
                        dup = True
                        if not o["desc"] and m["desc"]:
                            o["desc"] = m["desc"]
                        if o["name"] in ("Lighthouse",) or o["name"].endswith(" light"):
                            if not (m["name"] == "Lighthouse" or m["name"].endswith(" light")):
                                o["name"] = m["name"]
        if not dup:
            kept.append(m)
            grid[(ky, kx)].append(m)
    print(f"landmarks: {len(marks)} candidates -> {len(kept)} after de-duplication")
    marks = kept

    for town_name_, anchor in EXTRA_TOWNS:
        a = next((m for m in marks if m["name"] == anchor and m["kind"] == "lighthouse"), None)
        if a and not any(t["name"] == town_name_ for t in towns):
            towns.append({"name": town_name_, "lat": a["lat"], "lon": a["lon"], "place": "town",
                          "region": region_for(a["lat"], False, a["lon"])})

    # ------------------------------------------------------------ towns need enough landmarks
    def visible_count(tn):
        n = 0
        for m in marks:
            if abs(m["lat"] - tn["lat"]) > 0.3:
                continue
            d = dist_m(tn["lat"], tn["lon"], m["lat"], m["lon"])
            if d <= (2 if m["kind"] == "lighthouse" else 1) * TOWN_RADIUS_NM * NM:
                n += 1
        return n

    towns = [t for t in towns if visible_count(t) >= MIN_LANDMARKS_PER_TOWN]
    # same name twice in a region (common for villages): keep both but make them distinguishable
    by_name = collections.Counter((t["region"], t["name"]) for t in towns)
    for t in towns:
        if by_name[(t["region"], t["name"])] > 1:
            t["name"] = f"{t['name']} ({t['lat']:.2f}°N)"
    towns.sort(key=lambda t: (t["region"], -t["lat"]))
    for i, t in enumerate(towns, 1):
        t["id"] = i

    for m in marks:
        m["town"] = min(towns, key=lambda t: dist_m(t["lat"], t["lon"], m["lat"], m["lon"]))["id"]

    # unnamed harbour lights get their town in the name: "Green light – Póvoa de Varzim"
    town_name = {t["id"]: t["name"] for t in towns}
    for m in marks:
        if m["name"].endswith(" light") or m["name"] == "Lighthouse":
            m["name"] = f"{m['name']} – {town_name[m['town']]}"

    # ------------------------------------------------------------ write
    OUT.unlink(missing_ok=True)
    db = sqlite3.connect(OUT)
    db.executescript("""
        CREATE TABLE meta     (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE region   (id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL, sort_order INTEGER NOT NULL);
        CREATE TABLE town     (id INTEGER PRIMARY KEY, region_id INTEGER NOT NULL REFERENCES region(id),
                               name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
        CREATE TABLE landmark (id INTEGER PRIMARY KEY, osm_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                               lat REAL NOT NULL, lon REAL NOT NULL, town_id INTEGER NOT NULL REFERENCES town(id),
                               description TEXT);
        CREATE INDEX ix_town_region  ON town(region_id);
        CREATE INDEX ix_landmark_pos ON landmark(lat, lon);
    """)
    now = datetime.datetime.now(datetime.timezone.utc)
    version = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].isdigit() else now.strftime("%Y%m%d") + "01"
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("version", version),
        ("built_utc", now.isoformat(timespec="seconds")),
        ("coverage", "Ría de Vigo (ES) to Albufeira (PT), Madeira & Porto Santo"),
        ("source", "OpenStreetMap via Overpass API"),
        ("license", "© OpenStreetMap contributors, ODbL 1.0 – https://www.openstreetmap.org/copyright"),
        ("disclaimer", "Training aid only. Not for navigation."),
    ])
    used_regions = {t["region"] for t in towns}
    db.executemany("INSERT INTO region VALUES (?,?,?,?)", [r for r in REGIONS if r[0] in used_regions])
    db.executemany("INSERT INTO town VALUES (?,?,?,?,?)",
                   [(t["id"], t["region"], t["name"], round(t["lat"], 6), round(t["lon"], 6)) for t in towns])
    db.executemany("INSERT INTO landmark (osm_id,name,kind,lat,lon,town_id,description) VALUES (?,?,?,?,?,?,?)",
                   [(m["osm"], m["name"], m["kind"], round(m["lat"], 6), round(m["lon"], 6), m["town"], m["desc"])
                    for m in sorted(marks, key=lambda m: -m["lat"])])
    db.commit()
    db.execute("VACUUM")

    print(f"\n{OUT.name}: version {version}, {OUT.stat().st_size / 1024:.0f} KB")
    for row in db.execute("""SELECT r.name, COUNT(DISTINCT t.id), COUNT(l.id) FROM region r
                             JOIN town t ON t.region_id = r.id LEFT JOIN landmark l ON l.town_id = t.id
                             GROUP BY r.id ORDER BY r.sort_order"""):
        print(f"  {row[0]:32} {row[1]:4} towns {row[2]:5} landmarks")
    for row in db.execute("SELECT kind, COUNT(*) FROM landmark GROUP BY kind ORDER BY 2 DESC"):
        print(f"  {row[0]:14} {row[1]:5}")
    db.close()


if __name__ == "__main__":
    main()
