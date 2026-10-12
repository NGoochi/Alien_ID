"""
Alien Victorian site data for vic_site_map (keep this file next to it): ground elevation, urban trees and street
cross-sections, from open data with no API key. Standard library only.

elevation_grid(lon_min, lat_min, lon_max, lat_max, nx, ny, info=None) -> (grid, source, resolution_m)
    grid[j][i] = ground height (m AHD) or None; row 0 = south edge, column 0 = west edge.
    GA 5 m LiDAR DEM (bare earth, partial coverage) -> Vicmap Elevation 10 m DEM (statewide) fills gaps;
    Terrarium / Open-Meteo (surface models: read rooftops and canopy) only when both DEMs fail.
trees(lon_min, lat_min, lon_max, lat_max, species=True, info=None) -> [dict]
    Vicmap Vegetation Tree Urban points (metro Melbourne, Ballarat, Shepparton, Wangaratta, Sale) with height and
    canopy half-widths; species from the City of Melbourne inventory inside that council.
streets(lon_min, lat_min, lon_max, lat_max, paved_verges=None, info=None) -> {"roads": [dict], "reserves": [rings]}
    Vicmap Transport centrelines with a cross-section (carriageway, verge, footpath, nature strip, median) from the
    road class, fitted to the road reserve measured from Vicmap Property road casement polygons, plus those polygons.

Measured in Oct 2026 (see docs/DEV_NOTES.md): each layer answers in 0.1-0.5 s per site.
"""
import gzip
import json
import math
import struct
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Alien/2.0 (Grasshopper site map node)"

# --- endpoints -----------------------------------------------------------------------------
WFS_URL = "https://opendata.maps.vic.gov.au/geoserver/wfs"
VAAS_URL = "https://services-ap1.arcgis.com/P744lA0wf4LlBZ84/ArcGIS/rest/services"
VIC_DEM_TILE = ("https://tiles-ap1.arcgis.com/P744lA0wf4LlBZ84/arcgis/rest/services/"
                "Vicmap_10m_DEM/ImageServer/tile/{z}/{y}/{x}")
GA_WCS_URL = "https://services.ga.gov.au/gis/services/DEM_LiDAR_5m_2025/MapServer/WCSServer"
TERRARIUM_TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/elevation"
COM_TREES_URL = ("https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
                 "trees-with-species-and-dimensions-urban-forest/exports/json")

# City of Melbourne LGA approximated by two boxes (north of Victoria Pde it stops at Nicholson St;
# south of it East Melbourne reaches Hoddle St) -- only query the council tree inventory inside it.
COM_BOXES = ((144.895, -37.8095, 144.9745, -37.773), (144.895, -37.851, 144.992, -37.8095))
# Central city where verges are fully paved (Hoddle grid, Southbank, Docklands) -- crude default only;
# the node should prefer planning zones (CCZ/ACZ/C1Z/MUZ/DZ => paved, GRZ/NRZ/LDRZ => nature strips).
CENTRAL_CITY_BOX = (144.935, -37.830, 144.975, -37.805)

# GA DEM_LiDAR_5m native grid (degrees, GDA94; from WCS DescribeCoverage: origin = centre of cell 0,0)
GA_RES_X = 5.5063478185957097e-05
GA_RES_Y = 5.16012325277870332e-05
GA_ORIGIN_X = 114.098579728147413
GA_ORIGIN_Y = -9.86610996464120404


# =========================================================================================
# HTTP helpers
# =========================================================================================
def http_get(url, params=None, data=None, timeout=20, retries=1, headers=None):
    """GET (or POST when data is given). Returns bytes. Raises on final failure."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdr = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if headers:
        hdr.update(headers)
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding", "") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:  # timeouts, resets
            last = e
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    raise last


def http_json(url, params=None, data=None, timeout=20, retries=1):
    return json.loads(http_get(url, params, data, timeout, retries).decode("utf-8"))


def wfs_features(layer, bbox, props=None, timeout=30, page=10000):
    """GetFeature from the Vicmap open-data GeoServer as GeoJSON features (lon/lat, EPSG:4326)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    out, start = [], 0
    while True:
        p = {
            "service": "WFS", "version": "1.1.0", "request": "GetFeature",
            "typeName": "open-data-platform:" + layer,
            "outputFormat": "application/json", "srsName": "EPSG:4326",
            # urn CRS => lat,lon axis order in the bbox for WFS 1.1.0
            "bbox": "%.7f,%.7f,%.7f,%.7f,urn:ogc:def:crs:EPSG::4326" % (lat_min, lon_min, lat_max, lon_max),
            "maxFeatures": page, "startIndex": start,
        }
        if props:
            p["propertyName"] = ",".join(props)
        d = http_json(WFS_URL, p, timeout=timeout)
        feats = d.get("features", [])
        out.extend(feats)
        total = d.get("totalFeatures") or d.get("numberMatched")
        if not isinstance(total, int) or len(out) >= total or len(feats) < page:
            return out
        start += len(feats)


# =========================================================================================
# Geometry helpers
# =========================================================================================
def metres_per_degree(lat):
    """(m per deg lon, m per deg lat) on WGS84/GRS80 at latitude lat."""
    p = math.radians(lat)
    m_lat = 111132.954 - 559.822 * math.cos(2 * p) + 1.175 * math.cos(4 * p)
    m_lon = 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p) + 0.118 * math.cos(5 * p)
    return m_lon, m_lat


class LocalFrame(object):
    """Simple local tangent-plane approximation (east/north metres) about (lon0, lat0)."""

    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.kx, self.ky = metres_per_degree(lat0)

    def fwd(self, lon, lat):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def inv(self, x, y):
        return self.lon0 + x / self.kx, self.lat0 + y / self.ky


