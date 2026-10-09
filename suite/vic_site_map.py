# NODE_INPUTS: address:str | "Victorian street address, or lat,lon" default="201 Napier Street, Fitzroy VIC 3065"
# NODE_INPUTS: radius:float | "context around the site (m)" min=10 max=300 default=60
# NODE_INPUTS: assumed_height:float | "height for buildings with no known height (m); 0 = leave them flat" min=0 max=60 default=7
# NODE_INPUTS: refresh:bool | "fetch again instead of using the cache" default=false
# NODE_OUTPUTS: site:list[Curve] | "lot boundary", lots:list[Curve] | "neighbouring lots", roads:list[Curve] | "road centrelines", road_names:list[str] | "one per road curve"
# NODE_OUTPUTS: footprints:list[Curve] | "building footprints", buildings:list[Brep] | "extruded footprints", zone:str, overlays:list[str], info:str | "summary, sources and caveats", area:float | "site area (m2)"
# Victorian site map: an address becomes the lot, neighbouring lots, named roads, building massing, zone and overlays (Vicmap + Overture Maps).
#
# Geometry is in metres scaled to the model's units, centred on the site, Y axis = true north, flat (z = 0).
# Fetched data is cached for 30 days in the temp folder; set refresh to fetch again.
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import re
import struct
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import Rhino
import Rhino.Geometry as rg

UA = {"User-Agent": "Alien/2.0 (Grasshopper site map node)"}
VICMAP = "https://services-ap1.arcgis.com/P744lA0wf4LlBZ84/ArcGIS/rest/services"
ZONES = VICMAP + "/Vicmap_Planning/FeatureServer/3"
OVERLAYS = VICMAP + "/Vicmap_Planning/FeatureServer/2"
BUSHFIRE = VICMAP + "/Vicmap_Planning/FeatureServer/9"
PARCELS = VICMAP + "/Vicmap_Parcel/FeatureServer/0"
PROPERTIES = VICMAP + "/Vicmap_Property_Address/FeatureServer/0"
ROADS = VICMAP + "/Vicmap_Transport/FeatureServer/1"
ADDRESS_WFS = "https://opendata.maps.vic.gov.au/geoserver/wfs"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"  # fallback when Overture fails
OVERTURE_STAC = "https://stac.overturemaps.org/catalog.json"
OVERTURE_FALLBACK = "2026-09-23.0"  # used when the STAC catalogue does not answer
SCHEMES = "https://planning-schemes.app.planning.vic.gov.au"
CACHE_DIR = os.path.join(tempfile.gettempdir(), "alien-cache", "vic_site_map")
CACHE_DAYS = 30
STOREY, ROOF = 3.0, 1.0  # m: height from a storey count = storeys x STOREY + ROOF
Z, EXTENT, BUFFER = 14, 4096, 64  # Overture tiles: zoom, MVT units per tile, clip buffer

ROAD_TYPES = {
    "ST": "STREET", "RD": "ROAD", "AV": "AVENUE", "AVE": "AVENUE", "PDE": "PARADE", "CR": "CRESCENT",
    "CRES": "CRESCENT", "CT": "COURT", "DR": "DRIVE", "PL": "PLACE", "LN": "LANE", "TCE": "TERRACE",
    "GR": "GROVE", "BVD": "BOULEVARD", "BLVD": "BOULEVARD", "HWY": "HIGHWAY", "CL": "CLOSE",
    "CCT": "CIRCUIT", "SQ": "SQUARE", "ESP": "ESPLANADE", "HL": "HILL", "RDGE": "RIDGE",
}
ROAD_TYPES.update({v: v for v in list(ROAD_TYPES.values()) + ["WAY", "WALK", "RISE", "ALLEY", "MEWS", "ROW"]})
SMALL_WORDS = {"AND", "OF", "THE", "TO", "FOR", "ON", "IN"}


# ── http ────────────────────────────────────────────────────────────

def http(url, params=None, post=False, timeout=30):
    body = urllib.parse.urlencode(params or {})
    req = urllib.request.Request(url if post or not body else url + "?" + body,
                                 data=body.encode() if post else None, headers=UA)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def arcgis(layer, geometry, gtype, fields="*", geom=False, where="1=1", **extra):
    p = {"where": where, "outFields": fields, "returnGeometry": str(geom).lower(), "outSR": 4326, "f": "json",
         "geometry": json.dumps(geometry), "geometryType": gtype, "inSR": 4326,
         "spatialRel": "esriSpatialRelIntersects"}
    p.update(extra)
    out = http(layer + "/query", p, post=True)
    if "error" in out:
        raise RuntimeError(out["error"].get("message", "ArcGIS error"))
    return out.get("features", [])


def sql(s):
    return str(s).replace("'", "''")


# ── address → point ─────────────────────────────────────────────────

