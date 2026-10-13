# NODE_INPUTS: address:str | "Victorian street address (suggestions as you type), or lat,lon" default="201 Napier Street, Fitzroy VIC 3065" suggest="vic-address"
# NODE_INPUTS: radius:float | "context around the site (m)" min=10 max=300 default=60
# NODE_INPUTS: assumed_height:float | "height for buildings with no known height (m); 0 = leave them flat" min=0 max=60 default=7
# NODE_INPUTS: refresh:bool | "fetch again instead of using the cache" button
# NODE_INPUTS: terrain:bool | "ground from LiDAR / Vicmap elevation; buildings, streets and trees sit on it" default=true
# NODE_INPUTS: streets:bool | "3D streets: carriageways, kerbs, footpaths and nature strips in the road reserves" default=true
# NODE_INPUTS: trees:bool | "trees with measured height and canopy (metro Melbourne + Ballarat, Shepparton, Wangaratta, Sale)" default=true
# NODE_INPUTS: contour_step:float | "contour interval (m); 0 = no contours" min=0 max=10 step=0.5 default=1
# NODE_OUTPUTS: site:list[Curve] | "lot boundary", lots:list[Curve] | "neighbouring lots", roads:list[Curve] | "road centrelines", road_names:list[str] | "one per road curve"
# NODE_OUTPUTS: footprints:list[Curve] | "building footprints", buildings:list[Brep] | "extruded footprints", zone:str, overlays:list[str], info:str | "summary, sources and caveats", area:float | "site area (m2)"
# NODE_OUTPUTS: ground:Mesh | "terrain", contours:list[Curve] | "at whole multiples of contour_step (m AHD)", datum:float | "AHD height of z = 0 (m)"
# NODE_OUTPUTS: carriageway:Mesh | "road surface", footpath:Mesh | "paved verge", nature_strip:Mesh | "grass verge and medians", kerb:Mesh | "kerb faces"
# NODE_OUTPUTS: canopy:Mesh | "tree crowns", trunk:Mesh | "tree trunks"
# CATEGORY: Site & context
# Victorian site map: an address becomes the lot, neighbouring lots, roads, terrain, 3D streets, trees, building massing, zone and overlays (Vicmap, GA, Overture Maps).
#
# Geometry is in metres scaled to the model's units, centred on the site, Y axis = true north. z = 0 is the ground at
# the site (datum gives its AHD height); the curves stay flat at z = 0 for modelling, while the ground, buildings,
# streets and trees follow the terrain. Fetched data is cached for 30 days in the temp folder; set refresh to fetch again.
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

import alien_vicdata as vd

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
    try:
        out = http(layer + "/query", p, post=True)
        if "error" in out:
            raise RuntimeError(out["error"].get("message", "ArcGIS error"))
        return out.get("features", [])
    except Exception:
        if layer not in WFS_TWINS:
            raise
        FALLBACKS.add(WFS_TWINS[layer])
        return wfs_query(WFS_TWINS[layer], geometry, gtype, fields, geom, where, extra.get("resultRecordCount", 2000))


# The same Vicmap layers on the open-data GeoServer. On 12 Oct 2026 a republish broke every spatial query on the ArcGIS
# property and parcel layers ("Invalid query parameters") while attribute queries kept working.
WFS_TWINS = {PROPERTIES: "v_property_mp_address", PARCELS: "v_parcel_mp"}
FALLBACKS = set()


def wfs_query(layer, geometry, gtype, fields, geom, where, limit):
    """An ArcGIS-style query answered from the WFS twin: features as {"attributes", "geometry": {"rings"}}."""
    ll = lambda lon, lat: "%.8f %.8f" % (lat, lon)  # CQL geometry here is lat/lon; the output is lon/lat
    filters = [where] if where and where != "1=1" else []
    if not filters or gtype != "esriGeometryPoint":  # an attribute lookup (prop_pfi=...) needs no geometry test
        if gtype == "esriGeometryPoint":
            filters.append("INTERSECTS(geom, POINT(%s))" % ll(geometry["x"], geometry["y"]))
        elif gtype == "esriGeometryMultipoint":
            filters.append("INTERSECTS(geom, MULTIPOINT(%s))" % ", ".join("(%s)" % ll(*pt) for pt in geometry["points"]))
        elif gtype == "esriGeometryEnvelope":
            filters.append("BBOX(geom, %.8f, %.8f, %.8f, %.8f)" % (geometry["ymin"], geometry["xmin"], geometry["ymax"], geometry["xmax"]))
        elif gtype == "esriGeometryPolygon":
            rings = ", ".join("(%s)" % ", ".join(ll(*v) for v in r) for r in geometry["rings"])
            filters.append("INTERSECTS(geom, POLYGON(%s))" % rings)
    props = ",".join(f for f in fields.split(",") if f and f != "*") + (",geom" if geom else "")
    res = http(ADDRESS_WFS, {"service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": "open-data-platform:" + layer,
                             "outputFormat": "application/json", "srsName": "EPSG:4326", "maxFeatures": limit,
                             "propertyName": props, "CQL_FILTER": " AND ".join(filters)}, timeout=30)
    out = []
    for f in res.get("features", []):
        g = f.get("geometry") or {}
        polys = g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [g.get("coordinates", [])] if g.get("type") == "Polygon" else []
        out.append({"attributes": f.get("properties", {}), "geometry": {"rings": [ring for poly in polys for ring in poly]} if geom else None})
    return out


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


