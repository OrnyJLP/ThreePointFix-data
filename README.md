# ThreePointFix – landmark database

The offline landmark database used by the **Three Point Fix** sailing-navigation training app:
lighthouses, harbour-entrance lights, church towers, forts and towers along the coast from
**Ría de Vigo to Albufeira**, plus **Madeira, Porto Santo and the Desertas**, and the Dutch **Waddenzee, IJsselmeer/Markermeer and North Sea coast**, each with its LAT/LON.

`landmarks.db` is SQLite: `area` → `region` → `town` → `landmark` (`name`, `kind`, `lat`, `lon`,
`description` such as `Fl(3) W 15s · 57 m · 24 NM`) and a `meta` table whose `version`
(yyyymmddNN) the app compares before downloading an update.

## Licence

The data is derived from [OpenStreetMap](https://www.openstreetmap.org/copyright).
© OpenStreetMap contributors. `landmarks.db` is a *derived database* and is made available
under the same licence: the **Open Database License (ODbL) 1.0** – see `LICENSE`.
The build scripts are public domain.

Positions are volunteer-mapped, not hydrographic-office data. **Training aid only – not for navigation.**

## Rebuild

```bash
python3 fetch_osm.py --force   # download raw data from the OpenStreetMap Overpass API
python3 build_db.py            # write landmarks.db, version = today + "01"
```

To extend the coverage, add a bounding box to `AREAS` in `fetch_osm.py` and a region in `build_db.py`.
