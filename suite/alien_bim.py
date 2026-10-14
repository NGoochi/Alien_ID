"""
Alien BIM helpers: the shared data model and geometry for the building suite (bim_site, levels, grids, wall_types,
walls, doors, windows, curtain_wall, beams_columns, structure_edit, connectors). Keep this file next to those nodes.

Records. Building elements travel between nodes as JSON strings, one per element: {"alien": "<kind>", ...}.
Grasshopper passes strings through unchanged, while Python objects arrive as their repr. pack() makes a record;
read() takes whatever arrives on an input (a record string, a list of them, a dict, a .NET dictionary, None) and
returns a list of dicts. Records speak metres, in world coordinates; geometry on pins is in model units, so convert
with units() (model units per metre), pt() and P().

Record kinds (fields beyond "alien"; lengths in metres, angles in degrees):
  site       name, origin [x,y,z], rotation (project grid, CCW from world X), north (true north, CCW from world +Y),
             datum (AHD of model z=0), boundary (curve record or null)
  level      name, code, index, elevation (model z), height (floor to floor), rl (elevation + datum)
  grid       name, group ("letters" | "numbers" | other), index, start [x,y,z], end [x,y,z]
  wall_type  name, layers [{name, material, thickness, function}], thickness
  wall       id, type (wall_type record), path (curve record, at the wall's base), base, height, location
  opening    id, kind ("door" | "window"), wall (wall id), at (m along the wall path), width, height, sill, family
  member     id, role ("column" | "beam" | "brace"), start, end, section {shape, name, d, b, t, tf, tw}, rotation,
             material, level, grid
Nodes may add fields; readers ignore what they don't know.
"""
import json
import math
import re

import Rhino
import Rhino.Geometry as rg
import System.Drawing


# ── units ────────────────────────────────────────────────────

def doc():
    return Rhino.RhinoDoc.ActiveDoc


def units():
    """Model units per metre (1000 in a millimetre model)."""
    d = doc()
    return Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, d.ModelUnitSystem) if d else 1.0


def tol():
    d = doc()
    return d.ModelAbsoluteTolerance if d else 0.001


def pt(p, u=None):
    """Point3d in model units -> [x, y, z] in metres."""
    u = u or units()
    return [round(p.X / u, 6), round(p.Y / u, 6), round(p.Z / u, 6)]


def P(a, u=None):
    """[x, y, z] in metres -> Point3d in model units."""
    u = u or units()
    a = list(a) + [0.0] * (3 - len(a))
    return rg.Point3d(a[0] * u, a[1] * u, a[2] * u)


# ── records ──────────────────────────────────────────────────

def _clean(v):
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def pack(kind, **fields):
    """One record as a JSON string."""
    rec = {"alien": kind}
    rec.update(fields)
    return json.dumps(_clean(rec), separators=(",", ":"))


def dump(rec):
    """A record dict back to its JSON string."""
    return json.dumps(_clean(rec), separators=(",", ":"))


def _from_net(v):
    """A .NET dictionary or list (what a dict output can turn into) -> Python."""
    try:
        import System.Collections
        if isinstance(v, System.Collections.IDictionary):
            return {str(k): _from_net(v[k]) for k in v.Keys}
        if isinstance(v, System.Collections.IEnumerable) and not isinstance(v, str):
            return [_from_net(x) for x in v]
    except Exception:
        pass
    return v


def read(value, kind=None):
    """Every record in an input, as dicts, optionally only of one kind (a string or a tuple of kinds)."""
    out = []

    def take(v):
        if v is None:
            return
        if isinstance(v, str):
            s = v.strip()
            if not s or s[0] not in "[{":
                return
            try:
                take(json.loads(s))
            except ValueError:
                pass
            return
        if isinstance(v, dict):
            if "alien" in v:
                out.append(v)
            return
        if isinstance(v, (list, tuple)):
            for x in v:
                take(x)
            return
        conv = _from_net(v)
        if conv is not v:
            take(conv)
        else:
            take(str(v))

    take(value)
    if kind:
        kinds = (kind,) if isinstance(kind, str) else tuple(kind)
        out = [r for r in out if r.get("alien") in kinds]
    return out


def first(value, kind):
    recs = read(value, kind)
    return recs[0] if recs else None


# ── curves in records ────────────────────────────────────────