def gda2020_to_gda94(lon, lat):
    """Inverse of EPSG:8048 (GDA94 -> GDA2020, 7-parameter coordinate-frame Helmert), iterated.
    Shift is ~1.5-1.8 m. Used because the GA WCS only serves EPSG:4283 (GDA94)."""
    def helmert94to20(lon94, lat94):
        a, f = 6378137.0, 1 / 298.257222101
        e2 = f * (2 - f)
        la, ph = math.radians(lon94), math.radians(lat94)
        n = a / math.sqrt(1 - e2 * math.sin(ph) ** 2)
        x = n * math.cos(ph) * math.cos(la)
        y = n * math.cos(ph) * math.sin(la)
        z = n * (1 - e2) * math.sin(ph)
        s = 1 + (-0.009994e-6)
        sec = math.pi / (180 * 3600)
        rx, ry, rz = -0.0394924 * sec, -0.0327221 * sec, -0.0328979 * sec
        tx, ty, tz = 0.06155, -0.01087, -0.04019
        # coordinate-frame rotation convention (EPSG method 9607)
        x2 = tx + s * (x + rz * y - ry * z)
        y2 = ty + s * (-rz * x + y + rx * z)
        z2 = tz + s * (ry * x - rx * y + z)
        p = math.hypot(x2, y2)
        lat2 = math.atan2(z2, p * (1 - e2))
        for _ in range(5):
            n = a / math.sqrt(1 - e2 * math.sin(lat2) ** 2)
            lat2 = math.atan2(z2 + e2 * n * math.sin(lat2), p)
        return math.degrees(math.atan2(y2, x2)), math.degrees(lat2)

    lon94, lat94 = lon, lat
    for _ in range(3):
        l2, p2 = helmert94to20(lon94, lat94)
        lon94 += lon - l2
        lat94 += lat - p2
    return lon94, lat94


def lonlat_to_tile(lon, lat, z):
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


# =========================================================================================
# Raster decoders (pure Python)
# =========================================================================================
def lerc1_decode(b):
    """Decode an Esri LERC v1 ('CntZImage ') float tile -> (width, height, flat list[float|None])."""
    if b[:10] != b"CntZImage ":
        raise ValueError("not a LERC1 blob")
    _ver, _typ, h, w = struct.unpack_from("<iiii", b, 10)
    maxz, = struct.unpack_from("<d", b, 26)
    fp = 34
    # --- validity mask ("count" image), RLE bitmask
    _mby, _mbx, mnb, mmax = struct.unpack_from("<iiif", b, fp)
    fp += 16
    valid = None
    if mnb > 0:
        bits = bytearray()
        ip = fp
        cnt, = struct.unpack_from("<h", b, ip)
        ip += 2
        while cnt != -32768:
            if cnt > 0:
                bits += b[ip:ip + cnt]
                ip += cnt
            else:
                bits += bytes([b[ip]]) * (-cnt)
                ip += 1
            cnt, = struct.unpack_from("<h", b, ip)
            ip += 2
        fp += mnb
        valid = [bool(bits[k >> 3] & (128 >> (k & 7))) for k in range(w * h)]
    elif mmax == 0:
        valid = [False] * (w * h)
    # --- z values, tiled
    nby, nbx, nb, zmax = struct.unpack_from("<iiif", b, fp)
    fp += 16
    out = [None] * (w * h)
    if nb == 0 and nby == 0:
        for k in range(w * h):
            if valid is None or valid[k]:
                out[k] = zmax
        return w, h, out
    th0, tw0, scale = h // nby, w // nbx, 2.0 * maxz
    r0 = 0
    for ty in range(nby + 1):
        th = th0 if ty < nby else h % nby
        if th == 0:
            continue
        c0 = 0
        for tx in range(nbx + 1):
            tw = tw0 if tx < nbx else w % nbx
            if tw == 0:
                continue
            idx = [(r0 + r) * w + c0 + c for r in range(th) for c in range(tw)]
            if valid is not None:
                idx = [k for k in idx if valid[k]]
            hb = b[fp]
            fp += 1
            enc, ot = hb & 3, hb >> 6
            if enc == 2:                      # constant 0
                for k in idx:
                    out[k] = 0.0
            elif enc == 0:                    # raw float32
                vals = struct.unpack_from("<%df" % len(idx), b, fp)
                fp += 4 * len(idx)
                for k, v in zip(idx, vals):
                    out[k] = v
            else:
                if ot == 0:
                    off, = struct.unpack_from("<f", b, fp)
                    fp += 4
                elif ot == 1:
                    off, = struct.unpack_from("<h", b, fp)
                    fp += 2
                else:
                    off, = struct.unpack_from("<b", b, fp)
                    fp += 1
                if enc == 3:                  # constant offset
                    for k in idx:
                        out[k] = off
                else:                         # bit-stuffed quantised offsets
                    h2 = b[fp]
                    fp += 1
                    nbits, nt = h2 & 63, h2 >> 6
                    if nt == 0:
                        n, = struct.unpack_from("<I", b, fp)
                        fp += 4
                    elif nt == 1:
                        n, = struct.unpack_from("<H", b, fp)
                        fp += 2
                    else:
                        n = b[fp]
                        fp += 1
                    if nbits == 0:
                        for k in idx:
                            out[k] = off
                    else:
                        nuints = (n * nbits + 31) // 32
                        tailbytes = (((n * nbits) & 31) + 7) >> 3
                        notneeded = 4 - tailbytes if tailbytes else 0
                        nbytes = nuints * 4 - notneeded
                        raw = b[fp:fp + nbytes] + b"\0" * notneeded
                        fp += nbytes
                        words = list(struct.unpack("<%dI" % nuints, raw))
                        if notneeded:
                            words[-1] = (words[-1] << (8 * notneeded)) & 0xFFFFFFFF
                        big = 0
                        for wd in words:
                            big = (big << 32) | wd
                        total, mask = nuints * 32, (1 << nbits) - 1
                        nmax = (zmax - off) / scale if scale else 0
                        for i, k in enumerate(idx[:n]):
                            v = (big >> (total - (i + 1) * nbits)) & mask
                            out[k] = off + v * scale if v < nmax else zmax
            c0 += tw
        r0 += th
    return w, h, out