def parse_address(text):
    """'3/20A Albert St, Brunswick East VIC 3057' -> parts that match Vicmap's ezi_address."""
    t = re.sub(r"\s+", " ", text.upper()).strip()
    first, _, rest = t.partition(",")
    m = re.match(r"^(?:(?:UNIT|U|APT|APARTMENT|SHOP|SUITE)\s*)?(?:(\w+)\s*/\s*)?(\d+)([A-Z]?)(?:\s*-\s*\d+[A-Z]?)?\s+(.+)$",
                 first.strip())
    if not m:
        return None
    unit, number, suffix, road = m.groups()
    words = road.split()
    idx = next((i for i in range(1, len(words)) if words[i] in ROAD_TYPES), None)
    name, rtype, tail = (words, None, []) if idx is None else (words[:idx], ROAD_TYPES[words[idx]], words[idx + 1:])
    where = rest if rest else " ".join(tail)
    postcode = re.search(r"\b3\d{3}\b", where)
    locality = " ".join(re.sub(r"\b(VIC|VICTORIA|AUSTRALIA)\b|\b\d{4}\b", " ", where).replace(",", " ").split())
    return {"unit": unit, "number": number + suffix, "road": " ".join(name + ([rtype] if rtype else [])),
            "typed": rtype is not None, "locality": locality or None,
            "postcode": postcode.group(0) if postcode else None}


def geocode(text):
    """-> dict(lon, lat, pfi, label, how)."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", text)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        lat, lon = (a, b) if abs(a) < 60 else (b, a)
        return {"lon": lon, "lat": lat, "pfi": None, "label": text.strip(), "how": "coordinates"}
    a = parse_address(text)
    if a and a["typed"]:
        hit = vicmap_address(a)
        if hit:
            return hit
    return nominatim(text)


def vicmap_address(a):
    """Vicmap's own address index (fast prefix search on ezi_address, e.g. '1/114 BLYTH STREET BRUNSWICK 3056')."""
    prefix = ("%s/" % a["unit"] if a["unit"] else "") + "%s %s " % (a["number"], a["road"])

    def search(head):
        cql = "ezi_address LIKE '%s%s%s%%'" % (head, sql(prefix), sql(a["locality"] or ""))
        if a["postcode"]:
            cql += " AND postcode='%s'" % a["postcode"]
        res = http(ADDRESS_WFS, {"service": "WFS", "version": "1.1.0", "request": "GetFeature",
                                 "typeName": "open-data-platform:address", "outputFormat": "application/json",
                                 "maxFeatures": 20, "CQL_FILTER": cql}, timeout=20)
        return [f for f in res.get("features", []) if f.get("geometry")]

    feats = search("")
    if not feats and not a["unit"]:
        feats = search("%/")  # only unit addresses exist at this number ('1/21 MAIN ROAD ...'): slower search
    if a["locality"]:  # 'FITZROY%' also matches FITZROY NORTH: prefer the exact suburb
        exact = [f for f in feats if re.fullmatch(r"(\w+/)?" + re.escape(prefix + a["locality"]) + r" \d{4}",
                                                  f["properties"]["ezi_address"])]
        feats = exact or feats
    if not feats:
        return None
    places = sorted({f["properties"]["locality_name"] for f in feats})
    if len(places) > 1:
        raise ValueError("'%s' is in several suburbs (%s). Add the suburb." % (prefix.strip(), ", ".join(p.title() for p in places[:6])))
    f = sorted(feats, key=lambda f: f["properties"].get("is_primary") != "Y")[0]
    lon, lat = f["geometry"]["coordinates"][:2]
    return {"lon": lon, "lat": lat, "pfi": f["properties"].get("property_pfi"),
            "label": f["properties"]["ezi_address"], "how": "Vicmap Address"}


def nominatim(text):
    q = text if re.search(r"\b(VIC|VICTORIA)\b", text, re.I) else text + ", Victoria"
    res = http(NOMINATIM, {"q": q, "format": "jsonv2", "countrycodes": "au", "limit": 1}, timeout=20)
    if not res:
        raise ValueError("Address not found: '%s'. Check the number and street, or give lat,lon." % text)
    r = res[0]
    if r.get("category") == "highway" or r.get("addresstype") in {
            "road", "suburb", "city", "town", "village", "postcode", "state", "county", "municipality", "neighbourhood"}:
        # Only the street or suburb matched: the lot under that point would be a stranger's, with the wrong controls.
        raise ValueError("Couldn't find that exact address (nearest match: %s). Check the number and street, or give "
                         "lat,lon of a point inside the lot." % r.get("display_name", "?"))
    return {"lon": float(r["lon"]), "lat": float(r["lat"]), "pfi": None, "label": r.get("display_name", text),
            "how": "OpenStreetMap"}


# ── local frame: metres east/north of the site centre ───────────────

def frame(lon0, lat0):
    a, f = 6378137.0, 1 / 298.257222101  # GRS80 (GDA2020)
    e2 = f * (2 - f)
    s = math.sin(math.radians(lat0))
    kx = math.radians(1) * a / math.sqrt(1 - e2 * s * s) * math.cos(math.radians(lat0))
    ky = math.radians(1) * a * (1 - e2) / (1 - e2 * s * s) ** 1.5
    return (lambda lon, lat: ((lon - lon0) * kx, (lat - lat0) * ky)), (lambda x, y: (lon0 + x / kx, lat0 + y / ky))


