"""
Alien walls helpers: wall types (layered build-ups), wall solids rebuilt from wall records, and hosted openings
(doors, windows) cut through them, with the linework drawings need. Used by wall_types, walls, doors and windows;
keep it next to alien_bim.py.

Conventions (records are metres, geometry model units):
- A wall's `path` is its location line at the wall's base. Layers run exterior -> interior. The exterior is on the
  LEFT of the path's direction seen from above (as in Revit); a closed outline puts it outside whichever way it was
  drawn. The record's `side` says which: +1 left, -1 right (`flip` swaps it).
- Offsets run along the exterior normal from the path: the exterior face sits at `e`, layer boundary i at
  e - (thickness of the layers before it). `location` picks e: centre, exterior, interior or core centre.
- `caps` holds the direction of each end's cut line (null = square): a mitre where two walls meet in an L, the other
  wall's face where one butts into another (T). `walls` resolves them; everything downstream just rebuilds.
- Openings sit `at` metres along the path (their centre), `sill` above the wall's floor (its base level), with an
  overall `width` x `height`: the hole in the wall, which the frame fills.
"""
import copy
import json
import math
import re
from collections import OrderedDict

import Rhino.Geometry as rg
import System.Drawing

import alien_bim as bim

Z = rg.Vector3d.ZAxis

# ── wall types ───────────────────────────────────────────────

# Common Australian build-ups, exterior -> interior, thicknesses in mm; * marks the core (structure).
PRESETS = OrderedDict([
    ("brick veneer", "face brick 110; cavity 40; timber stud 90*; plasterboard 10"),
    ("double brick", "face brick 110; cavity 50; inner brick 110*; render 10"),
    ("timber stud", "weatherboard 20; timber stud 90*; plasterboard 10"),
    ("steel stud partition", "plasterboard 13; steel stud 92*; plasterboard 13"),
    ("concrete 200", "concrete 200*"),
    ("precast 150", "precast concrete 150*"),
    ("blockwork 190", "concrete block 190*"),
    ("CLT", "fibre cement cladding 9; cavity 25; insulation 50; CLT 120*"),
    ("rendered AAC", "render 6; AAC panel 75; cavity 35; timber stud 90*; plasterboard 10"),
])

# Layer name -> (material, function), first match wins (so "steel stud" is steel, "concrete block" is block).
_WORDS = [
    (r"\b(air|cavity|gap|void)\b", "air", "air"),
    (r"insulat|\bbatts?\b|rockwool|glasswool|\beps\b|\bxps\b|\bpir\b", "insulation", "thermal"),
    (r"membrane|\bwrap\b|sarking|vapou?r|barrier|waterproof", "membrane", "membrane"),
    (r"plasterboard|gyprock|drywall|gypsum|\bgib\b", "plasterboard", "finish"),
    (r"render|plaster|stucco", "render", "finish"),
    (r"\baac\b|hebel|autoclaved", "aac", "structure"),
    (r"\bclt\b|cross.?laminated|glulam|\blvl\b|mass timber", "clt", "structure"),
    (r"brick", "brick", "structure"),
    (r"block|\bcmu\b", "block", "structure"),
    (r"concrete|precast|tilt", "concrete", "structure"),
    (r"steel|metal stud", "steel", "structure"),
    (r"weatherboard|cladding|fibre cement|fiber cement|colorbond|sheet", "cladding", "finish"),
    (r"stud|timber|wood|frame|batten|ply", "timber", "structure"),
    (r"\btiles?\b", "tile", "finish"),
    (r"stone|sandstone|bluestone", "stone", "finish"),
    (r"glass", "glass", "finish"),
    (r"paint", "paint", "finish"),
]

_EXTRA = {"aac": (226, 222, 210), "generic": (205, 205, 200)}


def colour(material, alpha=None):
    """Preview colour of a layer material; air is see-through."""
    m = str(material or "").lower()
    a = alpha if alpha is not None else (70 if m == "air" else 255)
    for key, rgb in _EXTRA.items():
        if key in m:
            return System.Drawing.Color.FromArgb(a, *rgb)
    return bim.colour(m, a)


def material_of(name):
    s = str(name).lower()
    for pattern, material, function in _WORDS:
        if re.search(pattern, s):
            return material, function
    return "generic", "structure"


def parse_layers(text):
    """'brick 110; air 40; timber stud 90*; plasterboard 13' -> (layers, problems). Thickness in mm unless it says
    m or cm; * marks the core; ; , | or new lines separate layers."""
    layers, problems = [], []
    for tok in re.split(r"[;,|\n]+", str(text or "")):
        tok = tok.strip()
        if not tok:
            continue
        core = "*" in tok
        tok = tok.replace("*", " ").strip()
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(mm|cm|m)?\b", tok)
        if not m:
            problems.append("no thickness in '%s'" % tok)
            continue
        value, unit = float(m.group(1)), (m.group(2) or "mm")
        mm = value * {"mm": 1.0, "cm": 10.0, "m": 1000.0}[unit]
        name = (tok[:m.start()] + " " + tok[m.end():]).strip() or "layer"
        name = re.sub(r"\s+", " ", name)
        if mm <= 0 or mm > 2000:
            problems.append("'%s': %g mm is not a layer thickness (0-2000 mm)" % (tok, mm))
            continue
        material, function = material_of(name)
        layers.append({"name": name, "material": material, "thickness": round(mm / 1000.0, 6), "function": function, "core": core})
    if layers and not any(l["core"] for l in layers):
        structural = [l for l in layers if l["function"] == "structure"] or [l for l in layers if l["material"] != "air"] or layers
        max(structural, key=lambda l: l["thickness"])["core"] = True
    return layers, problems


def layers_text(layers):
    """Layers back to the text form (thicknesses in mm)."""
    return "; ".join("%s %g%s" % (l["name"], round(float(l["thickness"]) * 1000, 2), "*" if l.get("core") else "") for l in layers)


def make_type(name, layers, preset=None):
    return {"alien": "wall_type", "name": name, "preset": preset, "layers": layers,
            "thickness": round(sum(float(l["thickness"]) for l in layers), 6)}


def default_type():
    layers, _ = parse_layers("generic wall 200*")
    return make_type("Generic 200", layers, "generic")


def preset_type(preset):
    layers, _ = parse_layers(PRESETS[preset])
    return make_type(preset[:1].upper() + preset[1:], layers, preset)


def type_of(value):
    """The first wall_type record in a value (a wall record's `type` dict, a record string...), else generic 200."""
    if isinstance(value, dict) and value.get("alien") == "wall_type":
        t = value
    else:
        t = bim.first(value, "wall_type")
    if not t or not [l for l in t.get("layers") or [] if float(l.get("thickness") or 0) > 0]:
        return default_type()
    return t


def is_core(layer):
    return bool(layer.get("core")) or layer.get("function") == "core"