def curve_record(curve, u=None):
    """A curve (model units) -> {"points": [...], "closed": bool} for polylines, else {"nurbs": <Rhino JSON>} in metres."""
    u = u or units()
    ok, pl = curve.TryGetPolyline()
    if ok:
        return {"points": [pt(p, u) for p in pl], "closed": curve.IsClosed}
    c = curve.DuplicateCurve()
    c.Transform(rg.Transform.Scale(rg.Point3d.Origin, 1.0 / u))
    return {"nurbs": c.ToNurbsCurve().ToJSON(None), "closed": curve.IsClosed}


def curve_from(rec, u=None):
    """A curve record -> Curve in model units (None if it can't be read)."""
    if not rec:
        return None
    u = u or units()
    if rec.get("points"):
        pts = [P(p, u) for p in rec["points"]]
        return rg.PolylineCurve(pts) if len(pts) > 2 else rg.LineCurve(pts[0], pts[-1]) if len(pts) == 2 else None
    if rec.get("nurbs"):
        c = rg.GeometryBase.FromJSON(rec["nurbs"])
        if isinstance(c, rg.Curve):
            c.Transform(rg.Transform.Scale(rg.Point3d.Origin, u))
            return c
    return None


# ── site ─────────────────────────────────────────────────────

def site_of(value):
    """The site record on an input, or the default site (world origin, no rotation, datum 0)."""
    s = first(value, "site")
    base = {"alien": "site", "name": "Site", "origin": [0.0, 0.0, 0.0], "rotation": 0.0, "north": 0.0, "datum": 0.0, "boundary": None}
    if s:
        base.update({k: v for k, v in s.items() if v is not None})
    return base


def site_plane(site, u=None):
    """The project plane (model units): at the site origin, X along the project grid's rotation."""
    u = u or units()
    s = site if isinstance(site, dict) else site_of(site)
    a = math.radians(float(s.get("rotation") or 0.0))
    return rg.Plane(P(s.get("origin") or [0, 0, 0], u), rg.Vector3d(math.cos(a), math.sin(a), 0), rg.Vector3d(-math.sin(a), math.cos(a), 0))


# ── levels and grids ─────────────────────────────────────────

def levels_of(value, sample=True):
    """Level records sorted by elevation; with sample, three storeys and a roof when none are wired."""
    lv = sorted(read(value, "level"), key=lambda r: float(r.get("elevation", 0)))
    if lv or not sample:
        return lv
    return sample_levels()


def sample_levels(count=3, height=3.2):
    out = []
    for i in range(count + 1):
        name = "Ground" if i == 0 else "Roof" if i == count else "Level %d" % i
        code = "GF" if i == 0 else "RF" if i == count else "L%02d" % i
        out.append({"alien": "level", "name": name, "code": code, "index": i, "elevation": i * height, "height": height, "rl": i * height})
    return out


def level_at(levels, ref):
    """A level by name, code, index (int or digits) or nearest elevation (float); None if there are none."""
    if not levels:
        return None
    if ref is None or ref == "":
        return levels[0]
    if isinstance(ref, bool):
        return levels[0]
    if isinstance(ref, int):  # an index; negative counts from the top
        return levels[max(-len(levels), min(len(levels) - 1, ref))]
    if isinstance(ref, float):  # an elevation (m)
        return min(levels, key=lambda r: abs(float(r["elevation"]) - ref))
    s = str(ref).strip()
    for r in levels:
        if s.lower() in (str(r.get("name", "")).lower(), str(r.get("code", "")).lower()):
            return r
    if re.fullmatch(r"-?\d+", s):
        i = int(s)
        return levels[i if i >= 0 else len(levels) + i] if -len(levels) <= i < len(levels) else None
    try:
        return level_at(levels, float(s))
    except ValueError:
        return None


def level_above(levels, level):
    """The next level up, or None at the top."""
    above = [r for r in levels if float(r["elevation"]) > float(level["elevation"]) + 1e-6]
    return above[0] if above else None


def level_top(levels, level):
    """Elevation of the top of a storey: the next level, else elevation + height."""
    nxt = level_above(levels, level)
    h = level.get("height")
    return float(nxt["elevation"]) if nxt else float(level["elevation"]) + (3.0 if h is None else float(h))


def grids_of(value, sample=True):
    g = read(value, "grid")
    if g or not sample:
        return g
    return sample_grids()


def sample_grids(x_bays=(7.2, 7.2, 7.2), y_bays=(6.0, 6.0), ext=1.5):
    """Numbered grids (1, 2, ...) spaced along X, lettered grids (A, B, ...) along Y, from the world origin."""
    xs = [0.0]
    for b in x_bays:
        xs.append(xs[-1] + b)
    ys = [0.0]
    for b in y_bays:
        ys.append(ys[-1] + b)
    out = []
    for i, x in enumerate(xs):
        out.append({"alien": "grid", "name": str(i + 1), "group": "numbers", "index": i, "start": [x, -ext, 0.0], "end": [x, ys[-1] + ext, 0.0]})
    for i, y in enumerate(ys):
        out.append({"alien": "grid", "name": "ABCDEFGHJKLMNPQRSTUVWXYZ"[i], "group": "letters", "index": i, "start": [-ext, y, 0.0], "end": [xs[-1] + ext, y, 0.0]})
    return out