def ring_area(r):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1])) / 2


def in_rings(x, y, rings):
    inside = False
    for r in rings:
        for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def edge_dist(x, y, rings):
    best = float("inf")
    for r in rings:
        for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
            dx, dy = x2 - x1, y2 - y1
            t = 0 if dx == dy == 0 else max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(x - x1 - t * dx, y - y1 - t * dy))
    return best


def interior_samples(rings):
    """Points well inside the site, so slivers of a neighbouring zone along the boundary don't count."""
    pts = [p for r in rings for p in r]
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    w, h = x1 - x0, y1 - y0
    step = max(min(w, h) / 8, math.sqrt(w * h / 200), 0.25)
    inset = min(1.0, 0.1 * min(w, h))
    out, y = [], y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        while x < x1:
            if in_rings(x, y, rings) and edge_dist(x, y, rings) >= inset:
                out.append((x, y))
            x += step
        y += step
    return out or [(sum(p[0] for p in rings[0]) / len(rings[0]), sum(p[1] for p in rings[0]) / len(rings[0]))]


def min_rect(points):
    """Minimum-area bounding rectangle of the site -> (short side, long side)."""
    pts = sorted(set(points))
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    h, best = lo[:-1] + hi[:-1], None
    for i in range(len(h)):
        (xa, ya), (xb, yb) = h[i], h[(i + 1) % len(h)]
        ang = math.atan2(yb - ya, xb - xa)
        c, s = math.cos(ang), math.sin(ang)
        us, vs = [x * c + y * s for x, y in h], [-x * s + y * c for x, y in h]
        w, d = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * d < best[0]:
            best = (w * d, min(w, d), max(w, d))
    return best[1], best[2]


def clip_path(path, box):
    """Clip a polyline to a rectangle (Liang-Barsky per segment); returns the pieces inside."""
    x0, y0, x1, y1 = box
    out, cur = [], []
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        dx, dy, t0, t1, ok = bx - ax, by - ay, 0.0, 1.0, True
        for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
            if p == 0:
                ok = ok and q >= 0
            elif p < 0:
                t0 = max(t0, q / p)
            else:
                t1 = min(t1, q / p)
        if not ok or t0 > t1:
            if len(cur) > 1:
                out.append(cur)
            cur = []
            continue
        a, b = (ax + t0 * dx, ay + t0 * dy), (ax + t1 * dx, ay + t1 * dy)
        if cur and math.dist(cur[-1], a) > 1e-6:
            out.append(cur)
            cur = []
        cur = cur or [a]
        cur.append(b)
        if t1 < 1.0:
            out.append(cur)
            cur = []
    if len(cur) > 1:
        out.append(cur)
    return [p for p in out if len(p) > 1]


# ── fetch (everything stays lon/lat so the cache doesn't depend on units) ──