# ── layers v2: terrain, streets, trees (each cached on its own, so toggling one doesn't refetch the rest) ──

def layer(name, fetcher):
    """-> (data, error). A fetched layer for this site and radius, cached for CACHE_DAYS; refresh fetches again."""
    key = hashlib.sha1(json.dumps([data["origin"], fetch_radius, name, 2]).encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, "%s-%s.json" % (key, name))
    if not refresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_DAYS * 86400:
        with open(path) as f:
            return json.load(f), None
    try:
        out = fetcher()
    except Exception as ex:
        return None, "%s: %s" % (type(ex).__name__, ex)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f)
    return out, None


PAVED_ZONES = re.compile(r"^(CCZ|CDZ|ACZ|C1Z|MUZ|DZ|PDZ)")  # central, commercial and mixed-use streets: verges paved


def offset_path(pts, d):
    """A polyline offset d to its left (mitred corners, mitre limited to 2 x d)."""
    out = []
    for k in range(len(pts)):
        normals = []
        for (ax, ay), (bx, by) in ([(pts[k - 1], pts[k])] if k > 0 else []) + ([(pts[k], pts[k + 1])] if k < len(pts) - 1 else []):
            length = math.hypot(bx - ax, by - ay)
            normals.append((-(by - ay) / length, (bx - ax) / length))
        nx_, ny_ = sum(n[0] for n in normals), sum(n[1] for n in normals)
        length = math.hypot(nx_, ny_)
        nx_, ny_ = (nx_ / length, ny_ / length) if length > 1e-9 else normals[0]
        m = d / max(nx_ * normals[0][0] + ny_ * normals[0][1], 0.5)
        out.append((pts[k][0] + nx_ * m, pts[k][1] + ny_ * m))
    return out


def band(pts, a, b):
    """Closed outline (metres, z = 0) of the strip between the offsets a and b (left positive) of a polyline."""
    ring = offset_path(pts, b) + offset_path(pts, a)[::-1]
    return rg.PolylineCurve([rg.Point3d(x, y, 0) for x, y in ring + ring[:1]])


def dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) > 0.05:
            out.append(p)
    return out