def grid_line(g, u=None):
    return rg.Line(P(g["start"], u), P(g["end"], u))


def intersections(grids, u=None):
    """[(name, Point3d model units, grid a, grid b)] for every crossing of grids from different groups, letters first
    in the name ("A/1"), ordered by letters then numbers."""
    u = u or units()
    out = []
    for i, a in enumerate(grids):
        for b in grids[i + 1:]:
            if a.get("group") == b.get("group"):
                continue
            la, lb = grid_line(a, u), grid_line(b, u)
            ok, ta, tb = rg.Intersect.Intersection.LineLine(la, lb, tol(), True)
            if not ok:
                continue
            p = la.PointAt(ta)
            first_, second = (a, b) if a.get("group") == "letters" or (b.get("group") != "letters" and str(a["name"]) < str(b["name"])) else (b, a)
            out.append(("%s/%s" % (first_["name"], second["name"]), p, first_, second))
    out.sort(key=lambda r: (int(r[2].get("index", 0)), int(r[3].get("index", 0))))
    return out


# ── annotation ───────────────────────────────────────────────

def text_curves(text, plane, height, h_align="center", v_align="middle"):
    """Outline curves of text on a plane, `height` in model units. h_align: left|center|right; v_align: top|middle|bottom."""
    text = str(text)
    if not text.strip() or height <= 0:
        return []
    ds = Rhino.DocObjects.DimensionStyle()
    d = doc()
    if d is not None:
        try:
            ds = d.DimStyles.Current.Duplicate()
        except Exception:
            pass
    ds.TextHeight = 1.0
    te = rg.TextEntity.Create(text, rg.Plane.WorldXY, ds, False, 0, 0)
    if te is None:
        return []
    curves = [c for c in (te.CreateCurves(ds, False, 1.0, 0) or []) if c is not None]
    if not curves:
        return []
    bb = rg.BoundingBox.Empty
    for c in curves:
        bb.Union(c.GetBoundingBox(True))
    ax = {"left": bb.Min.X, "right": bb.Max.X}.get(h_align, (bb.Min.X + bb.Max.X) / 2)
    ay = {"bottom": bb.Min.Y, "top": bb.Max.Y}.get(v_align, (bb.Min.Y + bb.Max.Y) / 2)
    xf = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, plane) * rg.Transform.Scale(rg.Point3d.Origin, height) * rg.Transform.Translation(-ax, -ay, 0)
    for c in curves:
        c.Transform(xf)
    return curves


def bubble(plane, radius, text, text_height=None):
    """A grid/level head: a circle on the plane with the text centred in it (curves, model units)."""
    circle = rg.Circle(plane, radius).ToNurbsCurve()
    return [circle] + text_curves(text, plane, text_height or radius * 0.9)


# ── materials ────────────────────────────────────────────────

MATERIALS = {
    "concrete": (170, 170, 165), "masonry": (150, 95, 75), "brick": (160, 82, 60), "block": (185, 180, 170),
    "steel": (95, 105, 120), "aluminium": (175, 180, 188), "timber": (196, 154, 98), "clt": (210, 172, 118),
    "plasterboard": (236, 233, 226), "render": (225, 218, 205), "insulation": (238, 210, 90), "glass": (150, 200, 225),
    "membrane": (60, 70, 90), "air": (210, 225, 235), "cladding": (120, 125, 130), "stone": (200, 192, 176),
    "tile": (190, 120, 90), "paint": (240, 240, 236), "grout": (140, 140, 140),
}


def colour(material, alpha=255):
    """Preview colour for a material name (matched by keyword; grey when unknown)."""
    m = str(material or "").lower()
    for key, rgb in MATERIALS.items():
        if key in m:
            return System.Drawing.Color.FromArgb(alpha, *rgb)
    return System.Drawing.Color.FromArgb(alpha, 160, 160, 160)


# ── sections and members ─────────────────────────────────────

def _poly(pts):
    pts = [rg.Point3d(x, y, 0) for x, y in pts]
    return rg.PolylineCurve(pts + [pts[0]])