def fetch(text, radius):
    g = geocode(text)
    parts = parse_address(text)
    asked = "%s %s " % (parts["number"], parts["road"]) if parts else None
    pt = {"x": g["lon"], "y": g["lat"], "spatialReference": {"wkid": 4326}}
    fields = "add_ezi_address,prop_pfi,prop_propnum"
    props = []
    if g["pfi"]:
        props = arcgis(PROPERTIES, pt, "esriGeometryPoint", fields, True, where="prop_pfi='%s'" % sql(g["pfi"]),
                       distance=250, units="esriSRUnit_Meter")
    if not props:
        props = arcgis(PROPERTIES, pt, "esriGeometryPoint", fields, True)
    if not props:
        raise ValueError("No Vicmap property at %.6f, %.6f (found via %s)." % (g["lat"], g["lon"], g["how"]))
    rings, seen = [], set()
    for p in props:  # strata lots repeat the same outline
        for r in p["geometry"]["rings"]:
            key = tuple(tuple(round(c, 7) for c in v) for v in r)
            if key not in seen:
                seen.add(key)
                rings.append(r)

    lons, lats = [v[0] for r in rings for v in r], [v[1] for r in rings for v in r]
    lon0, lat0 = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    to_xy, to_ll = frame(lon0, lat0)
    local = [[to_xy(*v) for v in r] for r in rings]
    samples = {"points": [list(to_ll(x, y)) for x, y in interior_samples(local)], "spatialReference": {"wkid": 4326}}
    polygon = {"rings": rings, "spatialReference": {"wkid": 4326}}
    xs, ys = [p[0] for r in local for p in r], [p[1] for r in local for p in r]
    box = (min(xs) - radius, min(ys) - radius, max(xs) + radius, max(ys) + radius)
    (w, s), (e, n) = to_ll(box[0], box[1]), to_ll(box[2], box[3])
    env = {"xmin": w, "ymin": s, "xmax": e, "ymax": n, "spatialReference": {"wkid": 4326}}
    plan = "zone_code,zone_description,lga"

    jobs = {
        "zones_in": lambda: arcgis(ZONES, samples, "esriGeometryMultipoint", plan),
        "zones_edge": lambda: arcgis(ZONES, polygon, "esriGeometryPolygon", plan),
        "overlays_in": lambda: arcgis(OVERLAYS, samples, "esriGeometryMultipoint", plan),
        "overlays_edge": lambda: arcgis(OVERLAYS, polygon, "esriGeometryPolygon", plan),
        "bushfire_in": lambda: arcgis(BUSHFIRE, samples, "esriGeometryMultipoint", "lga_name"),
        "bushfire_edge": lambda: arcgis(BUSHFIRE, polygon, "esriGeometryPolygon", "lga_name"),
        "parcels_in": lambda: arcgis(PARCELS, samples, "esriGeometryMultipoint", "parcel_spi"),
        "near": lambda: arcgis(PARCELS, env, "esriGeometryEnvelope", "parcel_spi,parcel_road", True, resultRecordCount=1500),
        "roads": lambda: arcgis(ROADS, env, "esriGeometryEnvelope", "ezi_road_name_label,class_code", True, resultRecordCount=500),
        "buildings": lambda: footprints(s, w, n, e),
    }
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        for k, fut in futures.items():
            try:
                results[k] = fut.result()
            except Exception as ex:
                results[k], errors[k] = [], str(ex)

    def planning(inside, edge):
        found = {f["attributes"]["zone_code"]: f["attributes"] for f in results[inside]}
        out = [dict(a, extent="site") for _, a in sorted(found.items())]
        out += [dict(f["attributes"], extent="edge") for f in sorted(results[edge], key=lambda f: f["attributes"]["zone_code"])
                if f["attributes"]["zone_code"] not in found]
        return list({z["zone_code"]: z for z in out}.values())

    spis = sorted({f["attributes"]["parcel_spi"] for f in results["parcels_in"] if f["attributes"].get("parcel_spi")})
    return {
        "v": 1, "query": text, "radius": radius, "retrieved": dt.date.today().isoformat(),
        "geocode": g, "origin": [lon0, lat0], "site": rings,
        # the address that was asked for first, then street addresses before unit and shop addresses
        "address": sorted({p["attributes"]["add_ezi_address"] for p in props},
                          key=lambda a: (a != g["label"], not asked or asked not in a, "/" in a, len(a), a)),
        "propnum": sorted({str(p["attributes"]["prop_propnum"]) for p in props if p["attributes"].get("prop_propnum")}),
        "spis": spis,
        "zones": planning("zones_in", "zones_edge"),
        "overlays": planning("overlays_in", "overlays_edge"),
        "bushfire": "yes" if results["bushfire_in"] else ("edge only - check" if results["bushfire_edge"] else "no"),
        "lots": [f["geometry"]["rings"] for f in results["near"]
                 if f.get("geometry") and f["attributes"].get("parcel_road") != "Y"
                 and f["attributes"].get("parcel_spi") not in spis],
        "roads": [[nice(f["attributes"].get("ezi_road_name_label") or ""), f["geometry"]["paths"]]
                  for f in results["roads"] if f.get("geometry")],
        "buildings": results["buildings"][1] if results["buildings"] else [],
        "buildings_source": results["buildings"][0] if results["buildings"] else None,
        "errors": errors,
    }


def footprints(s, w, n, e):
    """-> (source, [[ring lon/lat, height or None, how, dataset]]). Overture first; OpenStreetMap if it fails."""
    try:
        release, out = overture(s, w, n, e)
        return "Overture Maps %s" % release, out
    except Exception as ex:
        first = ex
    try:
        return "OpenStreetMap (Overture failed: %s)" % first, overpass(s, w, n, e)
    except Exception as ex:
        raise RuntimeError("Overture: %s; OpenStreetMap: %s" % (first, ex))


def overpass(s, w, n, e):
    q = '[out:json][timeout:20];(way["building"](%f,%f,%f,%f);relation["building"](%f,%f,%f,%f););out tags geom;' % ((s, w, n, e) * 2)
    out = []
    for el in http(OVERPASS, {"data": q}, post=True, timeout=25).get("elements", []):
        tags = el.get("tags", {})
        rings = [el.get("geometry", [])] if el["type"] == "way" else \
            [m.get("geometry", []) for m in el.get("members", []) if m.get("role") == "outer"]
        h, how = osm_height(tags)
        for r in rings:
            ring = [[p["lon"], p["lat"]] for p in r]
            if len(ring) >= 4 and ring[0] == ring[-1]:
                out.append([ring, h, how, "OpenStreetMap" if h else None])
    return out


def osm_height(tags):
    m = re.match(r"\s*([\d.]+)\s*(m|ft|')?", tags.get("height", ""))
    if m:
        try:
            return float(m.group(1)) * (0.3048 if m.group(2) in ("ft", "'") else 1.0), "height"
        except ValueError:
            pass
    m = re.match(r"\s*([\d.]+)", tags.get("building:levels", ""))
    if m:
        try:
            return float(m.group(1)) * STOREY + ROOF, "floors"
        except ValueError:
            pass
    return None, None


# ── Overture Maps buildings: a PMTiles v3 archive of zoom-14 vector tiles, read by HTTP range ──
# Overture merges OpenStreetMap with Microsoft/Google ML footprints, so it covers far more of Victoria.