def exterior_offset(wtype, location):
    """Distance (m) from the location line to the exterior face, along the exterior normal."""
    layers = [l for l in wtype["layers"] if float(l.get("thickness") or 0) > 0]
    total = sum(float(l["thickness"]) for l in layers)
    loc = str(location or "centre").lower()
    if loc.startswith("ext"):
        return 0.0
    if loc.startswith("int"):
        return total
    if "core" in loc:
        d, span = 0.0, []
        for l in layers:
            if is_core(l):
                span += [d, d + float(l["thickness"])]
            d += float(l["thickness"])
        if span:
            return (min(span) + max(span)) / 2.0
    return total / 2.0


# ── small geometry ───────────────────────────────────────────

def _flat(v):
    v = rg.Vector3d(v.X, v.Y, 0)
    v.Unitize()
    return v


def _left(v):
    return rg.Vector3d(-v.Y, v.X, 0)


def _cross(a, b):
    return a.X * b.Y - a.Y * b.X


def _meet(p1, d1, p2, d2):
    """Where two plan lines p + t d cross (z of p1); None when (nearly) parallel."""
    den = _cross(d1, d2)
    if abs(den) < 1e-6:
        return None
    w = p2 - p1
    return p1 + d1 * (_cross(w, d2) / den)


def solid(b):
    """A closed Brep with outward normals (or None)."""
    if b is None:
        return None
    if isinstance(b, rg.Extrusion):
        b = b.ToBrep()
    if b.IsSolid and b.SolidOrientation == rg.BrepSolidOrientation.Inward:
        b.Flip()
    return b


def extrude_up(outline, height, tol):
    """A closed planar plan outline extruded straight up by height."""
    c = outline.DuplicateCurve()
    if c.ClosedCurveOrientation(Z) == rg.CurveOrientation.Clockwise:
        c.Reverse()
    e = rg.Extrusion.Create(c, height, True)
    if e is not None:
        return solid(e.ToBrep())
    pb = rg.Brep.CreatePlanarBreps(c, tol)
    if pb:
        return solid(pb[0].Faces[0].CreateExtrusion(rg.LineCurve(rg.Point3d.Origin, rg.Point3d(0, 0, height)), True))
    return None


def dashed(a, b, dash, gap):
    """A dashed line from a to b as short LineCurves (curves have no linetype in Grasshopper)."""
    v = b - a
    n = v.Length
    if n < 1e-9:
        return []
    v.Unitize()
    out, s = [], 0.0
    while s < n - 1e-9:
        e = min(n, s + dash)
        out.append(rg.LineCurve(a + v * s, a + v * e))
        s = e + gap
    return out


# ── walls ────────────────────────────────────────────────────

def make_wall(id_, wtype, path, base, height, location="centre", flip=False, floor=None, cut=1.0, level=None,
              top_level=None, caps=None, u=None):
    """A wall record from a plan curve (model units; flattened onto the base elevation)."""
    u = u or bim.units()
    c = path.DuplicateCurve()
    c.Transform(rg.Transform.PlanarProjection(rg.Plane.WorldXY))
    c.Translate(rg.Vector3d(0, 0, base * u))
    if c.IsClosed:
        side = -1 if c.ClosedCurveOrientation(Z) == rg.CurveOrientation.CounterClockwise else 1
    else:
        side = 1
    if flip:
        side = -side
    t = type_of(wtype)
    return {"alien": "wall", "id": id_, "type": t, "path": bim.curve_record(c, u), "base": base, "height": height,
            "floor": base if floor is None else floor, "cut": cut, "location": location, "flip": bool(flip), "side": side,
            "level": level, "top_level": top_level, "thickness": t["thickness"], "length": round(c.GetLength() / u, 6),
            "caps": caps or {"start": None, "end": None}, "openings": []}


def sample_wall():
    """A straight 6 m brick-veneer wall along world X, 3.2 m high (for doors/windows with nothing wired)."""
    lv = bim.sample_levels()
    u = bim.units()
    return make_wall("WL01", preset_type("brick veneer"), rg.LineCurve(rg.Point3d(0, 0, 0), rg.Point3d(6 * u, 0, 0)),
                     lv[0]["elevation"], lv[1]["elevation"] - lv[0]["elevation"], "centre", False,
                     level=lv[0]["name"], top_level=lv[1]["name"], u=u)


def _geom_key(rec):
    return json.dumps({k: v for k, v in rec.items() if k != "openings"}, sort_keys=True)


_WALLS = OrderedDict()   # geometry key -> Wall (built walls are reused between solves and between nodes)
_CUTS = OrderedDict()    # (geometry key, openings) -> cut layer solids


def _remember(cache, key, value, size):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > size:
        cache.popitem(last=False)


def wall_of(rec):
    """A built Wall for a record (cached by its geometry, so openings don't rebuild it)."""
    key = _geom_key(rec) + str(bim.units())
    w = _WALLS.get(key)
    if w is None:
        w = Wall(rec)
        _remember(_WALLS, key, w, 300)
    else:
        _WALLS.move_to_end(key)
    w.rec, w.id = rec, rec.get("id", "")
    return w