def tiff_float_decode(b, nodata_values=(0.0,)):
    """Minimal GeoTIFF reader: single-band float32, uncompressed, stripped or tiled.
    Returns (w, h, flat list[float|None], (x0, y0, dx, dy)) -- x0,y0 = top-left corner."""
    bo = "<" if b[:2] == b"II" else ">"
    if struct.unpack(bo + "H", b[2:4])[0] != 42:
        raise ValueError("not a classic TIFF")
    off, = struct.unpack(bo + "I", b[4:8])
    n, = struct.unpack(bo + "H", b[off:off + 2])
    sizes = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("I", 8), 11: ("f", 4),
             12: ("d", 8), 16: ("Q", 8)}
    tags = {}
    for i in range(n):
        e = off + 2 + i * 12
        tag, typ, cnt = struct.unpack(bo + "HHI", b[e:e + 8])
        fmt, sz = sizes.get(typ, ("B", 1))
        total = sz * cnt
        pos = e + 8 if total <= 4 else struct.unpack(bo + "I", b[e + 8:e + 12])[0]
        if typ == 2:
            tags[tag] = b[pos:pos + cnt].rstrip(b"\0").decode("latin1")
        elif typ == 5:
            tags[tag] = struct.unpack(bo + "%dI" % (2 * cnt), b[pos:pos + total])
        else:
            tags[tag] = struct.unpack(bo + "%d%s" % (cnt, fmt), b[pos:pos + total])
    w, h = tags[256][0], tags[257][0]
    if tags.get(259, (1,))[0] != 1 or tags.get(258, (32,))[0] != 32:
        raise ValueError("only uncompressed float32 supported")
    nod = set(nodata_values)
    if 42113 in tags:
        try:
            nod.add(float(tags[42113]))
        except ValueError:
            pass
    vals = [None] * (w * h)
    if 322 in tags:  # tiled
        tw, th = tags[322][0], tags[323][0]
        tx_n = (w + tw - 1) // tw
        for ti, (o, c) in enumerate(zip(tags[324], tags[325])):
            if c < 4 * tw * th:
                continue  # sparse / empty tile => nodata
            tv = struct.unpack(bo + "%df" % (tw * th), b[o:o + 4 * tw * th])
            r0, c0 = (ti // tx_n) * th, (ti % tx_n) * tw
            for r in range(min(th, h - r0)):
                base = (r0 + r) * w + c0
                row = tv[r * tw:r * tw + min(tw, w - c0)]
                for cc, v in enumerate(row):
                    if v == v and v not in nod and v > -1e30:
                        vals[base + cc] = v
    else:
        data = b"".join(b[o:o + c] for o, c in zip(tags[273], tags[279]))
        tv = struct.unpack(bo + "%df" % (w * h), data[:4 * w * h])
        for k, v in enumerate(tv):
            if v == v and v not in nod and v > -1e30:
                vals[k] = v
    sx, sy = tags[33550][0], tags[33550][1]
    tp = tags[33922]
    x0, y0 = tp[3] - tp[0] * sx, tp[4] + tp[1] * sy
    return w, h, vals, (x0, y0, sx, sy)


def png_rgb_decode(b):
    """Minimal PNG decoder for 8-bit RGB/RGBA non-interlaced images -> (w, h, bpp, bytearray)."""
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w, h, bpp = 8, [], 0, 0, 3
    while pos < len(b):
        ln, = struct.unpack(">I", b[pos:pos + 4])
        typ = b[pos + 4:pos + 8]
        chunk = b[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, bd, ct, _cm, _fm, il = struct.unpack(">IIBBBBB", chunk)
            if bd != 8 or il != 0 or ct not in (2, 6):
                raise ValueError("unsupported PNG")
            bpp = 3 if ct == 2 else 4
        elif typ == b"IDAT":
            idat.append(chunk)
        elif typ == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    i = 0
    for y in range(h):
        ft = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if ft == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif ft == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif ft == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                bb = prev[x]
                p = a + bb - c
                pa, pb, pc = abs(p - a), abs(p - bb), abs(p - c)
                line[x] = (line[x] + (a if (pa <= pb and pa <= pc) else (bb if pb <= pc else c))) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bpp, out


def _bilinear(get, fx, fy):
    """Bilinear interpolation on an integer lattice via get(ix, iy) -> float|None."""
    x0, y0 = int(math.floor(fx)), int(math.floor(fy))
    ax, ay = fx - x0, fy - y0
    pts = ((x0, y0, (1 - ax) * (1 - ay)), (x0 + 1, y0, ax * (1 - ay)),
           (x0, y0 + 1, (1 - ax) * ay), (x0 + 1, y0 + 1, ax * ay))
    s = ws = 0.0
    for ix, iy, wgt in pts:
        if wgt <= 1e-9:
            continue
        v = get(ix, iy)
        if v is None:
            if wgt > 0.5:
                return None  # nearest sample missing
            continue
        s += v * wgt
        ws += wgt
    return s / ws if ws > 0 else None


def _nodes(lon_min, lat_min, lon_max, lat_max, nx, ny):
    lons = [lon_min + i * (lon_max - lon_min) / max(nx - 1, 1) for i in range(nx)]
    lats = [lat_min + j * (lat_max - lat_min) / max(ny - 1, 1) for j in range(ny)]
    return lons, lats


# =========================================================================================
# 1. ELEVATION sources
# =========================================================================================
def _elev_ga_lidar(lons, lats):
    """GA 'DEM of Australia derived from LiDAR 5 m' via WCS 1.0.0 (GeoTIFF float32, EPSG:4283)."""
    lon_min, lon_max, lat_min, lat_max = lons[0], lons[-1], lats[0], lats[-1]
    # node positions are GDA2020 (~WGS84); the service grid is GDA94 -> shift by the Helmert offset (~1.5 m)
    c_lon, c_lat = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2
    l94, p94 = gda2020_to_gda94(c_lon, c_lat)
    dlon, dlat = l94 - c_lon, p94 - c_lat
    # Snap the request to the native 5 m cell edges and ask for native width/height: the server does a
    # nearest-neighbour resample and silently snaps, so an unsnapped bbox gives a geotransform that is
    # off by up to half a cell.
    ex0, ey0 = GA_ORIGIN_X - GA_RES_X / 2.0, GA_ORIGIN_Y + GA_RES_Y / 2.0
    c0 = int(math.floor((lon_min + dlon - ex0) / GA_RES_X)) - 1
    c1 = int(math.ceil((lon_max + dlon - ex0) / GA_RES_X)) + 1
    r0 = int(math.floor((ey0 - (lat_max + dlat)) / GA_RES_Y)) - 1
    r1 = int(math.ceil((ey0 - (lat_min + dlat)) / GA_RES_Y)) + 1
    bb = (ex0 + c0 * GA_RES_X, ey0 - r1 * GA_RES_Y, ex0 + c1 * GA_RES_X, ey0 - r0 * GA_RES_Y)
    b = http_get(GA_WCS_URL, {
        "service": "WCS", "version": "1.0.0", "request": "GetCoverage", "coverage": "1",
        "crs": "EPSG:4283", "bbox": "%.12f,%.12f,%.12f,%.12f" % bb,
        "width": c1 - c0, "height": r1 - r0, "format": "GeoTIFF"}, timeout=30)
    if b[:2] not in (b"II", b"MM"):
        raise IOError("GA WCS returned non-TIFF: %r" % b[:120])
    w, h, vals, (x0, y0, sx, sy) = tiff_float_decode(b, nodata_values=(0.0,))

    def get(ix, iy):
        if 0 <= ix < w and 0 <= iy < h:
            return vals[iy * w + ix]
        return None
    grid = []
    for lat in lats:
        row = []
        for lon in lons:
            fx = (lon + dlon - x0) / sx - 0.5
            fy = (y0 - (lat + dlat)) / sy - 0.5
            row.append(_bilinear(get, fx, fy))
        grid.append(row)
    return grid


def _fetch_tiles(url_tmpl, keys, z, decode, timeout=20):
    def one(k):
        try:
            return k, decode(http_get(url_tmpl.format(z=z, x=k[0], y=k[1]), timeout=timeout))
        except Exception:
            return k, None
    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(one, keys))


def _elev_vicmap10(lons, lats, z=15):
    """Vicmap Elevation 10 m DEM, hosted tile image service (LERC1, 257x257 px, maxLOD 15 = 4.78 m WebMercator)."""
    pts = [[lonlat_to_tile(lon, lat, z) for lon in lons] for lat in lats]
    keys = set()
    for row in pts:
        for x, y in row:
            keys.add((int(x), int(y)))
    tiles = _fetch_tiles(VIC_DEM_TILE, sorted(keys), z, lerc1_decode)
    grid = []
    for row in pts:
        out = []
        for x, y in row:
            t = tiles.get((int(x), int(y)))
            if t is None:
                out.append(None)
                continue
            w, h, v = t
            # 257 px tiles: pixel i is centred on tile-local coordinate i/256 (edges shared with neighbours)
            fx, fy = (x - int(x)) * (w - 1), (y - int(y)) * (h - 1)
            out.append(_bilinear(lambda ix, iy: v[iy * w + ix] if (0 <= ix < w and 0 <= iy < h) else None,
                                 fx, fy))
        grid.append(out)
    return grid


def _elev_terrarium(lons, lats, z=15):
    """AWS Open Data 'Terrain Tiles' (Mapzen/Tilezen) terrarium PNG: h = R*256 + G + B/256 - 32768."""
    def dec(b):
        w, h, bpp, px = png_rgb_decode(b)
        return [px[k] * 256.0 + px[k + 1] + px[k + 2] / 256.0 - 32768.0 for k in range(0, w * h * bpp, bpp)]
    pts = [[lonlat_to_tile(lon, lat, z) for lon in lons] for lat in lats]
    keys = set()
    for row in pts:
        for x, y in row:
            gx, gy = x * 256 - 0.5, y * 256 - 0.5
            for ddx in (0, 1):
                for ddy in (0, 1):
                    keys.add((int((math.floor(gx) + ddx) // 256), int((math.floor(gy) + ddy) // 256)))
    tiles = _fetch_tiles(TERRARIUM_TILE, sorted(keys), z, dec)

    def get(ix, iy):
        t = tiles.get((ix // 256, iy // 256))
        return None if t is None else t[(iy % 256) * 256 + (ix % 256)]
    return [[_bilinear(get, x * 256 - 0.5, y * 256 - 0.5) for x, y in row] for row in pts]


def _elev_open_meteo(lons, lats, max_pts=100):
    """Open-Meteo elevation API (Copernicus DEM GLO-90). <=100 points per call; coarse grid then bilinear."""
    n = max(2, min(len(lons), len(lats), int(math.sqrt(max_pts))))
    clon = [lons[0] + i * (lons[-1] - lons[0]) / (n - 1) for i in range(n)]
    clat = [lats[0] + j * (lats[-1] - lats[0]) / (n - 1) for j in range(n)]
    qlat, qlon = [], []
    for la in clat:
        for lo in clon:
            qlat.append("%.6f" % la)
            qlon.append("%.6f" % lo)
    d = http_json(OPEN_METEO_URL, {"latitude": ",".join(qlat), "longitude": ",".join(qlon)}, timeout=20)
    ev = d["elevation"]
    coarse = [ev[j * n:(j + 1) * n] for j in range(n)]
    grid = []
    for lat in lats:
        fy = (lat - clat[0]) / (clat[-1] - clat[0]) * (n - 1) if clat[-1] != clat[0] else 0
        row = []
        for lon in lons:
            fx = (lon - clon[0]) / (clon[-1] - clon[0]) * (n - 1) if clon[-1] != clon[0] else 0
            row.append(_bilinear(lambda ix, iy: coarse[min(iy, n - 1)][min(ix, n - 1)], fx, fy))
        grid.append(row)
    return grid


ELEVATION_SOURCES = [
    # key, function, display name, nominal resolution (m)
    ("ga_lidar_5m", _elev_ga_lidar, "GA DEM LiDAR 5m (AHD)", 5.0),
    ("vicmap_10m", _elev_vicmap10, "Vicmap Elevation DEM 10m (AHD)", 10.0),
    ("terrarium", _elev_terrarium, "AWS Terrain Tiles terrarium z15 (SRTM-based in Vic)", 30.0),
    ("open_meteo", _elev_open_meteo, "Open-Meteo Copernicus GLO-90", 90.0),
]


def _coverage(grid):
    tot = sum(len(r) for r in grid)
    ok = sum(1 for r in grid for v in r if v is not None)
    return ok / float(tot) if tot else 0.0


DEM_GRADE = ("ga_lidar_5m", "vicmap_10m")   # bare-earth DEMs in AHD; the others are surface models


def elevation_grid(lon_min, lat_min, lon_max, lat_max, nx, ny, info=None,
                   sources=("ga_lidar_5m", "vicmap_10m", "terrarium", "open_meteo"),
                   primary_min_coverage=0.9, fill_value=None):
    """Ground elevation grid (metres AHD) with a fallback chain.

    Returns (grid, source_name, resolution_m). grid[j][i]: row 0 = lat_min (south), col 0 = lon_min (west).
    None = no ground data (sea / bay / outside Victoria) unless fill_value (e.g. 0.0) is given.

    Policy: the bare-earth DEMs (GA LiDAR 5 m, Vicmap 10 m) are tried in order; the first covering
    >= primary_min_coverage of the nodes is the primary (else the one with the best coverage), and the
    other DEM fills its gaps after removing the median offset measured on overlapping nodes. The global
    surface models (Terrarium, Open-Meteo) are only used when no bare-earth DEM answered at all (service
    outage, site outside Victoria): in built-up areas they read 2-17 m high (rooftops/canopy).
    Pass a dict as `info` to receive per-source timings/coverage.
    """
    lons, lats = _nodes(lon_min, lat_min, lon_max, lat_max, nx, ny)
    if info is None:
        info = {}
    info["attempts"] = []
    table = dict((k, (fn, name, r)) for k, fn, name, r in ELEVATION_SOURCES)

    def run(key):
        fn = table[key][0]
        t0 = time.time()
        try:
            g = fn(lons, lats)
            cov, err = _coverage(g), None
        except Exception as e:
            g, cov, err = None, 0.0, "%s: %s" % (type(e).__name__, e)
        info["attempts"].append({"source": key, "seconds": round(time.time() - t0, 2),
                                 "coverage": round(cov, 4), "error": err})
        return g, cov

    def fill(result, g, name, names):
        diffs = sorted(result[j][i] - g[j][i] for j in range(ny) for i in range(nx)
                       if result[j][i] is not None and g[j][i] is not None)
        bias = diffs[len(diffs) // 2] if diffs else 0.0
        n = 0
        for j in range(ny):
            for i in range(nx):
                if result[j][i] is None and g[j][i] is not None:
                    result[j][i] = g[j][i] + bias
                    n += 1
        if n:
            names.append("%s (fill %d cells, bias %+.2f m)" % (name, n, bias))

    result, names, res = None, [], None
    for group in ([k for k in sources if k in DEM_GRADE], [k for k in sources if k not in DEM_GRADE]):
        got = []
        for key in group:
            g, cov = run(key)
            if g is not None and cov > 0:
                got.append((key, g, cov))
                if cov >= primary_min_coverage:
                    break
        if not got:
            continue
        prim = next((x for x in got if x[2] >= primary_min_coverage), None) or max(got, key=lambda x: x[2])
        result, names, res = prim[1], [table[prim[0]][1]], table[prim[0]][2]
        for key, g, cov in got:
            if key != prim[0] and _coverage(result) < 1.0:
                fill(result, g, table[key][1], names)
        # a partial primary may still have gaps another not-yet-fetched DEM could fill
        for key in group:
            if _coverage(result) >= 1.0:
                break
            if key not in [x[0] for x in got] and not any(a["source"] == key for a in info["attempts"]):
                g, cov = run(key)
                if g is not None and cov > 0:
                    fill(result, g, table[key][1], names)
        break
    if result is None:
        return [[fill_value] * nx for _ in range(ny)], "none", None
    info["coverage"] = _coverage(result)
    if fill_value is not None:
        result = [[fill_value if v is None else v for v in row] for row in result]
    return result, " + ".join(names), res


# =========================================================================================
# 2. TREES
# =========================================================================================
TREE_PROPS = ["ufi", "canopy_x_m", "canopy_y_m", "canopy_radius_m", "height_m", "dense_canopy",
              "source_end_date", "height_end_date", "geom"]


def _trees_featureserver(bbox):
    url = VAAS_URL + "/Vicmap_Vegetation_Tree_Urban/FeatureServer/0/query"
    out, offset = [], 0
    while True:
        d = http_json(url, {
            "geometry": "%.7f,%.7f,%.7f,%.7f" % bbox, "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "outFields": "ufi,canopy_x_m,canopy_y_m,canopy_radius_m,height_m,dense_canopy,source_end_date,height_end_date",
            "resultOffset": offset, "resultRecordCount": 2000, "f": "json"}, timeout=40)
        for f in d.get("features", []):
            a = dict(f["attributes"])
            out.append({"type": "Feature", "properties": a,
                        "geometry": {"type": "Point", "coordinates": [f["geometry"]["x"], f["geometry"]["y"]]}})
        if not d.get("exceededTransferLimit"):
            return out
        offset += len(d.get("features", []))


def _boxes_intersect(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _com_trees(lon_min, lat_min, lon_max, lat_max):
    where = "in_bbox(geolocation, %.7f, %.7f, %.7f, %.7f)" % (lat_min, lon_min, lat_max, lon_max)
    return http_json(COM_TREES_URL, {"where": where, "select": "com_id,common_name,scientific_name,"
                     "diameter_breast_height,age_description,located_in,latitude,longitude"}, timeout=30)


def trees(lon_min, lat_min, lon_max, lat_max, species=True, info=None, match_radius_m=4.0):
    """Individual urban trees (points) with height and canopy size.

    Vicmap Vegetation Tree Urban: tree centre points detected by machine learning on ~2019 aerial imagery,
    heights from a 2017-18 LiDAR canopy height model. canopy_x_m / canopy_y_m are the E-W / N-S canopy
    half-widths (canopy_radius_m ~ their mean). Coverage: metropolitan Melbourne plus urban Ballarat,
    Shepparton, Wangaratta and Sale only.

    height_m is height above ground from the canopy height model: trees on roofs/podiums report roof
    height (60 m "trees" in the CBD) -> run flag_rooftop_trees() with building footprints. Trees planted
    after the LiDAR flight report ~0 m -> use height_est_m, which substitutes ~2.5 x canopy radius.
    """
    if info is None:
        info = {}
    bbox = (lon_min, lat_min, lon_max, lat_max)
    want_com = species and any(_boxes_intersect(bbox, b) for b in COM_BOXES)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_com = ex.submit(_com_trees, *bbox) if want_com else None
        try:
            feats = wfs_features("tree_urban", bbox, props=TREE_PROPS)
            info["tree_source"] = "WFS open-data-platform:tree_urban"
        except Exception as e:
            info["wfs_error"] = repr(e)
            feats = _trees_featureserver(bbox)
            info["tree_source"] = "FeatureServer Vicmap_Vegetation_Tree_Urban/0"
        info["tree_seconds"] = round(time.time() - t0, 2)
        com = None
        if f_com is not None:
            try:
                com = f_com.result()
            except Exception as e:
                com = []
                info["com_error"] = repr(e)
            info["com_seconds"] = round(time.time() - t0, 2)
    out = []
    for f in feats:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        h = float(p["height_m"]) if p.get("height_m") is not None else None
        r = float(p["canopy_radius_m"]) if p.get("canopy_radius_m") is not None else None
        low = h is None or h < 1.5
        est = h
        if low:
            est = max(3.0, min(12.0, 2.5 * r)) if r else 5.0
        out.append({
            "lon": lon, "lat": lat,
            "height_m": h,
            "height_est_m": round(est, 2) if est is not None else None,
            "canopy_radius_m": r,
            "canopy_rx_m": float(p["canopy_x_m"]) if p.get("canopy_x_m") is not None else None,
            "canopy_ry_m": float(p["canopy_y_m"]) if p.get("canopy_y_m") is not None else None,
            "dense_canopy": p.get("dense_canopy") == "Y",
            # > 45 m is beyond Melbourne street/park trees (Dandenongs mountain ash reach ~40 m legitimately)
            "suspect": low or h > 45.0,
            "on_roof": None,
            "ufi": p.get("ufi"),
            "canopy_date": p.get("source_end_date"),
            "height_date": p.get("height_end_date"),
            "source": "Vicmap Vegetation Tree Urban",
        })
    # --- species enrichment inside the City of Melbourne
    if com is not None:
        info["com_trees"] = len(com)
        fr = LocalFrame((lon_min + lon_max) / 2, (lat_min + lat_max) / 2)
        cell = match_radius_m
        buckets = {}
        for c in com:
            if c.get("latitude") is None:
                continue
            x, y = fr.fwd(c["longitude"], c["latitude"])
            buckets.setdefault((int(x // cell), int(y // cell)), []).append((x, y, c))
        matched = 0
        for t in out:
            x, y = fr.fwd(t["lon"], t["lat"])
            best, bd = None, match_radius_m
            gx, gy = int(x // cell), int(y // cell)
            for ddx in (-1, 0, 1):
                for ddy in (-1, 0, 1):
                    for cx, cy, c in buckets.get((gx + ddx, gy + ddy), ()):
                        d = math.hypot(cx - x, cy - y)
                        if d < bd:
                            best, bd = c, d
            if best is not None:
                matched += 1
                t["species"] = best.get("scientific_name")
                t["common_name"] = best.get("common_name")
                t["dbh_cm"] = best.get("diameter_breast_height")
                t["located_in"] = best.get("located_in")
        info["species_matched"] = matched
    return out


def flag_rooftop_trees(tree_list, footprints, building_heights=None):
    """Mark trees whose point falls inside a building footprint (lon/lat rings, e.g. Overture).
    Sets t['on_roof']=True and, when building_heights[k] is known, t['height_above_roof_m']."""
    boxes = []
    for k, ring in enumerate(footprints):
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        boxes.append((min(xs), min(ys), max(xs), max(ys), k))
    for t in tree_list:
        t["on_roof"] = False
        for x0, y0, x1, y1, k in boxes:
            if x0 <= t["lon"] <= x1 and y0 <= t["lat"] <= y1 and _pip(t["lon"], t["lat"], [footprints[k]]):
                t["on_roof"] = True
                if building_heights and building_heights[k] and t["height_m"] is not None:
                    t["height_above_roof_m"] = max(0.0, t["height_m"] - building_heights[k])
                break
    return tree_list


# =========================================================================================
# 3. ROADS
# =========================================================================================
# Vicmap Transport TR_ROAD.CLASS_CODE (VMREFTAB.TR_ROAD_CLASS; 5/6 renamed Access Major/Minor).
# Values: name, nominal two-way undivided carriageway (kerb to kerb, m), min, max,
#         typical verge per side (property line to kerb, m), minimum verge, typical footpath width.
# Sources: VPP Cl.56.06-7 Table C1; VPA Engineering Design & Construction Manual (2011) Tables 4-5;
#          IDM Table 2 (Latrobe supplement); Austroads lane widths 3.0-3.5 m, parking lane 2.1-2.6 m.
ROAD_CLASSES = {
    0: ("Freeway", 22.0, 14.0, 36.0, 3.0, 0.0, 0.0),
    1: ("Highway", 14.0, 7.0, 24.0, 5.0, 3.0, 2.5),
    2: ("Arterial", 14.0, 7.0, 24.0, 5.0, 3.0, 2.5),
    3: ("Sub-arterial", 11.6, 7.0, 20.0, 5.0, 2.5, 2.0),
    4: ("Collector", 11.0, 7.0, 16.0, 5.0, 2.5, 1.5),
    5: ("Local / access major", 7.3, 5.5, 13.0, 4.0, 1.5, 1.5),
    6: ("Access minor", 5.5, 3.0, 7.3, 2.0, 0.0, 0.0),
    7: ("Major track", 4.0, 3.0, 6.0, 0.0, 0.0, 0.0),
    8: ("Minor track", 3.0, 2.5, 4.0, 0.0, 0.0, 0.0),
    9: ("Trail / path", 2.5, 1.5, 4.0, 0.0, 0.0, 0.0),
    14: ("Ferry route", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
}
LANE_TYPES = {"LANE", "ALLEY", "WALK", "ARCADE", "MEWS", "PASSAGE", "WALKWAY", "RIGHT OF WAY", "ROW"}
KERB_HEIGHT_M = 0.15     # B2 barrier kerb (SM2 semi-mountable ~0.10-0.11 m)
KERB_CHANNEL_M = 0.45    # B2 kerb + 300 mm channel (typical IDM standard drawing)

ROAD_PROPS = ["ezi_road_name_label", "road_type", "class_code", "direction_code", "div_rd", "road_seal",
              "feature_type_code", "width_m", "route_no", "ufi", "geom"]


def _pip(x, y, rings):
    inside = False
    for ring in rings:
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def _ray_hits(px, py, dx, dy, rings, max_s):
    """Smallest positive distances along +d and -d to any ring edge."""
    best_p, best_n = max_s, max_s
    for ring in rings:
        n = len(ring)
        for i in range(n - 1):
            ax, ay = ring[i]
            bx, by = ring[i + 1]
            ex, ey = bx - ax, by - ay
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            # P + s*d = A + u*e
            s = ((ax - px) * ey - (ay - py) * ex) / den
            u = ((ax - px) * dy - (ay - py) * dx) / den
            if 0.0 <= u <= 1.0:
                if 1e-6 < s < best_p:
                    best_p = s
                elif -best_n < s < -1e-6:
                    best_n = -s
    return best_p, best_n


def _median(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def estimate_cross_section(class_code, reserve_m=None, div_rd=None, road_type=None, sealed=True,
                           paved_verges=False):
    """Rule set: carriageway / verge / footpath from road class, constrained by measured reserve width.
    paved_verges=True => footpath runs kerb to property line (central city / activity centres)."""
    name, cw_nom, cw_min, cw_max, v_typ, v_min, fp = ROAD_CLASSES.get(class_code, ROAD_CLASSES[5])
    lane_like = (road_type or "").upper() in LANE_TYPES
    res = {"class_name": name, "median_m": 0.0, "kerb": False, "nature_strip_m": 0.0}
    if class_code in (7, 8, 9, 14) or cw_nom == 0.0:
        # tracks/trails/paths: a single surface; any surrounding reserve is not a verge of this line
        cw = min(cw_nom, reserve_m) if reserve_m else cw_nom
        res.update(carriageway_m=cw, verge_m=0.0, footpath_m=0.0)
        return res
    # laneways: building line to building line, no kerb/footpath (inner-city bluestone lanes 3-6 m)
    if (reserve_m is not None and reserve_m <= 7.5) or (reserve_m is None and lane_like):
        cw = reserve_m if reserve_m is not None else 4.0
        res.update(carriageway_m=round(cw, 2), verge_m=0.0, footpath_m=0.0)
        return res
    dual = div_rd == "DD"
    if reserve_m is None:
        cw = cw_nom / 2.0 if dual else cw_nom
        paved = paved_verges or v_typ < 3.5
        res.update(carriageway_m=cw, verge_m=v_typ, footpath_m=v_typ if paved else fp, kerb=bool(sealed),
                   nature_strip_m=0.0 if paved else max(0.0, v_typ - fp))
        if div_rd == "DS":
            res["median_m"] = 2.0
        return res
    if dual:
        # one carriageway of a dual road; the reserve is shared with the opposite carriageway + median
        half = reserve_m / 2.0
        spare = half - cw_nom / 2.0 - v_typ - 1.0
        cw = cw_nom / 2.0 + 0.25 * spare
        cw = max(cw_min / 2.0, min(cw, cw_max / 2.0, half - v_min - 0.5))
        verge = min(v_typ, max(v_min, (half - cw) / 2.0))
        median = max(0.0, reserve_m - 2 * cw - 2 * verge)
    else:
        spare = reserve_m - cw_nom - 2 * v_typ
        cw = cw_nom + 0.5 * spare
        cw = max(cw_min, min(cw, cw_max, reserve_m - 2 * v_min))
        cw = min(cw, reserve_m)
        median = 0.0
        if div_rd == "DS":   # divided road drawn as one centreline: carve a median out of the verges
            median = max(1.2, min(8.0, 0.15 * reserve_m))
            if reserve_m - cw - median < 2 * v_min:
                median = max(0.0, reserve_m - cw - 2 * v_min)
        verge = (reserve_m - cw - median) / 2.0
    # < 3.5 m leaves no room for a tree nature strip (VPA EDCM Table 5) => paved kerb to property line
    footpath = verge if (paved_verges or verge < 3.5) else min(verge, max(fp, 1.5))
    res.update(carriageway_m=round(cw, 2), verge_m=round(verge, 2), footpath_m=round(footpath, 2),
               nature_strip_m=round(max(0.0, verge - footpath), 2), median_m=round(median, 2),
               kerb=bool(sealed))
    return res


def road_profile(lon_min, lat_min, lon_max, lat_max, info=None, margin_m=40.0, station_m=4.0,
                 end_skip_m=8.0, max_half_width_m=45.0, paved_verges=None):
    """Per-centreline class/name and estimated cross-section, constrained by the measured road reserve.

    Reserve width is measured at stations every `station_m` along each Vicmap Transport centreline by
    casting perpendicular rays to the boundary of the Vicmap Property road casement polygon that contains
    the station (stations within `end_skip_m` of segment ends and inside intersection casements are skipped).
    paved_verges: True/False, or None = auto (True only inside CENTRAL_CITY_BOX; pass the planning-zone
    answer from the node instead when available).
    """
    if info is None:
        info = {}
    if paved_verges is None:
        cx, cy = (lon_min + lon_max) / 2.0, (lat_min + lat_max) / 2.0
        b = CENTRAL_CITY_BOX
        paved_verges = b[0] <= cx <= b[2] and b[1] <= cy <= b[3]
    info["paved_verges"] = paved_verges
    fr = LocalFrame((lon_min + lon_max) / 2.0, (lat_min + lat_max) / 2.0)
    mlon = margin_m / fr.kx
    mlat = margin_m / fr.ky
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_roads = ex.submit(wfs_features, "tr_road", (lon_min, lat_min, lon_max, lat_max), ROAD_PROPS)
        f_res = ex.submit(wfs_features, "road_casement_polygon",
                          (lon_min - mlon, lat_min - mlat, lon_max + mlon, lat_max + mlat),
                          ["ufi", "intersection", "z_level", "geom"])
        roads_f, res_f = f_roads.result(), f_res.result()
    info["fetch_seconds"] = round(time.time() - t0, 2)
    info["centrelines"], info["reserve_polygons"] = len(roads_f), len(res_f)

    # reserve polygons in local metres with bboxes
    polys = []
    for f in res_f:
        g = f["geometry"]
        parts = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for part in parts:
            rings = [[fr.fwd(x, y) for x, y in ring[:]] for ring in part]
            xs = [p[0] for r in rings for p in r]
            ys = [p[1] for r in rings for p in r]
            polys.append((min(xs), min(ys), max(xs), max(ys), rings, f["properties"].get("intersection") == "Y"))

    def containing(px, py):
        hit = None
        for x0, y0, x1, y1, rings, inter in polys:
            if x0 <= px <= x1 and y0 <= py <= y1 and _pip(px, py, rings):
                if not inter:
                    return rings, False
                hit = (rings, True)
        return hit

    t1 = time.time()
    out = []
    for f in roads_f:
        p = f["properties"]
        g = f["geometry"]
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        widths, offsets = [], []
        length = 0.0
        for line in lines:
            pts = [fr.fwd(x, y) for x, y in line]
            seglens = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)]
            L = sum(seglens)
            length += L
            if L < 1.0:
                continue
            skip = min(end_skip_m, 0.3 * L)
            s = skip
            stations = []
            while s <= L - skip + 1e-6:
                stations.append(s)
                s += station_m
            if not stations:
                stations = [L / 2.0]
            acc, k = 0.0, 0
            for st in stations:
                while k < len(seglens) - 1 and acc + seglens[k] < st:
                    acc += seglens[k]
                    k += 1
                if seglens[k] <= 0:
                    continue
                tt = (st - acc) / seglens[k]
                ax, ay = pts[k]
                bx, by = pts[k + 1]
                px, py = ax + (bx - ax) * tt, ay + (by - ay) * tt
                ux, uy = (bx - ax) / seglens[k], (by - ay) / seglens[k]
                c = containing(px, py)
                if c is None or c[1]:
                    continue
                left, right = _ray_hits(px, py, -uy, ux, c[0], max_half_width_m)
                if left >= max_half_width_m or right >= max_half_width_m:
                    continue
                widths.append(left + right)
                offsets.append((left - right) / 2.0)
        reserve = _median(widths) if len(widths) >= 2 or (widths and length < 3 * station_m) else None
        cls = p.get("class_code")
        cls = int(cls) if cls is not None else 5
        sec = estimate_cross_section(cls, reserve, p.get("div_rd"), p.get("road_type"),
                                     sealed=(str(p.get("road_seal")) == "1"), paved_verges=paved_verges)
        rec = {
            "name": p.get("ezi_road_name_label"),
            "class_code": cls,
            "direction": p.get("direction_code"),   # B two-way, F one-way in digitised direction
            "div_rd": p.get("div_rd"),             # ND not divided, DS divided/single line, DD dual lines, U unknown
            "sealed": str(p.get("road_seal")) == "1",
            "feature_type": p.get("feature_type_code"),
            "length_m": round(length, 1),
            "reserve_width_m": round(reserve, 2) if reserve is not None else None,
            "reserve_samples": len(widths),
            "reserve_iqr_m": None,
            "centre_offset_m": round(_median(offsets), 2) if offsets else None,  # + = reserve centre left of line
            "width_source": "measured reserve + class rules" if reserve is not None else "class default",
            "coords": lines[0] if len(lines) == 1 else lines,
            "ufi": p.get("ufi"),
        }
        if len(widths) >= 4:
            w = sorted(widths)
            rec["reserve_iqr_m"] = round(w[3 * len(w) // 4] - w[len(w) // 4], 2)
        rec.update(sec)
        out.append(rec)
    info["measure_seconds"] = round(time.time() - t1, 2)
    info["measured"] = sum(1 for r in out if r["reserve_width_m"] is not None)
    info["reserves"] = [[[list(fr.inv(x, y)) for x, y in ring] for ring in rings] for _, _, _, _, rings, _ in polys]
    return out


def streets(lon_min, lat_min, lon_max, lat_max, paved_verges=None, info=None):
    """Road centrelines with cross-sections, and the road reserve polygons (lon/lat rings) they were fitted to."""
    info = {} if info is None else info
    roads = road_profile(lon_min, lat_min, lon_max, lat_max, info=info, paved_verges=paved_verges)
    return {"roads": roads, "reserves": info.pop("reserves", [])}