def overture(s, w, n, e):
    release = overture_release()
    try:
        hdr = pmtiles_header(release)
    except Exception:
        if release == OVERTURE_FALLBACK:
            raise
        release = OVERTURE_FALLBACK
        hdr = pmtiles_header(release)
    (x0, y0), (x1, y1) = ll_to_tile(w, n), ll_to_tile(e, s)  # NW and SE corners, tile units
    bx0, by0, bx1, by1 = int(x0), int(y0), int(x1), int(y1)
    tiles = [(x, y) for x in range(bx0, bx1 + 1) for y in range(by0, by1 + 1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        datas = list(pool.map(lambda t: get_tile(release, hdr, Z, t[0], t[1]), tiles))

    pieces, props, parts = {}, {}, {}
    for (tx, ty), data in zip(tiles, datas):
        if not data:
            continue  # no tile: nothing built there
        layers = mvt_layers(data, ("building", "building_part"))
        if "building_part" in layers:
            ext, feats, keys, vals = layers["building_part"]
            cache = {}
            for fb in feats:
                _, _, tags, _ = mvt_feature(fb, geometry=False)
                p = mvt_props(tags, keys, vals, cache)
                bid = p.get("building_id")
                if not bid or p.get("is_underground") is True:
                    continue
                h, f = positive(p.get("height"), 1000), positive(p.get("num_floors"), 200)
                ph, pf = parts.get(bid, (None, None))
                parts[bid] = (max(h, ph or 0) if h else ph, max(f, pf or 0) if f else pf)
        if "building" not in layers:
            continue
        ext, feats, keys, vals = layers["building"]
        cache = {}
        for fb in feats:
            fid, gtype, tags, geom = mvt_feature(fb)
            if gtype != 3:
                continue
            rings = mvt_rings(geom)
            xs, ys = [x for r in rings for x, _ in r], [y for r in rings for _, y in r]
            if not xs or tx + max(xs) / ext < x0 or tx + min(xs) / ext > x1 or ty + max(ys) / ext < y0 or ty + min(ys) / ext > y1:
                continue
            p = mvt_props(tags, keys, vals, cache)
            if p.get("is_underground") is True:
                continue
            bid = p.get("id") or "fid-%s" % fid
            pieces.setdefault(bid, []).append((tx, ty, rings))
            props.setdefault(bid, p)

    out = []
    for bid, ps in pieces.items():
        h, how, ds = overture_height(props[bid], parts.get(bid))
        for r in stitch(ps, bx0, by0, bx1, by1):
            if ring_area(r) <= 0:
                continue  # a hole (courtyard): footprints are kept as outer outlines only
            ring = [list(tile_to_ll(bx0 + x / EXTENT, by0 + y / EXTENT)) for x, y in r]
            out.append([ring + [ring[0]], h, how, ds])
    return release, out


def overture_release():
    """Newest release from the STAC catalogue (checked once a day), else the known-good fallback."""
    path = os.path.join(CACHE_DIR, "overture-release.txt")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        with open(path) as f:
            return f.read().strip()
    try:
        rel = http(OVERTURE_STAC, timeout=5).get("latest") or ""
    except Exception:
        return OVERTURE_FALLBACK
    rel = rel if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", rel) else OVERTURE_FALLBACK
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(rel)
    return rel


def byte_range(release, offset, length):
    """A slice of the release's archive. Releases never change, so slices are cached on disk."""
    path = os.path.join(CACHE_DIR, "pmtiles", release, "%d-%d.bin" % (offset, length))
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    url = "https://tiles.overturemaps.org/%s/buildings.pmtiles" % release
    req = urllib.request.Request(url, headers=dict(UA, Range="bytes=%d-%d" % (offset, offset + length - 1)))
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    if len(data) > length:  # server ignored the Range header
        data = data[offset:offset + length]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data


def pmtiles_header(release):
    b = byte_range(release, 0, 127)
    if b[:7] != b"PMTiles" or b[7] != 3:
        raise RuntimeError("not a PMTiles v3 archive")
    v = struct.unpack("<11Q", b[8:96])
    if b[99] != 1:
        raise RuntimeError("archive does not hold vector tiles")
    return {"root": (v[0], v[1]), "leaf": v[4], "data": v[6], "dir_gzip": b[97] == 2, "tile_gzip": b[98] == 2}


def get_tile(release, hdr, z, x, y):
    tid = tile_id(z, x, y)
    off, length = hdr["root"]
    for _ in range(4):  # root directory, then up to three levels of leaf directories
        raw = byte_range(release, off, length)
        entry = find_entry(read_directory(gzip.decompress(raw) if hdr["dir_gzip"] else raw), tid)
        if entry is None:
            return None
        run, eoff, elen = entry
        if run > 0:
            raw = byte_range(release, hdr["data"] + eoff, elen)
            return gzip.decompress(raw) if hdr["tile_gzip"] else raw
        off, length = hdr["leaf"] + eoff, elen
    return None


def tile_id(z, x, y):
    """PMTiles tile id: tiles of lower zooms first, then the Hilbert index within the zoom."""
    d, s = 0, (1 << z) >> 1
    while s > 0:
        rx, ry = int(x & s > 0), int(y & s > 0)
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s >>= 1
    return ((1 << 2 * z) - 1) // 3 + d


def varint(b, i):
    result = shift = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, i
        shift += 7


def read_directory(data):
    n, i = varint(data, 0)
    cols = []
    for _ in range(4):  # tile ids (delta coded), run lengths, lengths, offsets
        col = []
        for _ in range(n):
            v, i = varint(data, i)
            col.append(v)
        cols.append(col)
    ids, runs, lens, raw = cols
    for k in range(1, n):
        ids[k] += ids[k - 1]
    offs = []
    for k in range(n):
        offs.append(offs[k - 1] + lens[k - 1] if raw[k] == 0 and k > 0 else raw[k] - 1)
    return ids, runs, lens, offs


def find_entry(directory, tid):
    ids, runs, lens, offs = directory
    lo, hi = 0, len(ids) - 1
    while lo <= hi:
        m = (lo + hi) // 2
        if tid > ids[m]:
            lo = m + 1
        elif tid < ids[m]:
            hi = m - 1
        else:
            return runs[m], offs[m], lens[m]
    if hi >= 0 and (runs[hi] == 0 or tid - ids[hi] < runs[hi]):
        return runs[hi], offs[hi], lens[hi]
    return None


def pb(buf):
    """Protocol-buffer fields of a message: (field number, value)."""
    i, n = 0, len(buf)
    while i < n:
        key, i = varint(buf, i)
        wt = key & 7
        if wt == 0:
            v, i = varint(buf, i)
        elif wt == 2:
            ln, i = varint(buf, i)
            v, i = buf[i:i + ln], i + ln
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            raise ValueError("bad protobuf wire type %d" % wt)
        yield key >> 3, v


def packed(buf):
    out, i, n = [], 0, len(buf)
    while i < n:
        v, i = varint(buf, i)
        out.append(v)
    return out


def mvt_layers(tile, wanted):
    """{name: (extent, raw features, keys, raw values)} for the wanted layers of a vector tile."""
    out = {}
    for f, layer in pb(tile):
        if f != 3:
            continue
        name, feats, keys, vals, extent = None, [], [], [], EXTENT
        for lf, v in pb(layer):
            if lf == 1:
                name = v.decode("utf-8")
            elif lf == 2:
                feats.append(v)
            elif lf == 3:
                keys.append(v.decode("utf-8"))
            elif lf == 4:
                vals.append(v)
            elif lf == 5:
                extent = v
        if name in wanted:
            out[name] = (extent, feats, keys, vals)
    return out


def mvt_feature(buf, geometry=True):
    fid, gtype, tags, geom = None, 0, [], []
    for f, v in pb(buf):
        if f == 1:
            fid = v
        elif f == 2:
            tags = packed(v)
        elif f == 3:
            gtype = v
        elif f == 4 and geometry:
            geom = packed(v)
    return fid, gtype, tags, geom


def mvt_props(tags, keys, vals, cache):
    p = {}
    for k, vi in zip(tags[::2], tags[1::2]):
        if vi not in cache:
            cache[vi] = mvt_value(vals[vi])
        p[keys[k]] = cache[vi]
    return p


def mvt_value(buf):
    for f, v in pb(buf):
        if f == 1:
            return v.decode("utf-8")
        if f == 2:
            return struct.unpack("<f", v)[0]
        if f == 3:
            return struct.unpack("<d", v)[0]
        if f in (4, 5):
            return v
        if f == 6:
            return (v >> 1) ^ -(v & 1)
        if f == 7:
            return bool(v)
    return None


def mvt_rings(geom):
    """MVT geometry commands -> rings in tile units (y down)."""
    rings, x, y, i = [], 0, 0, 0
    while i < len(geom):
        cmd, count = geom[i] & 7, geom[i] >> 3
        i += 1
        if cmd in (1, 2):
            for _ in range(count):
                x += (geom[i] >> 1) ^ -(geom[i] & 1)
                y += (geom[i + 1] >> 1) ^ -(geom[i + 1] & 1)
                i += 2
                if cmd == 1:
                    rings.append([(x, y)])
                else:
                    rings[-1].append((x, y))
    return rings


def ll_to_tile(lon, lat):
    n, r = 2 ** Z, math.radians(lat)
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n


def tile_to_ll(tx, ty):
    n = 2 ** Z
    return tx / n * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))