class Wall(object):
    """A wall record's geometry in model units: path, layer boundaries, solids, plan lines, opening stations."""

    def __init__(self, rec, u=None):
        self.rec = rec
        self.u = u = u or bim.units()
        self.tol = bim.tol()
        self.id = rec.get("id", "")
        self.type = type_of(rec.get("type"))
        self.layers = [l for l in self.type["layers"] if float(l.get("thickness") or 0) > 0]
        depths = [0.0]
        for l in self.layers:
            depths.append(depths[-1] + float(l["thickness"]) * u)
        self.depths = depths
        self.T = depths[-1]
        self.e = exterior_offset(self.type, rec.get("location")) * u
        self.side = -1 if float(rec.get("side", 1)) < 0 else 1
        self.base = float(rec.get("base") or 0) * u
        self.height = max(float(rec.get("height") or 0) * u, 0.0)
        self.floor = float(rec.get("floor", rec.get("base")) or 0) * u
        self.cut = float(rec.get("cut", 1.0)) * u
        path = bim.curve_from(rec.get("path"), u)
        if path is None:
            raise ValueError("wall %s has no readable path" % self.id)
        path.Transform(rg.Transform.PlanarProjection(rg.Plane.WorldXY))
        path.Translate(rg.Vector3d(0, 0, self.base))
        self.path = path
        self.closed = path.IsClosed
        self.length = path.GetLength()
        caps = rec.get("caps") or {}
        self.caps = tuple(_flat(rg.Vector3d(c[0], c[1], 0)) if c else None for c in (caps.get("start"), caps.get("end")))
        self.pts = self._points()
        # signed offsets of the layer boundaries to the LEFT of the path, exterior first
        self.offsets = [self.side * (self.e - d) for d in depths]
        self.centre_offset = self.side * (self.e - self.T / 2.0)
        self.reach = max(abs(o) for o in self.offsets)  # furthest the wall gets from its path
        self._bounds = {}
        self._solids = None

    # path

    def _points(self):
        ok, pl = self.path.TryGetPolyline()
        if not ok:
            return None
        pts = []
        for p in pl:
            if not pts or p.DistanceTo(pts[-1]) > self.tol:
                pts.append(rg.Point3d(p.X, p.Y, self.base))
        if self.closed and len(pts) > 2 and pts[0].DistanceTo(pts[-1]) <= self.tol * 10:
            pts.pop()
        return pts if len(pts) >= 2 else None

    def tangent(self, t):
        return _flat(self.path.TangentAt(t))

    def param(self, along):
        """Curve parameter at a distance (model units) along the path."""
        a = along % self.length if self.closed else max(0.0, min(self.length, along))
        ok, t = self.path.LengthParameter(a)
        return t if ok else self.path.Domain.Min

    def along(self, t):
        return self.path.GetLength(rg.Interval(self.path.Domain.Min, t)) if t > self.path.Domain.Min else 0.0

    # layer boundaries

    def boundary(self, o):
        """The line `o` (model units) to the left of the path, mitred at corners and cut to the end caps."""
        key = round(o, 6)
        if key in self._bounds:
            return self._bounds[key]
        c = None
        if self.pts:
            c = self._sound(self._offset_poly(self.pts, o), o)
        else:
            c = self._sound(self._offset_curve(o), o)
            if c is None:  # a curve Rhino can't offset cleanly: follow a fine polyline of it instead
                pl = self.path.ToPolyline(0.002 * self.u, math.radians(3), 0, 0)
                ok, poly = pl.TryGetPolyline() if pl else (False, None)
                if ok:
                    pts = list(poly)
                    if self.closed:
                        pts.pop()
                    c = self._sound(self._offset_poly(pts, o), o)
        self._bounds[key] = c
        return c

    def _sound(self, c, o):
        """None when an offset folded over itself (a bend too tight for the wall's thickness): its points must all
        stay |o| from the path, and it must not cross itself."""
        if c is None:
            return None
        if abs(o) > self.tol:
            if isinstance(c, rg.PolylineCurve):
                n = c.PointCount
                qs = [c.Point(i) for i in range(0, n, max(1, n // 16))]
            else:
                dom = c.Domain
                qs = [c.PointAt(dom.Min + (dom.Max - dom.Min) * k / 12.0) for k in range(13)]
            for q in qs:
                ok, t = self.path.ClosestPoint(q)
                if ok and q.DistanceTo(self.path.PointAt(t)) < 0.9 * abs(o) - 10 * self.tol:
                    return None
        x = rg.Intersect.Intersection.CurveSelf(c, self.tol)
        return None if x is not None and x.Count > 0 else c

    def _offset_poly(self, pts, o):
        n = len(pts)
        segs = n if self.closed else n - 1
        lines = []
        for i in range(segs):
            a, b = pts[i], pts[(i + 1) % n]
            d = _flat(b - a)
            nl = _left(d)
            lines.append((a + nl * o, d, nl))
        out = []

        def corner(i, prev, cur):
            p = _meet(prev[0], prev[1], cur[0], cur[1])
            return p if p is not None else pts[i] + cur[2] * o

        def cap(p, line, c):
            q = _meet(line[0], line[1], p, c) if c is not None else None
            return q if q is not None else p + line[2] * o

        if self.closed:
            for i in range(n):
                out.append(corner(i, lines[i - 1], lines[i]))
            out.append(out[0])
        else:
            out.append(cap(pts[0], lines[0], self.caps[0]))
            for i in range(1, n - 1):
                out.append(corner(i, lines[i - 1], lines[i]))
            out.append(cap(pts[-1], lines[-1], self.caps[1]))
        return rg.PolylineCurve(out)

    def _offset_curve(self, o):
        if abs(o) < self.tol:
            return self.path.DuplicateCurve()
        plane = rg.Plane(self.path.PointAtStart, Z)
        # Rhino offsets to the right for +; half a millimetre is plenty for a wall and much faster than model tolerance
        res = self.path.Offset(plane, -o, max(self.tol, 0.0005 * self.u), rg.CurveOffsetCornerStyle.Sharp)
        if not res:
            return None
        res = list(res)
        if len(res) > 1:
            joined = rg.Curve.JoinCurves(res, self.tol * 10)
            res = list(joined) if joined else res
        if len(res) != 1 or res[0].IsClosed != self.closed:
            return None
        c = res[0]
        if self.closed:
            if c.ClosedCurveOrientation(Z) != self.path.ClosedCurveOrientation(Z):
                c.Reverse()
        else:
            want = self.path.PointAtStart + _left(self.tangent(self.path.Domain.Min)) * o
            if c.PointAtEnd.DistanceTo(want) < c.PointAtStart.DistanceTo(want):
                c.Reverse()
            # a sound offset of an open curve keeps its ends square to the path
            if c.PointAtStart.DistanceTo(want) > abs(o) * 0.5 + self.tol * 10:
                return None
        return c

    def bounds(self):
        return [self.boundary(o) for o in self.offsets]

    def centreline(self):
        return self.boundary(self.centre_offset)

    # solids

    def solids(self):
        """[(Brep, layer)] one closed solid per layer, uncut."""
        if self._solids is None:
            out = []
            b = self.bounds()
            for i, layer in enumerate(self.layers):
                s = self._layer(b[i], b[i + 1])
                if s is not None:
                    out.append((s, layer))
            self._solids = out
        return [(s.DuplicateBrep(), l) for s, l in self._solids]

    def _layer(self, b0, b1):
        if b0 is None or b1 is None or self.height <= self.tol:
            return None
        if self.closed:
            pb = rg.Brep.CreatePlanarBreps([b0, b1], self.tol)
            if not pb or len(pb) != 1 or pb[0].Faces.Count != 1 or pb[0].Faces[0].Loops.Count != 2:
                return None
            b = solid(pb[0].Faces[0].CreateExtrusion(rg.LineCurve(rg.Point3d.Origin, rg.Point3d(0, 0, self.height)), True))
            return b if b is not None and b.IsValid and b.IsSolid else None
        if isinstance(b0, rg.PolylineCurve) and isinstance(b1, rg.PolylineCurve):
            p0 = [b0.Point(i) for i in range(b0.PointCount)]
            p1 = [b1.Point(i) for i in range(b1.PointCount)]
            outline = rg.PolylineCurve(p0 + p1[::-1] + [p0[0]])
        else:
            r = b1.DuplicateCurve()
            r.Reverse()
            parts = [b0, rg.LineCurve(b0.PointAtEnd, r.PointAtStart), r, rg.LineCurve(r.PointAtEnd, b0.PointAtStart)]
            joined = rg.Curve.JoinCurves(parts, self.tol * 10)
            if not joined or len(joined) != 1 or not joined[0].IsClosed:
                return None
            outline = joined[0]
        b = extrude_up(outline, self.height, self.tol)
        return b if b is not None and b.IsValid and b.IsSolid else None

    # openings

    def station(self, at_m):
        """(centre of the wall's thickness at the base, tangent, exterior normal, parameter) at `at` m along the path."""
        t = self.param(at_m * self.u)
        p = self.path.PointAt(t)
        tan = self.tangent(t)
        left = _left(tan)
        return p + left * self.centre_offset, tan, left * self.side, t

    def sag(self, at_m, width_m):
        """How far a curved wall bows away from its chord over an opening (model units)."""
        if self.pts:
            return 0.0
        c, tan, ext, _ = self.station(at_m)
        worst = 0.0
        for k in range(-3, 4):
            q, _, _, _ = self.station(at_m + width_m * k / 6.0)
            worst = max(worst, abs((q - c) * ext))
        return worst

    def kinks(self):
        """[(distance along the path, turn in radians)] for every corner of the path."""
        out = []
        if self.pts:
            n = len(self.pts)
            s = 0.0
            dirs = [_flat(self.pts[(i + 1) % n] - self.pts[i]) for i in range(n if self.closed else n - 1)]
            for i in range(len(dirs)):
                if i > 0 or self.closed:
                    turn = rg.Vector3d.VectorAngle(dirs[i - 1], dirs[i])
                    if turn > math.radians(1):
                        out.append((s, turn))
                s += self.pts[i].DistanceTo(self.pts[(i + 1) % n])
            return out
        dom = self.path.Domain
        t = dom.Min
        eps = (dom.Max - dom.Min) * 1e-6
        for _ in range(200):
            ok, t2 = self.path.GetNextDiscontinuity(rg.Continuity.G1_continuous, t, dom.Max)
            if not ok or t2 >= dom.Max - eps:
                break
            turn = rg.Vector3d.VectorAngle(_flat(self.path.TangentAt(t2 - eps)), _flat(self.path.TangentAt(t2 + eps)))
            if turn > math.radians(1):
                out.append((self.along(t2), turn))
            t = t2
        if self.closed:
            turn = rg.Vector3d.VectorAngle(_flat(self.path.TangentAt(dom.Max - eps)), _flat(self.path.TangentAt(dom.Min + eps)))
            if turn > math.radians(1):
                out.insert(0, (0.0, turn))
        return out

    def spans(self):
        """Stretches of wall an opening may sit in: [(a, b, clear_a, clear_b, whole_loop)] along the path (model units).
        Clearances keep openings out of mitred corners and off free ends; on a closed path b may pass the length."""
        L = self.length
        m = self.reach + 0.05 * self.u
        nib = 0.05 * self.u

        def at_corner(turn):
            return m * math.tan(min(turn, math.radians(170)) / 2.0) + 0.02 * self.u

        def at_end(which):
            c = self.caps[which]
            if c is None:
                return nib
            t = self.path.Domain.Min if which == 0 else self.path.Domain.Max
            phi = rg.Vector3d.VectorAngle(c, _left(self.tangent(t)))
            phi = min(phi, math.pi - phi)
            return m * math.tan(min(phi, math.radians(80))) + nib

        k = self.kinks()
        if self.closed:
            if not k:
                return [(0.0, L, 0.0, 0.0, True)]
            out = []
            for i, (s, turn) in enumerate(k):
                s2, turn2 = k[(i + 1) % len(k)]
                if i == len(k) - 1:
                    s2 += L
                out.append((s, s2, at_corner(turn), at_corner(turn2), False))
            return out
        stops = [(0.0, at_end(0))] + [(s, at_corner(turn)) for s, turn in k] + [(L, at_end(1))]
        return [(stops[i][0], stops[i + 1][0], stops[i][1], stops[i + 1][1], False) for i in range(len(stops) - 1)]

    def fit(self, at_m, width_m, z0, z1, others, gap_m=0.05):
        """Keep an opening on its stretch of wall and clear of the others there.
        -> (at in m or None, note or None). z0/z1 (m above the floor) let a window sit over a door."""
        u, L = self.u, self.length
        w = width_m * u
        c = at_m * u
        c = c % L if self.closed else max(0.0, min(L, c))
        for a, b, ca, cb, whole in self.spans():
            for cc in ((c, c + L) if self.closed else (c,)):
                if a - 1e-6 <= cc <= b + 1e-6:
                    break
            else:
                continue
            note = None
            if not whole:
                lo, hi = a + ca + w / 2.0, b - cb - w / 2.0
                if lo > hi:
                    return None, "%.2f m wide doesn't fit the %.2f m of clear wall there" % (width_m, max(0.0, (b - a - ca - cb) / u))
                c2 = min(max(cc, lo), hi)
                if abs(c2 - cc) > 0.001 * u:
                    note = "moved %.2f m to stay on the wall" % ((c2 - cc) / u)
                cc = c2
            c = cc % L if self.closed else cc
            break
        for o in others:
            d = abs(c / u - float(o["at"]))
            if self.closed:
                d = min(d, L / u - d)
            oz0 = float(o.get("sill") or 0)
            oz1 = oz0 + float(o.get("height") or 0)
            if d < (width_m + float(o["width"])) / 2.0 + gap_m and z0 < oz1 + gap_m and oz0 < z1 + gap_m:
                return None, "overlaps %s" % o.get("id", "an opening")
        return round(c / u, 6), note

    def cutter(self, op):
        """A box through the wall for an opening record."""
        u = self.u
        w = float(op["width"]) * u
        c, tan, ext, _ = self.station(float(op["at"]))
        z0 = self.floor + float(op.get("sill") or 0) * u - self.base
        z1 = z0 + float(op["height"]) * u
        m = 0.05 * u
        depth = self.T / 2.0 + m + self.sag(float(op["at"]), float(op["width"]))
        if z0 <= self.tol:
            z0 = -m
        if z1 >= self.height - self.tol:
            z1 = self.height + m
        return Frame.box_at(c, tan, ext, -w / 2.0, w / 2.0, -depth, depth, z0, z1)

    def cut_solids(self, openings):
        """[(Brep, layer)] with every opening cut through, and problems. Straight-segment (polyline) walls are built
        face by face with the holes in them; curved walls, and anything that can't be built that way, use Booleans,
        cached per prefix of the openings list so windows after doors only cut the windows."""
        problems = []
        if not openings:
            return self.solids(), problems
        gkey = _geom_key(self.rec) + str(self.u)
        sig = [json.dumps({k: o.get(k) for k in ("at", "width", "height", "sill")}, sort_keys=True) for o in openings]
        hit = _CUTS.get((gkey, tuple(sig)))
        if hit is not None:
            return [(b.DuplicateBrep(), l) for b, l in hit], problems
        pieces = None
        if self.pts:
            pieces = []
            for i, layer in enumerate(self.layers):
                got = self._assemble(i, openings)
                if got is None:
                    pieces = None
                    break
                pieces.extend((b, layer) for b in got)
        if pieces is None:
            pieces, problems = self._boolean(openings, gkey, sig)
        _remember(_CUTS, (gkey, tuple(sig)), [(b.DuplicateBrep(), l) for b, l in pieces], 200)
        return pieces, problems

    def _boolean(self, openings, gkey, sig):
        problems = []
        start, done = 0, None
        for k in range(len(sig) - 1, 0, -1):
            hit = _CUTS.get((gkey, tuple(sig[:k])))
            if hit is not None:
                start, done = k, hit
                break
        pieces = [(b.DuplicateBrep(), l) for b, l in done] if done is not None else self.solids()
        cutters = [c for c in (self.cutter(o) for o in openings[start:]) if c is not None]
        out = []
        for b, layer in pieces:
            bb = b.GetBoundingBox(True)
            mine = [c for c in cutters if _overlap(bb, c.GetBoundingBox(True))]
            if not mine:
                out.append((b, layer))
                continue
            res = rg.Brep.CreateBooleanDifference([b], mine, self.tol)
            if not res or not all(r.IsSolid for r in res):
                res = [b]  # one at a time, keeping what works
                for c in mine:
                    nxt = []
                    for r in res:
                        d = rg.Brep.CreateBooleanDifference(r, c, self.tol) if _overlap(r.GetBoundingBox(True), c.GetBoundingBox(True)) else [r]
                        if d is not None and len(d) and all(x.IsSolid for x in d):
                            nxt.extend(d)
                        else:
                            nxt.append(r)
                            problems.append("%s: couldn't cut an opening through the %s layer" % (self.id, layer["name"]))
                    res = nxt
            out.extend((solid(r), layer) for r in res)
        return out, problems

    def _assemble(self, i, openings):
        """Layer i of a polyline wall with its openings, built from planar faces and joined (no Booleans): the side
        faces with the holes (notches for doors), the top and bottom split at openings that reach them, jambs, heads
        and sills. None when an opening doesn't sit on one straight segment or the faces don't close up."""
        A, B = self.bounds()[i], self.bounds()[i + 1]
        if not (isinstance(A, rg.PolylineCurve) and isinstance(B, rg.PolylineCurve)):
            return None
        pts, tol, H, base, u = self.pts, self.tol, self.height, self.base, self.u
        n = len(pts)
        segs = n if self.closed else n - 1
        av = [A.Point(k) for k in range(A.PointCount)]
        bv = [B.Point(k) for k in range(B.PointCount)]
        if len(av) != segs + 1 or len(bv) != segs + 1:
            return None
        oA, oB = self.offsets[i], self.offsets[i + 1]
        seg, run = [], 0.0
        for j in range(segs):
            p, q = pts[j], pts[(j + 1) % n]
            d = _flat(q - p)
            seg.append((p.X, p.Y, d.X, d.Y, run))
            run += p.DistanceTo(q)
        seg.append((0, 0, 0, 0, run))

        def on(j, o, t, z):
            x, y, dx, dy, _ = seg[j]
            return rg.Point3d(x - dy * o + dx * t, y + dx * o + dy * t, base + z)

        def tpos(j, v):
            x, y, dx, dy, _ = seg[j]
            return (v.X - x) * dx + (v.Y - y) * dy

        cuts = []  # (segment, a, b, z0, z1, from the bottom, to the top)
        for op in openings:
            c = float(op["at"]) * u
            if self.closed:
                c %= self.length
            w2 = float(op["width"]) * u / 2.0
            z0 = self.floor + float(op.get("sill") or 0) * u - base
            z1 = z0 + float(op["height"]) * u
            bottom, top = z0 <= tol, z1 >= H - tol
            z0, z1 = max(z0, 0.0), min(z1, H)
            if z1 - z0 <= tol:
                continue
            j = next((k for k in range(segs) if seg[k][4] - 1e-6 <= c <= seg[k + 1][4] + 1e-6), None)
            if j is None:
                return None
            a, b = c - seg[j][4] - w2, c - seg[j][4] + w2
            for vv in (av, bv):
                if not (tpos(j, vv[j]) + tol < a and b < tpos(j, vv[j + 1]) - tol):
                    return None
            cuts.append((j, a, b, z0, z1, bottom, top))
        if not cuts:
            return [s.DuplicateBrep() for s, l in self.solids() if l is self.layers[i]]
        cuts.sort(key=lambda c: (c[0], c[1]))
        faces = []

        def planar(loops):
            crvs = [rg.PolylineCurve(lp + [lp[0]]) for lp in loops]
            pb = rg.Brep.CreatePlanarBreps(crvs, tol)
            if not pb:
                raise ValueError("face")
            faces.extend(pb)

        def flat(v, z):
            return rg.Point3d(v.X, v.Y, base + z)

        try:
            for o, vv in ((oA, av), (oB, bv)):  # the two side faces, segment by segment
                for j in range(segs):
                    mine = [c for c in cuts if c[0] == j]
                    for outer, inner in _face_loops(tpos(j, vv[j]), tpos(j, vv[j + 1]), H, mine):
                        planar([[on(j, o, t, z) for t, z in outer]] + [[on(j, o, t, z) for t, z in lp] for lp in inner])
            for z, which in ((0.0, 5), (H, 6)):  # bottom and top, split where openings reach them
                gaps = [c for c in cuts if c[which]]
                if not gaps:
                    if self.closed:
                        planar([[flat(v, z) for v in av[:-1]], [flat(v, z) for v in bv[:-1]]])
                    else:
                        planar([[flat(v, z) for v in av] + [flat(v, z) for v in reversed(bv)]])
                    continue
                if self.closed:
                    spans = [(g, gaps[(k + 1) % len(gaps)]) for k, g in enumerate(gaps)]
                else:
                    spans = [(None, gaps[0])] + [(gaps[k], gaps[k + 1]) for k in range(len(gaps) - 1)] + [(gaps[-1], None)]
                for g0, g1 in spans:
                    if g0 is None:
                        ja, ia = 0, [0]
                    else:
                        ja, ia = g0[0], None
                    jb = g1[0] if g1 is not None else segs - 1
                    count = (jb - ja) % segs if self.closed else jb - ja
                    if self.closed and count == 0 and g1[1] < g0[2]:
                        count = segs
                    between = [(ja + 1 + q) % segs for q in range(count)]
                    if g1 is None:
                        between.append(segs)
                    if g0 is None:
                        between = [0] + between
                    sa = [on(g0[0], oA, g0[2], z)] if g0 is not None else []
                    sb = [on(g0[0], oB, g0[2], z)] if g0 is not None else []
                    ea = [on(g1[0], oA, g1[1], z)] if g1 is not None else []
                    eb = [on(g1[0], oB, g1[1], z)] if g1 is not None else []
                    side_a = sa + [flat(av[k], z) for k in between] + ea
                    side_b = sb + [flat(bv[k], z) for k in between] + eb
                    planar([side_a + side_b[::-1]])
            for j, a, b, z0, z1, bottom, top in cuts:  # reveals
                for t in (a, b):
                    planar([[on(j, oA, t, z0), on(j, oB, t, z0), on(j, oB, t, z1), on(j, oA, t, z1)]])
                for z, skip in ((z1, top), (z0, bottom)):
                    if not skip:
                        planar([[on(j, oA, a, z), on(j, oA, b, z), on(j, oB, b, z), on(j, oB, a, z)]])
            if not self.closed:  # the ends
                for k in (0, segs):
                    planar([[flat(av[k], 0), flat(bv[k], 0), flat(bv[k], H), flat(av[k], H)]])
        except ValueError:
            return None
        joined = rg.Brep.JoinBreps(faces, tol)
        if not joined or not all(b.IsSolid and b.IsValid for b in joined):
            return None
        return [solid(b) for b in joined]

    # plan

    def plan(self, openings=()):
        """Cut linework at the plan height (floor + cut): every layer boundary, the ends, and the jambs of openings
        the cut passes through, with the wall lines removed across them (where wall stands below an opening, e.g. a
        window's sill, its two faces stay: seen below the cut)."""
        tol = self.tol
        zc = min(max(self.floor + self.cut, self.base + tol), self.base + self.height - tol) if self.height > 2 * tol else self.base
        move = rg.Transform.Translation(0, 0, zc - self.base)
        lines = [b.DuplicateCurve() for b in self.bounds() if b is not None]
        if not self.closed and len(lines) >= 2:
            lines += [rg.LineCurve(lines[0].PointAtStart, lines[-1].PointAtStart), rg.LineCurve(lines[0].PointAtEnd, lines[-1].PointAtEnd)]
        for c in lines:
            c.Transform(move)
        holes = []  # (centre x, y, tangent x, y, exterior x, y, half width, half depth, wall below) as floats: fast
        for op in openings:
            z0 = self.floor + float(op.get("sill") or 0) * self.u
            z1 = z0 + float(op["height"]) * self.u
            if z0 < zc < z1:
                c, tan, ext, _ = self.station(float(op["at"]))
                holes.append((c.X, c.Y, tan.X, tan.Y, ext.X, ext.Y, float(op["width"]) * self.u / 2.0,
                              self.T / 2.0 + 0.05 * self.u + self.sag(float(op["at"]), float(op["width"])), z0 > self.base + tol))
        if not holes:
            return lines

        def jamb_plane(h, sgn):
            return rg.Plane(rg.Point3d(h[0] + h[2] * sgn * h[6], h[1] + h[3] * sgn * h[6], zc), rg.Vector3d(h[2], h[3], 0))

        def across(q, h):  # |offset of q from the opening's centre line, across the wall|
            return abs((q.X - h[0]) * h[4] + (q.Y - h[1]) * h[5])

        out = []
        faces = (0, len(self.bounds()) - 1)
        for i, crv in enumerate(lines):
            params = []
            for h in holes:
                for sgn in (-1, 1):
                    x = rg.Intersect.Intersection.CurvePlane(crv, jamb_plane(h, sgn), tol)
                    for ev in (x or []):
                        if across(ev.PointA, h) <= h[7]:
                            params.append(ev.ParameterA)
            pieces = crv.Split(sorted(set(params))) if params else None
            for p in (pieces if pieces else [crv]):
                mid = p.PointAt(p.Domain.Mid)
                inside = [h[8] for h in holes if abs((mid.X - h[0]) * h[2] + (mid.Y - h[1]) * h[3]) < h[6] - tol and across(mid, h) <= h[7]]
                if not inside or (i in faces and all(inside)):
                    out.append(p)
        # jambs: across the full thickness at each side of the hole
        outer = []
        for b in (self.bounds()[0], self.bounds()[-1]):
            bz = b.DuplicateCurve()
            bz.Transform(move)
            outer.append(bz)
        for h in holes:
            for sgn in (-1, 1):
                pl = jamb_plane(h, sgn)
                ends = []
                for bz in outer:
                    x = rg.Intersect.Intersection.CurvePlane(bz, pl, tol)
                    hits = [ev.PointA for ev in (x or []) if across(ev.PointA, h) <= h[7]]
                    if hits:
                        ends.append(min(hits, key=lambda q: across(q, h)))
                if len(ends) == 2 and ends[0].DistanceTo(ends[1]) > tol:
                    out.append(rg.LineCurve(ends[0], ends[1]))
        return out


def _overlap(a, b):
    return a.Min.X <= b.Max.X and b.Min.X <= a.Max.X and a.Min.Y <= b.Max.Y and b.Min.Y <= a.Max.Y and a.Min.Z <= b.Max.Z and b.Min.Z <= a.Max.Z


def _face_loops(t0, t1, H, cuts):
    """A wall face t0..t1 by 0..H (local t along the wall, z up) less its openings: [(outer loop, [hole loops])].
    Openings reaching the bottom or top notch the outline; one running full height splits the face."""
    pieces, start, cur = [], t0, []
    for c in sorted(cuts, key=lambda c: c[1]):
        if c[5] and c[6]:
            pieces.append((start, c[1], cur))
            start, cur = c[2], []
        else:
            cur.append(c)
    pieces.append((start, t1, cur))
    out = []
    for ta, tb, cs in pieces:
        loop = [(ta, 0.0)]
        for c in cs:
            if c[5]:
                loop += [(c[1], 0.0), (c[1], c[4]), (c[2], c[4]), (c[2], 0.0)]
        loop += [(tb, 0.0), (tb, H)]
        for c in reversed(cs):
            if c[6]:
                loop += [(c[2], H), (c[2], c[3]), (c[1], c[3]), (c[1], H)]
        loop.append((ta, H))
        holes = [[(c[1], c[3]), (c[2], c[3]), (c[2], c[4]), (c[1], c[4])] for c in cs if not c[5] and not c[6]]
        out.append((loop, holes))
    return out


# ── joins between separate walls ─────────────────────────────

def join(recs, u=None):
    """Clean ends where separate walls meet: two ends at one point are mitred (L); an end that stops inside another
    wall is trimmed back to that wall's face and cut along it (T). Crossings (X) are left alone.
    Returns notes. Changes the records in place (path and caps)."""
    u = u or bim.units()
    walls = [Wall(r, u) for r in recs]
    tol = bim.tol()
    near = max(0.01 * u, tol * 10)
    notes = []
    ends = []
    for i, w in enumerate(walls):
        if w.closed or w.length <= tol:
            continue
        dom = w.path.Domain
        ends.append((i, 0, w.path.PointAtStart, w.tangent(dom.Min)))
        ends.append((i, 1, w.path.PointAtEnd, -w.tangent(dom.Max)))  # pointing back into the wall
    caps = {i: [None, None] for i in range(len(recs))}
    trims = {i: [None, None] for i in range(len(recs))}
    used = set()
    for a in range(len(ends)):
        here = [b for b in range(len(ends)) if ends[b][2].DistanceTo(ends[a][2]) <= near]
        if len(here) != 2 or a in used:
            continue
        b = here[1] if here[0] == a else here[0]
        (i, wi, p, da), (j, wj, q, db) = ends[a], ends[b]
        if i == j:
            continue
        used.update((a, b))
        if rg.Vector3d.VectorAngle(da, db) > math.radians(179):
            continue  # in line: square ends meet
        m = da + db
        m.Unitize()
        caps[i][wi] = caps[j][wj] = m
        notes.append("%s/%s: mitred corner" % (walls[i].id, walls[j].id))
    for k, (i, wi, p, d) in enumerate(ends):
        if k in used:
            continue
        for j, o in enumerate(walls):
            if j == i:
                continue
            ok, t = o.path.ClosestPoint(p)
            if not ok:
                continue
            q = o.path.PointAt(t)
            off = (p - q) * _left(o.tangent(t))
            if not (min(o.offsets) - near <= off <= max(o.offsets) + near):
                continue
            if not o.closed and (o.along(t) < o.reach or o.length - o.along(t) < o.reach):
                continue  # at the other wall's end: a corner, not a T
            w = walls[i]
            best = None
            for face in (o.boundary(min(o.offsets)), o.boundary(max(o.offsets))):
                if face is None:
                    continue
                x = rg.Intersect.Intersection.CurveCurve(w.path, face, tol, tol)
                for ev in (x or []):
                    s = w.along(ev.ParameterA)
                    gone = s if wi == 0 else w.length - s  # length cut off this end
                    if 0 <= gone < w.length - tol and (best is None or gone < best[0]):
                        best = (gone, ev.ParameterA, _flat(face.TangentAt(ev.ParameterB)))
            if best is None:
                continue
            if best[0] <= tol:  # already ends on the face: just cut along it
                caps[i][wi] = best[2]
            else:
                trims[i][wi] = best[1]
                caps[i][wi] = best[2]
            note = "%s butts into %s" % (w.id, o.id)
            if note not in notes:
                notes.append(note)
            break
    for i, rec in enumerate(recs):
        w = walls[i]
        if trims[i] != [None, None]:
            dom = w.path.Domain
            t0 = trims[i][0] if trims[i][0] is not None else dom.Min
            t1 = trims[i][1] if trims[i][1] is not None else dom.Max
            if t1 - t0 > 1e-9:
                c = w.path.Trim(t0, t1)
                if c is not None and c.GetLength() > tol:
                    rec["path"] = bim.curve_record(c, u)
                    rec["length"] = round(c.GetLength() / u, 6)
        rec["caps"] = {"start": [round(caps[i][0].X, 6), round(caps[i][0].Y, 6)] if caps[i][0] else None,
                       "end": [round(caps[i][1].X, 6), round(caps[i][1].Y, 6)] if caps[i][1] else None}
    return notes


# ── choosing walls and positions ─────────────────────────────

def walls_in(value):
    """Wall records on an input (deep copies, each with an openings list), or a sample wall when none."""
    recs = [copy.deepcopy(r) for r in bim.read(value, "wall")]
    sample = not recs
    if sample:
        recs = [sample_wall()]
    for r in recs:
        r["openings"] = [o for o in (r.get("openings") or []) if isinstance(o, dict)]
    return recs, sample


def pick(recs, text):
    """Which walls `text` names: ids or indexes ('WL02', '0, 2', '-1'), 'all' or '*'. -> (indexes, problems); blank -> []."""
    out, problems = [], []
    for tok in re.split(r"[,;\s]+", str(text or "").strip()):
        if not tok:
            continue
        if tok.lower() in ("all", "*"):
            return list(range(len(recs))), problems
        hit = [i for i, r in enumerate(recs) if str(r.get("id", "")).lower() == tok.lower()]
        if not hit and re.fullmatch(r"-?\d+", tok):
            k = int(tok)
            if -len(recs) <= k < len(recs):
                hit = [k % len(recs)]
        if hit:
            out += [i for i in hit if i not in out]
        else:
            problems.append("no wall '%s'" % tok)
    return out, problems


def positions(items, length_m):
    """'1.2, 4.5, -1, 50%' -> distances (m) along a wall: negative counts back from the end, % of its length.
    -> (list, problems)."""
    out, problems = [], []
    for item in items or []:
        if item is None:
            continue
        if isinstance(item, (int, float)):
            toks = [str(item)]
        else:
            toks = re.split(r"[,;\s]+", str(item).strip())
        for tok in toks:
            if not tok:
                continue
            try:
                if tok.endswith("%"):
                    out.append(length_m * float(tok[:-1]) / 100.0)
                else:
                    v = float(tok)
                    out.append(length_m + v if v < 0 else v)
            except ValueError:
                problems.append("not a distance: '%s'" % tok)
    return out, problems


def place(recs, kind, prefix, wall_text, position_items, points, width, height, sill, fields):
    """Add openings to wall records (in place). Distances go on the named walls (blank: the first); points go to the
    nearest named wall (blank: any). No distances and no points: one opening mid-wall. -> (new openings, notes)."""
    u = bim.units()
    notes = []
    chosen, problems = pick(recs, wall_text)
    notes += problems
    targets = []  # (wall index, at m)
    pts = [p for p in (points or []) if isinstance(p, rg.Point3d)]
    for p in pts:
        pool = chosen or list(range(len(recs)))
        best = None
        for i in pool:
            w = wall_of(recs[i])
            flat = rg.Point3d(p.X, p.Y, w.base)
            ok, t = w.path.ClosestPoint(flat)
            if ok:
                d = flat.DistanceTo(w.path.PointAt(t))
                if best is None or d < best[0]:
                    best = (d, i, w.along(t) / u)
        if best:
            if best[0] > 1.0 * u:
                notes.append("a point %.1f m from the nearest wall went to %s" % (best[0] / u, recs[best[1]]["id"]))
            targets.append((best[1], best[2]))
    has_text = any(str(x).strip() for x in (position_items or []) if x is not None)
    for i in (chosen or [0]):
        L = float(recs[i].get("length") or wall_of(recs[i]).length / u)
        if has_text:
            ds, problems = positions(position_items, L)
            notes += [m for m in problems if m not in notes]
            targets += [(i, d) for d in ds]
        elif not pts:
            targets.append((i, L / 2.0))
    taken = []
    for r in recs:
        for o in r["openings"]:
            m = re.match(r"%s(\d+)$" % re.escape(prefix), str(o.get("id", "")))
            if m:
                taken.append(int(m.group(1)))
    n = max(taken) if taken else 0
    new = []
    for i, at in targets:
        rec = recs[i]
        w = wall_of(rec)
        top = (w.base + w.height - w.floor) / u  # top of the wall above the floor
        bottom = (w.base - w.floor) / u
        s = max(sill, bottom)
        h = min(height, top - s)
        if h <= 0.05:
            notes.append("%s at %.2f m: the wall is too low for it" % (rec["id"], at))
            continue
        if h < height - 1e-6:
            notes.append("%s at %.2f m: height cut to %.2f m to fit the wall" % (rec["id"], at, h))
        at2, note = w.fit(at, width, s, s + h, rec["openings"])
        if at2 is None:
            notes.append("skipped %s at %.2f m on %s: %s" % (kind, at, rec["id"], note))
            continue
        n += 1
        op = {"alien": "opening", "id": "%s%02d" % (prefix, n), "kind": kind, "wall": rec["id"], "at": at2,
              "width": width, "height": round(h, 6), "sill": round(s, 6)}
        op.update(fields)
        if note:
            notes.append("%s on %s: %s" % (op["id"], rec["id"], note))
        rec["openings"].append(op)
        new.append((i, op))
    return new, notes


def cut_all(recs):
    """Every wall's layer solids with all its openings cut. -> (solids, colours, materials, plan lines, problems)."""
    solids, colours, materials, plan, problems = [], [], [], [], []
    for r in recs:
        try:
            w = wall_of(r)
        except Exception as ex:
            problems.append("%s: %s" % (r.get("id"), ex))
            continue
        pieces, p = w.cut_solids(r.get("openings") or [])
        problems += p
        for b, layer in pieces:
            if b is None:
                continue
            solids.append(b)
            materials.append(layer["material"])
            colours.append(colour(layer["material"]))
        plan += w.plan(r.get("openings") or [])
    return solids, colours, materials, plan, problems


# ── opening frames ───────────────────────────────────────────

class Frame(object):
    """An opening's local frame: x along the wall (path direction), y towards the exterior, z up from the bottom of
    the opening, origin at the wall's centre. Sizes in model units."""

    def __init__(self, wall, op):
        u = self.u = wall.u
        self.wall, self.op = wall, op
        self.tol = wall.tol
        c, self.tan, self.ext, _ = wall.station(float(op["at"]))
        self.z0 = wall.floor + float(op.get("sill") or 0) * u
        self.origin = rg.Point3d(c.X, c.Y, self.z0)
        self.w = float(op["width"]) * u
        self.h = float(op["height"]) * u
        self.f = float(op.get("frame") or 0.05) * u
        self.T = wall.T
        self.sag = wall.sag(float(op["at"]), float(op["width"]))
        self.zcut = wall.floor + wall.cut - self.z0  # plan cut, local z
        self._f = (self.origin.X, self.origin.Y, self.origin.Z, self.tan.X, self.tan.Y, self.ext.X, self.ext.Y)

    def hinge_sign(self, swing, hand):
        """+1 when the hinges (or the side it opens towards) are at +x. Hand is seen from the side it opens into:
        standing there facing the wall, left = on your left. The frame (x along the path, y out) is left-handed when
        the exterior is on the path's right, which flips the answer."""
        s = 1 if str(swing).lower().startswith("out") else -1
        right_handed = _cross(self.tan, self.ext) > 0
        return s * (1 if str(hand).lower().startswith("l") else -1) * (1 if right_handed else -1)

    def pt(self, x, y, z):
        ox, oy, oz, tx, ty, ex, ey = self._f
        return rg.Point3d(ox + tx * x + ex * y, oy + ty * x + ey * y, oz + z)

    @staticmethod
    def box_at(origin, tan, ext, x0, x1, y0, y1, z0, z1):
        if x1 - x0 <= 0 or y1 - y0 <= 0 or z1 - z0 <= 0:
            return None
        if _cross(tan, ext) > 0:
            pl, iy = rg.Plane(origin, tan, ext), rg.Interval(y0, y1)
        else:
            pl, iy = rg.Plane(origin, tan, -ext), rg.Interval(-y1, -y0)
        return rg.Box(pl, rg.Interval(x0, x1), iy, rg.Interval(z0, z1)).ToBrep()

    def box(self, x0, x1, y0, y1, z0, z1):
        return Frame.box_at(self.origin, self.tan, self.ext, x0, x1, y0, y1, z0, z1)

    def prism(self, loops, y0, y1):
        """Closed loops in the local x-z plane [[(x, z)...], holes...] pushed through from y0 to y1: one solid."""
        curves = []
        for loop in loops:
            pts = [self.pt(x, y0, z) for x, z in loop]
            curves.append(rg.PolylineCurve(pts + [pts[0]]))
        pb = rg.Brep.CreatePlanarBreps(curves, self.tol)
        if not pb:
            return None
        return solid(pb[0].Faces[0].CreateExtrusion(rg.LineCurve(rg.Point3d.Origin, rg.Point3d.Origin + self.ext * (y1 - y0)), True))

    def ring(self, x0, x1, z0, z1, width, y0, y1, bottom=True):
        """A frame of `width` around x0..x1 by z0..z1 (without the bottom member when bottom is False)."""
        if bottom:
            return self.prism([[(x0, z0), (x1, z0), (x1, z1), (x0, z1)],
                               [(x0 + width, z0 + width), (x1 - width, z0 + width), (x1 - width, z1 - width), (x0 + width, z1 - width)]], y0, y1)
        return self.prism([[(x0, z0), (x0 + width, z0), (x0 + width, z1 - width), (x1 - width, z1 - width), (x1 - width, z0),
                            (x1, z0), (x1, z1), (x0, z1)]], y0, y1)

    def poly(self, pts, closed=False):
        p = [self.pt(*q) for q in pts]
        return rg.PolylineCurve(p + [p[0]] if closed else p)

    def rect_plan(self, x0, x1, y0, y1, z):
        return self.poly([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], True)

    def line(self, a, b):
        return rg.LineCurve(self.pt(*a), self.pt(*b))

    def arc(self, a, m, b):
        return rg.ArcCurve(rg.Arc(self.pt(*a), self.pt(*m), self.pt(*b)))

    def dashed(self, a, b):
        return dashed(self.pt(*a), self.pt(*b), 0.08 * self.u, 0.05 * self.u)

    def arrow(self, a, b, plan=False):
        """A line from a to b with an open arrowhead at b: drawn in plan (flat) or on the wall's face (elevation)."""
        pa, pb = self.pt(*a), self.pt(*b)
        v = pb - pa
        if v.Length < 1e-9:
            return []
        head = min(0.12 * self.u, v.Length * 0.35)
        v.Unitize()
        side = rg.Vector3d.CrossProduct(v, Z if plan else self.ext)
        if side.Length < 1e-6:
            side = rg.Vector3d.CrossProduct(v, self.tan)
        side.Unitize()
        back = pb - v * head
        return [rg.LineCurve(pa, pb), rg.LineCurve(pb, back + side * (head * 0.5)), rg.LineCurve(pb, back - side * (head * 0.5))]

    def fit(self, geoms, box, x0, x1, y0, y1, z0, z1, front=1, mirror=False):
        """Map a design (geometry list, its bounding box `box`) into the local box: its width along x, depth along y,
        height up z; its front (-Y as built) faces `front` (+1 exterior, -1 interior). Mirrored across x on request."""
        dims = box.Max - box.Min
        sx = (x1 - x0) / dims.X if dims.X > 1e-9 else 1.0
        sy = (y1 - y0) / dims.Y if dims.Y > 1e-9 else 1.0
        sz = (z1 - z0) / dims.Z if dims.Z > 1e-9 else 1.0
        fdir = self.ext * front
        yw = -fdir
        xw = rg.Vector3d.CrossProduct(yw, Z)
        centre = self.pt((x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0)
        xf = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, rg.Plane(centre, xw, yw)) * rg.Transform.Scale(rg.Plane.WorldXY, sx, sy, sz)
        if mirror:
            xf = xf * rg.Transform.Mirror(rg.Plane.WorldYZ)
        xf = xf * rg.Transform.Translation(-(box.Min.X + box.Max.X) / 2.0, -(box.Min.Y + box.Max.Y) / 2.0, -(box.Min.Z + box.Max.Z) / 2.0)
        out = []
        for g in geoms:
            if isinstance(g, rg.Extrusion) or isinstance(g, rg.Surface):
                g = g.ToBrep()
            d = g.DuplicateBrep() if isinstance(g, rg.Brep) else g.Duplicate()
            try:
                d.MakeDeformable()
            except Exception:
                pass
            if d.Transform(xf):
                if isinstance(d, rg.Brep):
                    d = solid(d)
                out.append(d)
        return out


def design_of(value):
    """Geometry wired as a design and its bounding box; ([], None) when there's none."""
    geoms = []
    for g in bim.flat(value if isinstance(value, (list, tuple)) else [value]):
        if isinstance(g, rg.GeometryBase):
            geoms.append(g)
    if not geoms:
        return [], None
    box = rg.BoundingBox.Empty
    for g in geoms:
        box.Union(g.GetBoundingBox(True))
    return (geoms, box) if box.IsValid else ([], None)