class Shapes(object):
    """Overlapping closed curves, found through a 10 m grid: a point is in the set when any curve contains it."""

    CELL = 10.0

    def __init__(self, curves):
        self.cells = {}
        for c in curves:
            if not (c and c.IsClosed):
                continue
            bb = c.GetBoundingBox(True)
            for i in range(int(bb.Min.X // self.CELL), int(bb.Max.X // self.CELL) + 1):
                for j in range(int(bb.Min.Y // self.CELL), int(bb.Max.Y // self.CELL) + 1):
                    self.cells.setdefault((i, j), []).append(c)

    def __contains__(self, pt):
        for c in self.cells.get((int(pt.X // self.CELL), int(pt.Y // self.CELL)), ()):
            if c.Contains(pt, rg.Plane.WorldXY, TOL) == rg.PointContainment.Inside:
                return True
        return False


def union(curves):
    curves = [c for c in curves if c and c.IsClosed]
    if len(curves) < 2:
        return curves
    out = rg.Curve.CreateBooleanUnion(curves, TOL)
    return list(out) if out else curves


TOL = 0.001  # m: the street layout is built in metres, then scaled
KERB_H, ROAD_LIFT = 0.15, 0.05  # verges sit a kerb above the road; roads float 5 cm over the terrain mesh (no z-fighting)


def street_surfaces(records, reserves, box, ground):
    """
    Road reserves split into carriageway, footpath and nature strip (meshes draped on the ground), plus kerb faces.
    Each centreline's cross-section becomes bands (carriageway, median, nature strip); the bands, the measured
    reserve polygons and the context box are overlaid into planar regions, and each region takes the class of the
    bands it falls in: carriageway, else nature strip (grass verge or median), else footpath.
    """
    cbands, nbands, rbands = [], [], []
    for r in records:
        cw, med = r.get("carriageway_m") or 0, r.get("median_m") or 0
        if cw <= 0:
            continue
        paths = r["coords"] if r["coords"] and isinstance(r["coords"][0][0], list) else [r["coords"]]
        for path in paths:
            pts = dedupe([to_xy(*v) for v in path])
            if len(pts) < 2:
                continue
            c = offset_path(pts, r.get("centre_offset_m") or 0.0)  # centre the section in the measured reserve
            half = med / 2 + cw / 2
            cbands += [band(c, med / 2, half), band(c, -half, -med / 2)] if med > 0 else [band(c, -cw / 2, cw / 2)]
            if (r.get("nature_strip_m") or 0) > 0 or med > 0:
                nbands.append(band(c, -(half + (r.get("nature_strip_m") or 0)), half + (r.get("nature_strip_m") or 0)))
            if r.get("reserve_width_m") is None:  # no reserve polygon measured: the class's typical verges
                rbands.append(band(c, -(half + (r.get("verge_m") or 0)), half + (r.get("verge_m") or 0)))
    reserve_curves = []
    for rings in reserves:  # outer rings only: holes in road reserves are rare slivers
        ring = dedupe([to_xy(*v) for v in rings[0]])
        if len(ring) >= 4:
            reserve_curves.append(rg.PolylineCurve([rg.Point3d(x, y, 0) for x, y in ring[:-1] + ring[:1]] if ring[0] == ring[-1] else
                                                   [rg.Point3d(x, y, 0) for x, y in ring + ring[:1]]))
    R, C, N = union(reserve_curves + cbands + rbands), union(cbands), union(nbands)
    inR, inC, inN = Shapes(reserve_curves + cbands + rbands), Shapes(cbands), Shapes(nbands)  # the small originals: fast tests
    x0, y0, x1, y1 = box
    frame_curve = rg.PolylineCurve([rg.Point3d(x0, y0, 0), rg.Point3d(x1, y0, 0), rg.Point3d(x1, y1, 0), rg.Point3d(x0, y1, 0), rg.Point3d(x0, y0, 0)])

    def kind(pt):
        if not (x0 < pt.X < x1 and y0 < pt.Y < y1) or pt not in inR:
            return None
        return "carriageway" if pt in inC else "nature_strip" if pt in inN else "footpath"

    regions = rg.Curve.CreateBooleanRegions(R + C + N + [frame_curve], rg.Plane.WorldXY, False, TOL)
    if regions is None:
        raise RuntimeError("could not split the road reserves into regions")
    mp = rg.MeshingParameters.FastRenderMesh
    mp.MaximumEdgeLength = 1.5  # small enough to follow the terrain
    mp.SimplePlanes = False
    meshes = {"carriageway": rg.Mesh(), "footpath": rg.Mesh(), "nature_strip": rg.Mesh()}
    road_edges = []
    for i in range(regions.RegionCount):
        curves = list(regions.RegionCurves(i))
        breps = rg.Brep.CreatePlanarBreps(curves, TOL) or []
        for b in breps:
            m = rg.Mesh()
            for piece in rg.Mesh.CreateFromBrep(b, mp) or []:
                m.Append(piece)
            if m.Faces.Count == 0:
                continue
            k = kind(m.Faces.GetFaceCenter(0))
            if k is None:
                continue
            lift = ROAD_LIFT if k == "carriageway" else ROAD_LIFT + KERB_H
            for v in range(m.Vertices.Count):
                p = m.Vertices[v]
                m.Vertices.SetVertex(v, p.X, p.Y, ground(p.X, p.Y) + lift)
            meshes[k].Append(m)
            if k == "carriageway":
                road_edges += curves
    # kerbs: carriageway edges with a raised verge on the other side
    kerb = rg.Mesh()
    for c in road_edges:
        ts = c.DivideByLength(1.0, True) or []
        run = []
        for t in list(ts) + [None]:
            ok = False
            if t is not None:
                p, tan = c.PointAt(t), c.TangentAt(t)
                n = rg.Vector3d(-tan.Y, tan.X, 0) * 0.25
                sides = (kind(p + n), kind(p - n))
                ok = "carriageway" in sides and ("footpath" in sides or "nature_strip" in sides)
            if ok:
                run.append(p)
                continue
            for a, b in zip(run, run[1:]):
                za, zb = ground(a.X, a.Y), ground(b.X, b.Y)
                i0 = kerb.Vertices.Count
                kerb.Vertices.Add(a.X, a.Y, za + ROAD_LIFT)
                kerb.Vertices.Add(b.X, b.Y, zb + ROAD_LIFT)
                kerb.Vertices.Add(b.X, b.Y, zb + ROAD_LIFT + KERB_H)
                kerb.Vertices.Add(a.X, a.Y, za + ROAD_LIFT + KERB_H)
                kerb.Faces.AddFace(i0, i0 + 1, i0 + 2, i0 + 3)
            run = []
    meshes["kerb"] = kerb
    return meshes


# ── geometry ────────────────────────────────────────────────────────

doc = Rhino.RhinoDoc.ActiveDoc
scale = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
units = doc.ModelUnitSystem.ToString().lower() if doc else "meters"
to_model = rg.Transform.Scale(rg.Point3d.Origin, scale)


def curve(points, closed):
    pts = []
    for x, y in points:  # WFS rings repeat vertices: a zero-length segment makes the polyline invalid
        p = rg.Point3d(x * scale, y * scale, 0)
        if not pts or p.DistanceTo(pts[-1]) > 1e-6 * scale:
            pts.append(p)
    if closed and pts[0].DistanceTo(pts[-1]) > 1e-6 * scale:
        pts.append(pts[0])
    return rg.PolylineCurve(pts)


def extrude(c, h, base=0.0):
    if c.ClosedCurveOrientation(rg.Plane.WorldXY) == rg.CurveOrientation.Clockwise:
        c = c.DuplicateCurve()
        c.Reverse()
    ext = rg.Extrusion.Create(c, h * scale, True)
    if not ext:
        return None
    b = ext.ToBrep()
    if base:
        b.Translate(0, 0, base * scale)
    return b


text = (address or "").strip()
if not text:
    raise ValueError("Give an address (e.g. '201 Napier Street, Fitzroy VIC 3065') or lat,lon.")
radius = min(max(radius or 60.0, 10.0), 300.0)
# Fetch a whole context bucket and trim to the radius asked for: sliding the radius within a bucket costs nothing.
FETCH_RADII = (60.0, 120.0, 200.0, 300.0)
fetch_radius = next(r for r in FETCH_RADII if r >= radius - 1e-9)
data, from_cache = cached(text, fetch_radius, bool(refresh))
to_xy, to_ll = frame(*data["origin"])

site_local = [[to_xy(*v) for v in r] for r in data["site"]]
site = [curve(r, True) for r in site_local]
area = round(abs(sum(ring_area(r) for r in site_local)), 1)
short, long_ = min_rect([p for r in site_local for p in r])

xs, ys = [p[0] for r in site_local for p in r], [p[1] for r in site_local for p in r]
box = (min(xs) - radius, min(ys) - radius, max(xs) + radius, max(ys) + radius)                    # what is shown
fbox = (min(xs) - fetch_radius, min(ys) - fetch_radius, max(xs) + fetch_radius, max(ys) + fetch_radius)  # what is fetched
(lon_w, lat_s), (lon_e, lat_n) = to_ll(fbox[0], fbox[1]), to_ll(fbox[2], fbox[3])


def overlaps(points, b, margin=0.0):
    px, py = [p[0] for p in points], [p[1] for p in points]
    return min(px) <= b[2] + margin and max(px) >= b[0] - margin and min(py) <= b[3] + margin and max(py) >= b[1] - margin


lots = []
for rings in data["lots"]:
    for r in rings:
        local = [to_xy(*v) for v in r]
        if overlaps(local, box):
            lots.append(curve(local, True))
roads, road_names = [], []
for name, paths in data["roads"]:
    for path in paths:
        for piece in clip_path([to_xy(*v) for v in path], box):
            roads.append(curve(piece, False))
            road_names.append(name)

site_zones = [z["zone_code"] for z in data["zones"] if z["extent"] == "site"]
paved = any(PAVED_ZONES.match(z or "") for z in site_zones)
spacing = max(2.0, max(fbox[2] - fbox[0], fbox[3] - fbox[1]) / 200.0)  # terrain grid: 2 m, at most 201 x 201 points
gnx, gny = int(round((fbox[2] - fbox[0]) / spacing)) + 1, int(round((fbox[3] - fbox[1]) / spacing)) + 1
jobs = {}
if terrain:
    jobs["terrain-%dx%d" % (gnx, gny)] = lambda: dict(zip(("grid", "source", "res"), vd.elevation_grid(lon_w, lat_s, lon_e, lat_n, gnx, gny)))
if streets:
    jobs["streets-paved" if paved else "streets"] = lambda: vd.streets(lon_w, lat_s, lon_e, lat_n, paved_verges=paved)
if trees:
    jobs["trees"] = lambda: vd.trees(lon_w, lat_s, lon_e, lat_n)
layers, layer_errors = {}, {}
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {k: pool.submit(layer, k, fn) for k, fn in jobs.items()}
    for k, fut in futures.items():
        name = k.split("-")[0]
        layers[name], err = fut.result()
        if err:
            layer_errors[name] = err

# ground: bilinear on the grid, metres relative to the datum (the ground at the site centre)
grid = (layers.get("terrain") or {}).get("grid")
datum = 0.0
if grid:
    valid = [v for row in grid for v in row if v is not None]
    gdx, gdy = (fbox[2] - fbox[0]) / (gnx - 1), (fbox[3] - fbox[1]) / (gny - 1)

    def ahd(x, y):
        fx = min(max((x - fbox[0]) / gdx, 0), gnx - 1.000001)
        fy = min(max((y - fbox[1]) / gdy, 0), gny - 1.000001)
        i, j = int(fx), int(fy)
        ax, ay = fx - i, fy - j
        corners = [(grid[j][i], (1 - ax) * (1 - ay)), (grid[j][i + 1], ax * (1 - ay)),
                   (grid[j + 1][i], (1 - ax) * ay), (grid[j + 1][i + 1], ax * ay)]
        w = sum(wt for v, wt in corners if v is not None)
        return sum(v * wt for v, wt in corners if v is not None) / w if w > 0 else None

    centre = ahd(0.0, 0.0)
    datum = round(centre if centre is not None else sum(valid) / len(valid), 2)

    def ground(x, y):
        z = ahd(x, y)
        return (z if z is not None else datum) - datum
else:
    valid = []

    def ground(x, y):
        return 0.0

ground_mesh, contours = None, []
if grid and valid:
    ground_mesh = rg.Mesh()
    index = {}
    # only the grid nodes that cover the context shown (the grid spans the whole fetched bucket)
    i0, i1 = max(0, int(math.floor((box[0] - fbox[0]) / gdx))), min(gnx - 1, int(math.ceil((box[2] - fbox[0]) / gdx)))
    j0, j1 = max(0, int(math.floor((box[1] - fbox[1]) / gdy))), min(gny - 1, int(math.ceil((box[3] - fbox[1]) / gdy)))
    for j in range(j0, j1 + 1):
        for i in range(i0, i1 + 1):
            if grid[j][i] is not None:
                index[(i, j)] = ground_mesh.Vertices.Add((fbox[0] + i * gdx) * scale, (fbox[1] + j * gdy) * scale, (grid[j][i] - datum) * scale)
    valid = [grid[j][i] for j in range(j0, j1 + 1) for i in range(i0, i1 + 1) if grid[j][i] is not None] or valid
    for j in range(j0, j1):
        for i in range(i0, i1):
            q = [index.get(k) for k in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))]
            if None not in q:
                ground_mesh.Faces.AddFace(*q)
    ground_mesh.Normals.ComputeNormals()
    ground_mesh.Compact()
    if contour_step and contour_step > 0:
        level = math.ceil(min(valid) / contour_step) * contour_step
        while level <= max(valid):
            plane = rg.Plane(rg.Point3d(0, 0, (level - datum) * scale), rg.Vector3d.ZAxis)
            for pl in rg.Intersect.Intersection.MeshPlane(ground_mesh, plane) or []:
                contours.append(rg.PolylineCurve(pl))
            level += contour_step

footprints, buildings, known, building_rings = [], [], {}, []
for ring, h, how, ds in data["buildings"]:
    local = [to_xy(*v) for v in ring]
    cx, cy = sum(p[0] for p in local[:-1]) / max(len(local) - 1, 1), sum(p[1] for p in local[:-1]) / max(len(local) - 1, 1)
    if not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
        continue  # mostly outside the context (a tile reached further): it would hang off the ground
    c = curve(local, True)
    footprints.append(c)
    if h is not None:
        known[ds or "unknown"] = known.get(ds or "unknown", 0) + 1
    height = h if h is not None else assumed_height
    building_rings.append((local, height))
    if height and height > 0:
        base = min(ground(x, y) for x, y in local) if grid else 0.0  # stand on the lowest ground under the footprint
        b = extrude(c, height, base)
        if b:
            buildings.append(b)

carriageway = footpath = nature_strip = kerb = None
street_note = None
st = layers.get("streets")
if st:
    try:
        near = lambda pts: overlaps(pts, box, 25.0)  # the bucket's streets beyond the context only cost time
        roads_near = [r for r in st["roads"] if near([to_xy(*v) for path in (r["coords"] if r["coords"] and isinstance(r["coords"][0][0], list) else [r["coords"]]) for v in path])]
        reserves_near = [rs for rs in st["reserves"] if rs and near([to_xy(*v) for v in rs[0]])]
        surf = street_surfaces(roads_near, reserves_near, box, ground)
        for m in surf.values():
            m.Transform(to_model)
            m.Normals.ComputeNormals()
            m.Compact()
        carriageway, footpath, nature_strip, kerb = surf["carriageway"], surf["footpath"], surf["nature_strip"], surf["kerb"]
        measured = sum(1 for r in roads_near if r.get("reserve_width_m") is not None)
        street_note = "Streets: %d centrelines, %d fitted to measured road reserves, the rest at class defaults; verges %s" % (
            len(roads_near), measured, "paved (central / commercial zone)" if paved else "with nature strips where wide enough")
    except Exception as ex:
        layer_errors["streets"] = "%s: %s" % (type(ex).__name__, ex)

# ── trees: shapes by species where known (City of Melbourne), else by proportion ──

CONIFERS = {"pinus", "cupressus", "picea", "araucaria", "cedrus", "callitris", "sequoia", "sequoiadendron", "juniperus",
            "abies", "taxodium", "podocarpus", "agathis", "chamaecyparis", "thuja", "metasequoia"}
PALMS = {"phoenix", "washingtonia", "archontophoenix", "syagrus", "livistona", "trachycarpus", "howea", "butia", "cocos"}
EUCALYPTS = {"eucalyptus", "corymbia", "angophora"}
ICO = rg.Mesh.CreateIcoSphere(rg.Sphere(rg.Point3d.Origin, 1.0), 1)  # 80 faces: low-poly crowns read as foliage


def tree_shape(t, h, rx, ry):
    name = " ".join(str(t.get(k) or "") for k in ("species", "common_name")).lower()
    genus = name.split()[0] if name.split() else ""
    if genus in PALMS or "palm" in name:
        return "palm"
    if genus in CONIFERS or " pine" in " " + name or "cypress" in name or "cedar" in name:
        return "conifer"
    if genus in EUCALYPTS or " gum" in " " + name or (t.get("dense_canopy") and h > 18):
        return "eucalypt"
    if h > 2.2 * 2 * max(rx, ry):
        return "columnar"
    return "broad"


def blob(rng, c, rx, ry, rz, jitter=0.16):
    """A lumpy low-poly ball: an icosphere with every vertex pushed in or out a little."""
    m = ICO.DuplicateMesh()
    for i in range(m.Vertices.Count):
        v = m.Vertices[i]
        k = 1.0 + rng.uniform(-jitter, jitter)
        m.Vertices.SetVertex(i, c.X + v.X * rx * k, c.Y + v.Y * ry * k, c.Z + v.Z * rz * k)
    return m


def limb(p0, p1, r0, r1, sides=6, cap=False):
    """A tapered tube from p0 (radius r0) to p1 (radius r1, 0 = a cone point), as a mesh."""
    axis = p1 - p0
    if axis.Length < 1e-6:
        return None
    plane = rg.Plane(p0, axis)
    m = rg.Mesh()
    for k in range(sides):
        a = 2 * math.pi * k / sides
        m.Vertices.Add(p0 + plane.XAxis * (r0 * math.cos(a)) + plane.YAxis * (r0 * math.sin(a)))
    if r1 <= 0:  # a cone: one apex vertex (a ring of coincident ones would make zero-area, invalid faces)
        apex = m.Vertices.Add(p1)
        for k in range(sides):
            m.Faces.AddFace(k, (k + 1) % sides, apex)
    else:
        for k in range(sides):
            a = 2 * math.pi * k / sides
            m.Vertices.Add(p1 + plane.XAxis * (r1 * math.cos(a)) + plane.YAxis * (r1 * math.sin(a)))
        for k in range(sides):
            n = (k + 1) % sides
            m.Faces.AddFace(k, n, sides + n, sides + k)
    if cap:  # close the base (cones)
        centre = m.Vertices.Add(p0)
        for k in range(sides):
            m.Faces.AddFace(centre, (k + 1) % sides, k)
    return m


def tree_meshes(shape, rng, x, y, z0, h, rx, ry):
    """-> (crown mesh, wood mesh) for one tree standing at (x, y, z0), in metres."""
    crown, wood = rg.Mesh(), rg.Mesh()
    base = rg.Point3d(x, y, z0)
    r = min(max(0.012 * h + 0.06, 0.08), 0.6)  # trunk radius at the ground
    if shape == "palm":
        top = rg.Point3d(x + rng.uniform(-0.04, 0.04) * h, y + rng.uniform(-0.04, 0.04) * h, z0 + h)
        wood.Append(limb(base, top, r * 0.9, r * 0.7, 7))
        reach = max(1.2, 0.95 * max(rx, ry))
        for k in range(10):  # drooping fronds: thin blades out and down from the crown
            a = 2 * math.pi * (k + rng.uniform(-0.2, 0.2)) / 10
            d = rg.Vector3d(math.cos(a), math.sin(a), 0)
            side = rg.Vector3d(-d.Y, d.X, 0)
            pts = [top + d * (reach * f) + rg.Vector3d(0, 0, reach * (0.25 * f - 0.65 * f * f)) for f in (0.0, 0.35, 0.7, 1.0)]
            widths = (0.05, 0.32, 0.26)
            i0 = crown.Vertices.Count
            for p, w in zip(pts, widths):
                crown.Vertices.Add(p - side * w)
                crown.Vertices.Add(p + side * w)
            tip = crown.Vertices.Add(pts[3])  # one tip vertex: a zero-width quad would be an invalid face
            for j in range(2):
                crown.Faces.AddFace(i0 + 2 * j, i0 + 2 * j + 1, i0 + 2 * j + 3, i0 + 2 * j + 2)
            crown.Faces.AddFace(i0 + 4, i0 + 5, tip)
        crown.Append(blob(rng, top, 0.35, 0.35, 0.3, 0.1))
        return crown, wood
    if shape == "conifer":
        start = z0 + max(1.2, 0.18 * h)
        wood.Append(limb(base, rg.Point3d(x, y, z0 + h * 0.97), r, r * 0.25, 6))
        tiers = 4
        for k in range(tiers):  # stacked cones, narrower towards the top
            f0 = k / float(tiers)
            zb = start + (h - start) * f0 * 0.82
            zt = min(z0 + h, zb + (h - start) * 0.42)
            w = (1.0 - 0.72 * f0)
            c = limb(rg.Point3d(x, y, zb), rg.Point3d(x, y, zt), max(rx, ry) * w * rng.uniform(0.9, 1.08), 0.0, 9, cap=True)
            if c:
                crown.Append(c)
        return crown, wood
    depth = max(1.2, min((0.5 if shape == "eucalypt" else 0.62 if shape == "broad" else 0.78) * h, h - 1.6))
    centre = rg.Point3d(x, y, z0 + h - depth / 2)
    split = rg.Point3d(x + rng.uniform(-0.1, 0.1) * rx, y + rng.uniform(-0.1, 0.1) * ry, z0 + h - depth * (0.75 if shape == "eucalypt" else 0.85))
    wood.Append(limb(base, split, r, r * 0.65, 6))
    blobs = []
    if shape == "columnar":
        n = 4
        for k in range(n):  # stacked, narrowing upwards
            f = k / float(n - 1)
            c = rg.Point3d(x + rng.uniform(-0.1, 0.1) * rx, y + rng.uniform(-0.1, 0.1) * ry, centre.Z - depth * 0.32 + f * depth * 0.62)
            s = 1.0 - 0.45 * f
            blobs.append((c, rx * s, ry * s, depth * 0.3))
    else:
        open_crown = shape == "eucalypt"
        n = max(3, min(7, int(2 + (rx + ry) / (2.4 if open_crown else 1.6))))
        blobs.append((rg.Point3d(centre.X, centre.Y, centre.Z + depth * 0.12), rx * 0.62, ry * 0.62, depth * 0.36))
        for k in range(n - 1):  # a ring of clumps round the middle, at varied heights
            a = 2 * math.pi * (k + rng.uniform(-0.25, 0.25)) / (n - 1)
            spread = rng.uniform(0.38, 0.55) if not open_crown else rng.uniform(0.5, 0.68)
            c = rg.Point3d(centre.X + math.cos(a) * rx * spread, centre.Y + math.sin(a) * ry * spread,
                           centre.Z + rng.uniform(-0.28, 0.2) * depth)
            s = rng.uniform(0.42, 0.58) if not open_crown else rng.uniform(0.32, 0.45)
            blobs.append((c, rx * s, ry * s, depth * s * 0.85))
    for c, bx, by, bz in blobs:
        crown.Append(blob(rng, c, bx, by, bz))
    # limbs from the trunk's split into the larger clumps, so the crown reads as held up by branches
    for c, bx, by, bz in sorted(blobs, key=lambda b: -b[1])[:3 if shape != "eucalypt" else 5]:
        tip = rg.Point3d(split.X + (c.X - split.X) * 0.8, split.Y + (c.Y - split.Y) * 0.8, split.Z + (c.Z - split.Z) * 0.8)
        l = limb(split, tip, r * 0.45, r * 0.15, 5)
        if l:
            wood.Append(l)
    return crown, wood


canopy = trunk = None
tree_note = None
tr = layers.get("trees")
if tr is not None:
    import random
    canopy, trunk = rg.Mesh(), rg.Mesh()
    shown = roof = species = 0
    shapes = {}
    for t in tr:
        x, y = to_xy(t["lon"], t["lat"])
        if not (box[0] < x < box[2] and box[1] < y < box[3]):
            continue
        h = t["height_m"] if t["height_m"] is not None and t["height_m"] >= 1.5 else t["height_est_m"] or 5.0
        under = next((bh for ring, bh in building_rings if in_rings(x, y, [ring])), None)
        if under is not None and (h > 45 or (under and h > 0.7 * under)):
            roof += 1  # the LiDAR read the roof under it, not a tree
            continue
        rx = max(t.get("canopy_rx_m") or t.get("canopy_radius_m") or 2.0, 0.6)
        ry = max(t.get("canopy_ry_m") or t.get("canopy_radius_m") or 2.0, 0.6)
        shape = tree_shape(t, h, rx, ry)
        shapes[shape] = shapes.get(shape, 0) + 1
        rng = random.Random(t.get("ufi") or "%.1f,%.1f" % (x, y))  # the same tree looks the same every run
        c, w = tree_meshes(shape, rng, x, y, ground(x, y), h, rx, ry)
        canopy.Append(c)
        trunk.Append(w)
        shown += 1
        species += 1 if t.get("species") else 0
    for m in (canopy, trunk):
        m.Transform(to_model)
        m.Faces.CullDegenerateFaces()  # belt and braces: an invalid mesh stops Grasshopper's bake with a dialog
        m.Normals.ComputeNormals()
        m.Compact()
    tree_note = ("Trees: %d from Vicmap Tree Urban (crowns from 2019 imagery, heights from 2017-18 LiDAR): %s%s%s" % (
        shown, ", ".join("%d %s" % (v, k) for k, v in sorted(shapes.items(), key=lambda kv: -kv[1])),
        ("; %d on roofs left out" % roof) if roof else "", ("; species known for %d (City of Melbourne)" % species) if species else "")
        if tr else "Trees: none here (Vicmap Tree Urban covers metro Melbourne, Ballarat, Shepparton, Wangaratta and Sale)")
ground = ground_mesh


def label(z):
    return "%s - %s%s" % (z["zone_code"], nice(z["zone_description"]), "" if z["extent"] == "site" else " (edge only)")

zone = "; ".join(label(z) for z in data["zones"] if z["extent"] == "site") or "; ".join(label(z) for z in data["zones"]) or "unknown"
overlays = [label(z) for z in data["overlays"]]
lga = next((z["lga"] for z in data["zones"] + data["overlays"] if z.get("lga")), None)
lga_nice = lga.title() if lga else None

g = data["geocode"]
terrain_info = layers.get("terrain")
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
]
if grid and valid:
    lines.append("Terrain: %s on a %.1f m grid, %.1f-%.1f m AHD; z = 0 is %.2f m AHD (the ground at the site centre)" % (
        terrain_info["source"], spacing, min(valid), max(valid), datum))
if street_note:
    lines.append(street_note)
if tree_note:
    lines.append(tree_note)
lines += [
    "Geometry: %s, centred on the site (lat %.6f, lon %.6f), Y = true north; curves flat at z = 0%s" % (
        units, data["origin"][1], data["origin"][0], ", ground, buildings, streets and trees on the terrain" if grid and valid else ""),
    "Sources: Vicmap (State of Victoria, CC BY 4.0); Overture Maps Foundation and OpenStreetMap contributors (ODbL)%s. Retrieved %s%s."
    % ("; DEM of Australia from LiDAR 5 m (Geoscience Australia, CC BY 4.0)" if grid else "", data["retrieved"], ", from cache" if from_cache else ""),
    "Not a Planning Property Report: confirm with VicPlan before relying on it.",
]
if FALLBACKS:
    lines.insert(-2, "Note: Vicmap's ArcGIS property/parcel queries failed; read from the open-data WFS instead (%s)." % ", ".join(sorted(FALLBACKS)))
for k, err in list(data["errors"].items()) + list(layer_errors.items()):
    lines.append("WARNING %s failed: %s" % (k, err))
    print("%s failed: %s" % (k, err))
if "Microsoft ML Buildings" in known:
    lines.insert(-2, "Note: Microsoft ML heights ran about 2.3 m low against measured heights in inner Melbourne.")
info = "\n".join(lines)