def stitch(pieces, bx0, by0, bx1, by1):
    """
    One building's pieces from several tiles -> rings in block units (tile units from the block's top-left, y down).
    Tiles clip features to the tile plus a 64-unit buffer: a piece with no vertex on that buffer is the whole building.
    Otherwise each piece is cut at the tile edges it shares with other fetched tiles and the pieces are joined by
    cancelling the edges that run both ways along those seams.
    """
    def to_block(tx, ty, r):
        r = r[:-1] if len(r) > 1 and r[0] == r[-1] else r
        return [((tx - bx0) * EXTENT + x, (ty - by0) * EXTENT + y) for x, y in r]

    lo, hi = -BUFFER, EXTENT + BUFFER
    clipped = lambda rings: any(x <= lo or x >= hi or y <= lo or y >= hi for r in rings for x, y in r)
    whole = pieces[0] if len(pieces) == 1 else next((p for p in pieces if not clipped(p[2])), None)
    if whole:
        return [r for r in (to_block(whole[0], whole[1], r) for r in whole[2]) if len(r) >= 3]

    edges = []
    for tx, ty, rings in pieces:
        ox, oy = (tx - bx0) * EXTENT, (ty - by0) * EXTENT
        for ring in rings:
            r = to_block(tx, ty, ring)
            if tx > bx0:
                r = clip_half(r, 0, ox, False)
            if tx < bx1:
                r = clip_half(r, 0, ox + EXTENT, True)
            if ty > by0:
                r = clip_half(r, 1, oy, False)
            if ty < by1:
                r = clip_half(r, 1, oy + EXTENT, True)
            edges += [(r[i], r[(i + 1) % len(r)]) for i in range(len(r)) if len(r) >= 3 and r[i] != r[(i + 1) % len(r)]]

    def seam(a, b):
        for axis, (first, last) in ((0, (bx0, bx1)), (1, (by0, by1))):
            c = a[axis]
            if c == b[axis] and c % EXTENT == 0 and 0 < c // EXTENT <= last - first:
                return axis, c
        return None

    plain, seams = [], {}
    for e in edges:
        k = seam(*e)
        if k is None:
            plain.append(e)
        else:
            seams.setdefault(k, []).append(e)
    net = {}
    for (axis, c), es in seams.items():
        along = sorted({v for a, b in es for v in (a[1 - axis], b[1 - axis])})
        for a, b in es:
            frm, to = a[1 - axis], b[1 - axis]
            stops = [v for v in along if (v - frm) * (v - to) < 0]
            pts = [frm] + (stops if frm < to else stops[::-1]) + [to]
            for i in range(len(pts) - 1):
                p = (c, pts[i]) if axis == 0 else (pts[i], c)
                q = (c, pts[i + 1]) if axis == 0 else (pts[i + 1], c)
                if net.get((q, p), 0) > 0:
                    net[(q, p)] -= 1
                else:
                    net[(p, q)] = net.get((p, q), 0) + 1
    for (p, q), k in net.items():
        plain += [(p, q)] * k

    starts = {}
    for e in plain:
        starts.setdefault(e[0], []).append(e)
    rings = []
    for first in plain:
        if first not in starts.get(first[0], []):
            continue  # already used
        ring, e = [], first
        for _ in range(len(plain) + 1):
            starts[e[0]].remove(e)
            ring.append(e[0])
            if e[1] == first[0] or not starts.get(e[1]):
                break
            e = starts[e[1]][0]
        n = len(ring)
        clean = [p for i, p in enumerate(ring)  # drop collinear points left on the seams
                 if (ring[(i + 1) % n][0] - ring[i - 1][0]) * (p[1] - ring[i - 1][1])
                 != (ring[(i + 1) % n][1] - ring[i - 1][1]) * (p[0] - ring[i - 1][0])]
        if len(clean) >= 3 and abs(ring_area(clean)) > 0.5:
            rings.append(clean)
    return rings