def section(spec, u=None):
    """Profile curves of a section in world XY, centred on the origin, model units: [outer, inner...].
    spec (metres): {"shape": rect|box|circle|tube|I|T|C|L, "d": depth (local Y, "up"), "b": width (local X),
    "t": wall (box/tube), "tf": flange, "tw": web}. Missing sizes fall back to sensible proportions."""
    u = u or units()
    shape = str(spec.get("shape", "rect")).lower()
    d = float(spec.get("d") or spec.get("b") or 0.3) * u
    b = float(spec.get("b") or spec.get("d") or 0.15) * u
    t = float(spec.get("t") or 0) * u or max(min(b, d) * 0.06, 0.002 * u)
    tf = float(spec.get("tf") or 0) * u or max(d * 0.05, 0.004 * u)
    tw = float(spec.get("tw") or 0) * u or max(b * 0.05, 0.003 * u)
    hb, hd = b / 2, d / 2
    if shape in ("circle", "round", "rod"):
        return [rg.Circle(rg.Plane.WorldXY, d / 2).ToNurbsCurve()]
    if shape in ("tube", "chs", "pipe"):
        r = d / 2
        return [rg.Circle(rg.Plane.WorldXY, r).ToNurbsCurve(), rg.Circle(rg.Plane.WorldXY, max(r - t, r * 0.2)).ToNurbsCurve()]
    if shape in ("box", "shs", "rhs", "hollow"):
        t = min(t, hb * 0.8, hd * 0.8)
        return [_poly([(-hb, -hd), (hb, -hd), (hb, hd), (-hb, hd)]), _poly([(-hb + t, -hd + t), (hb - t, -hd + t), (hb - t, hd - t), (-hb + t, hd - t)])]
    if shape in ("i", "ub", "uc", "h", "wb"):
        w = tw / 2
        return [_poly([(-hb, -hd), (hb, -hd), (hb, -hd + tf), (w, -hd + tf), (w, hd - tf), (hb, hd - tf), (hb, hd), (-hb, hd),
                       (-hb, hd - tf), (-w, hd - tf), (-w, -hd + tf), (-hb, -hd + tf)])]
    if shape in ("t", "tee"):
        w = tw / 2
        return [_poly([(-hb, hd), (-hb, hd - tf), (-w, hd - tf), (-w, -hd), (w, -hd), (w, hd - tf), (hb, hd - tf), (hb, hd)])]
    if shape in ("c", "pfc", "channel"):
        return [_poly([(-hb, -hd), (hb, -hd), (hb, -hd + tf), (-hb + tw, -hd + tf), (-hb + tw, hd - tf), (hb, hd - tf), (hb, hd), (-hb, hd)])]
    if shape in ("l", "angle", "ea", "ua"):
        return [_poly([(-hb, -hd), (hb, -hd), (hb, -hd + t), (-hb + t, -hd + t), (-hb + t, hd), (-hb, hd)])]
    return [_poly([(-hb, -hd), (hb, -hd), (hb, hd), (-hb, hd)])]


def member(start, end, spec, rotation=0.0, up=None):
    """A straight member solid (Brep, model units) from start to end (Point3d, model units) with a section spec
    (metres). The section's depth (local Y) points `up` (default world Z; for vertical members, world Y), turned by
    `rotation` degrees about the member axis. None when start and end coincide."""
    axis = end - start
    if axis.Length < tol():
        return None
    curves = section(spec)
    if rotation:
        r = rg.Transform.Rotation(math.radians(rotation), rg.Point3d.Origin)
        for c in curves:
            c.Transform(r)
    if up is None:
        a = rg.Vector3d(axis)
        a.Unitize()
        up = rg.Vector3d.YAxis if abs(a.Z) > 0.999 else rg.Vector3d.ZAxis
    ext = rg.Extrusion()
    if not ext.SetOuterProfile(curves[0], True):
        return None
    for inner in curves[1:]:
        ext.AddInnerProfile(inner)
    if not ext.SetPathAndUp(start, end, up):
        return None
    return ext.ToBrep()


def box(plane, x0, x1, y0, y1, z0, z1):
    """A box Brep on a plane (model units); None if it has no volume."""
    if x1 - x0 <= 0 or y1 - y0 <= 0 or z1 - z0 <= 0:
        return None
    return rg.Box(plane, rg.Interval(x0, x1), rg.Interval(y0, y1), rg.Interval(z0, z1)).ToBrep()


# ── outputs ──────────────────────────────────────────────────

def flat(items):
    out = []
    for x in items or []:
        if isinstance(x, (list, tuple)):
            out.extend(flat(x))
        elif x is not None:
            out.append(x)
    return out