def clip_half(ring, axis, c, keep_below):
    """Sutherland-Hodgman against one axis-aligned line; crossings snap to whole tile units so tiles agree."""
    inside = (lambda p: p[axis] <= c) if keep_below else (lambda p: p[axis] >= c)

    def cross(a, b):
        p, q = (a, b) if a[axis] <= b[axis] else (b, a)
        v = round(p[1 - axis] + (c - p[axis]) / (q[axis] - p[axis]) * (q[1 - axis] - p[1 - axis]))
        return (c, v) if axis == 0 else (v, c)

    out = []
    for i, cur in enumerate(ring):
        prev = ring[i - 1]
        if inside(cur):
            if not inside(prev):
                out.append(cross(prev, cur))
            out.append(cur)
        elif inside(prev):
            out.append(cross(prev, cur))
    return out


def positive(v, most):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < most else None


def overture_height(p, part):
    """-> (height m or None, how, dataset). Height, then storeys x 3 m + 1 m roof, then the tallest building part."""
    ds = None
    try:
        sources = json.loads(p.get("sources") or "[]")
        whole = next((x.get("dataset") for x in sources if not x.get("property")), None)
        ds = next((x.get("dataset") for x in sources if x.get("property") == "/properties/height"), whole)
    except (ValueError, AttributeError, TypeError):
        pass
    h, f = positive(p.get("height"), 1000), positive(p.get("num_floors"), 200)
    if h:
        return h, "height", ds
    if f:
        return f * STOREY + ROOF, "floors", ds
    ph, pf = part or (None, None)
    if ph:
        return ph, "part", ds
    if pf:
        return pf * STOREY + ROOF, "floors", ds
    return None, None, None


def nice(desc):
    words = (desc or "").split()
    return " ".join(w.lower() if w in SMALL_WORDS and i else w.capitalize() if w.isalpha() else w for i, w in enumerate(words))


def cached(text, radius, again):
    key = hashlib.sha1(json.dumps([" ".join(text.upper().split()), round(radius, 1)]).encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, key + ".json")
    if not again and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_DAYS * 86400:
        with open(path) as f:
            return json.load(f), True
    data = fetch(text, radius)
    if not data["errors"]:  # don't keep a half answer
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
    return data, False


# ── geometry ────────────────────────────────────────────────────────

doc = Rhino.RhinoDoc.ActiveDoc
scale = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
units = doc.ModelUnitSystem.ToString().lower() if doc else "meters"


def curve(points, closed):
    pts = [rg.Point3d(x * scale, y * scale, 0) for x, y in points]
    if closed and pts[0].DistanceTo(pts[-1]) > 1e-9:
        pts.append(pts[0])
    return rg.PolylineCurve(pts)


def extrude(c, h):
    if c.ClosedCurveOrientation(rg.Plane.WorldXY) == rg.CurveOrientation.Clockwise:
        c = c.DuplicateCurve()
        c.Reverse()
    ext = rg.Extrusion.Create(c, h * scale, True)
    return ext.ToBrep() if ext else None


text = (address or "").strip()
if not text:
    raise ValueError("Give an address (e.g. '201 Napier Street, Fitzroy VIC 3065') or lat,lon.")
radius = min(max(radius or 60.0, 10.0), 300.0)
data, from_cache = cached(text, radius, bool(refresh))
to_xy, _ = frame(*data["origin"])

site_local = [[to_xy(*v) for v in r] for r in data["site"]]
site = [curve(r, True) for r in site_local]
area = round(abs(sum(ring_area(r) for r in site_local)), 1)
short, long_ = min_rect([p for r in site_local for p in r])

lots = [curve([to_xy(*v) for v in r], True) for rings in data["lots"] for r in rings]
xs, ys = [p[0] for r in site_local for p in r], [p[1] for r in site_local for p in r]
box = (min(xs) - radius, min(ys) - radius, max(xs) + radius, max(ys) + radius)
roads, road_names = [], []
for name, paths in data["roads"]:
    for path in paths:
        for piece in clip_path([to_xy(*v) for v in path], box):
            roads.append(curve(piece, False))
            road_names.append(name)

footprints, buildings, known = [], [], {}
for ring, h, how, ds in data["buildings"]:
    c = curve([to_xy(*v) for v in ring], True)
    footprints.append(c)
    if h is not None:
        known[ds or "unknown"] = known.get(ds or "unknown", 0) + 1
    height = h if h is not None else assumed_height
    if height and height > 0:
        b = extrude(c, height)
        if b:
            buildings.append(b)

def label(z):
    return "%s - %s%s" % (z["zone_code"], nice(z["zone_description"]), "" if z["extent"] == "site" else " (edge only)")

zone = "; ".join(label(z) for z in data["zones"] if z["extent"] == "site") or "; ".join(label(z) for z in data["zones"]) or "unknown"
overlays = [label(z) for z in data["overlays"]]
lga = next((z["lga"] for z in data["zones"] + data["overlays"] if z.get("lga")), None)
lga_nice = lga.title() if lga else None

g = data["geocode"]
lines = [
    data["address"][0] + (" (+%d more)" % (len(data["address"]) - 1) if len(data["address"]) > 1 else ""),
    "Found via %s%s" % (g["how"], "" if g["how"] != "OpenStreetMap" else ": " + g["label"]),
    "%s Planning Scheme | %s/%s" % (lga_nice, SCHEMES, urllib.parse.quote(lga or "")) if lga else "Planning scheme: unknown",
    "Lot(s): %s | council property no. %s" % (", ".join(data["spis"]) or "?", ", ".join(data["propnum"]) or "?"),
    "Zone: " + zone,
    "Overlays: " + ("; ".join(overlays) or "none"),
    "Bushfire prone area: " + data["bushfire"],
    "Site: %.0f m2, %.1f x %.1f m (minimum rectangle)" % (area, short, long_),
    "Buildings: %d footprints from %s; %d with a height (%s)%s" % (
        len(footprints), data.get("buildings_source") or "nowhere", sum(known.values()),
        ", ".join("%d %s" % (v, k) for k, v in sorted(known.items())) or "none",
        ("; the rest at an assumed %g m" % assumed_height) if assumed_height else "; the rest left flat"),
    "Geometry: %s, centred on the site (lat %.6f, lon %.6f), Y = true north, z = 0" % (units, data["origin"][1], data["origin"][0]),
    "Sources: Vicmap (State of Victoria, CC BY 4.0); Overture Maps Foundation and OpenStreetMap contributors (ODbL). Retrieved %s%s."
    % (data["retrieved"], ", from cache" if from_cache else ""),
    "Not a Planning Property Report: confirm with VicPlan before relying on it.",
]
for k, err in data["errors"].items():
    lines.append("WARNING %s failed: %s" % (k, err))
    print("%s failed: %s" % (k, err))
if "Microsoft ML Buildings" in known:
    lines.insert(-2, "Note: Microsoft ML heights ran about 2.3 m low against measured heights in inner Melbourne.")
info = "\n".join(lines)
