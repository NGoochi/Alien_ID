# NODE_INPUTS: path:Curve | "base line in plan: the glazing line before offset; exterior on its right, closed outlines face out (sample: a 12 m line)", levels:list[object] | "level records (sample: 3 storeys at 3.2 m)"
# NODE_INPUTS: base:str | "base level: name, code, index or an elevation like 4.5 (blank = the lowest)" default="", top:str | "top level: name, code or index, -2 = second from the top (blank = the highest)" default=""
# NODE_INPUTS: height:float | "height above the base level (m); 0 = up to the top level" min=0 max=200 default=0
# NODE_INPUTS: panel:float | "panel width (m): mullion spacing" min=0.1 max=12 default=1.5, fit:str | "what happens to the remainder" default="equal divide" options="equal divide|fixed from start|centred"
# NODE_INPUTS: transom_heights:str | "transom heights (m), e.g. '1.2, 2.4'; '@1.5' = every 1.5 m; negative = down from the top" default="", measure:str | "transom heights are measured from" default="each storey" options="each storey|wall base"
# NODE_INPUTS: at_levels:bool | "a transom at every level (slab edge)" default=true
# NODE_INPUTS: profile:str | "mullion profile" default="box" options="box|I|T|round|fin|flat bar", width:float | "mullion face width (mm)" min=10 max=400 default=50, depth:float | "mullion depth (mm), perpendicular to the facade; round: the diameter" min=20 max=1000 default=150
# NODE_INPUTS: transom_profile:str | "transom profile" default="same" options="same|box|I|T|round|fin|flat bar", transom_width:float | "transom face height (mm); 0 = as the mullion" min=0 max=400 default=0, transom_depth:float | "transom depth (mm); 0 = as the mullion" min=0 max=1000 default=0
# NODE_INPUTS: glazing:float | "glass thickness (mm)" min=4 max=80 default=28, offset:float | "glazing line offset from the path (m); + = outwards" min=-2 max=2 default=0, flip:bool | "swap inside and outside" default=false
# NODE_INPUTS: truss:str | "trusses behind the mullions (inside face)" default="none" options="none|warren|pratt|howe|vierendeel", truss_depth:float | "truss depth, chord centre to chord centre (m)" min=0.2 max=4 default=0.8
# NODE_INPUTS: chord:str | "chord section in mm: depth x width [x wall], e.g. 'box 100x100', 'round 114x6', 'I 150x75'" default="box 100x100", web:str | "web section in mm, e.g. 'round 60', 'box 60x60'" default="round 60"
# NODE_INPUTS: cut:float | "plan cut height above the base (m)" min=0 max=100 default=1
# NODE_OUTPUTS: solids:list[Brep] | "mullions, transoms, frame, glass and trusses, in that order", colours:list[Color] | "preview colour per solid"
# NODE_OUTPUTS: mullions:list[Brep], transoms:list[Brep], frame:list[Brep] | "jambs, sills and heads", glass:list[Brep], trusses:list[Brep]
# NODE_OUTPUTS: plan:list[Curve] | "cut at the plan height: member outlines and glass", grid:list[Curve] | "the curtain grid on the glazing plane", panels:list[Brep] | "cell surfaces on the grid, facing out"
# NODE_OUTPUTS: records:list[str] | "curtain_wall record, then one curtain_panel per cell", info:str
# CATEGORY: Building elements
# Curtain wall: mullions, transoms, perimeter frame, glass and optional back trusses along a straight, faceted or closed path, between levels.
#
# The path is faceted into straight panels: corners are always mullions, smooth stretches (arcs, splines) are split
# into chords of about the panel width. fit: equal divide (nearest whole number of equal panels per stretch), fixed
# from start (full panels, the remainder at the end), centred (full panels, the remainder split between both ends);
# a remainder narrower than the mullion plus 50 mm joins its neighbour. Transom heights apply in each storey (between
# levels) or once from the wall base; '@1.5' repeats, a negative height measures down from the storey or wall top.
# Mullions and transoms are member sections (alien_bim) with depth perpendicular to the facade. Framed profiles stand
# 25 mm proud of the glass with the glass cut to their faces; round tubes sit on the glazing line; fins stand behind
# continuous glass (12 mm joints) and get box channels for the jambs, sills and heads. Mullions run full height;
# transoms, sills and heads fit between them; trusses stand behind every mullion, chords against its inside face.
# Too many members (tiny panels, very tall walls) are capped with a note in info rather than freezing Grasshopper.
import bisect
import hashlib
import json
import math
import re

import Rhino.Geometry as rg

import alien_bim as bim

u = bim.units()
tol = bim.tol()
Z = rg.Vector3d.ZAxis
EPS = 1e-4 * u           # 0.1 mm: bands that only touch don't count as overlapping
MAX_MULLIONS = 300
MAX_CELLS = 1500
MAX_TRUSS = 1500         # truss members in all
MAX_BAYS = 200           # bays per truss
JOINT = 0.012            # silicone joint between frameless panes (m)
CAP = 0.025              # how far a framed member stands proud of the glass (m)
STEEL = ("i", "t", "fin", "flat bar")
notes = []


def number(v, default, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if v != v else max(lo, min(hi, v))


def member_type(profile, w_mm, d_mm, g):
    """A mullion or transom type: section spec (m), face width b, depth d, and c = its centre's distance in front of
    the glazing line (m; negative = behind)."""
    p = str(profile or "box").strip().lower()
    w, d = w_mm / 1000.0, d_mm / 1000.0
    if p == "round":  # a tube on the glazing line, glass to its sides
        return {"profile": p, "spec": {"shape": "tube", "d": d, "b": d}, "b": d, "d": d, "c": 0.0}
    if p == "fin":  # a plate behind continuous glass
        return {"profile": p, "spec": {"shape": "rect", "b": w, "d": d}, "b": w, "d": d, "c": -g / 2 - d / 2}
    if p == "flat bar":
        spec = {"shape": "rect", "b": w, "d": d}
    elif p in ("i", "t"):
        spec = {"shape": p.upper(), "b": w, "d": d, "tf": min(max(0.15 * w, 0.005), 0.3 * d), "tw": min(max(0.12 * w, 0.004), 0.5 * w)}
    else:
        p, spec = "box", {"shape": "box", "b": w, "d": d}
    front = min(d / 2, g / 2 + CAP)  # T: flange outwards, against the glass
    return {"profile": p.upper() if p in ("i", "t") else p, "spec": spec, "b": w, "d": d, "c": front - d / 2}


def channel(t, g):
    """Fins have no frame of their own: jambs, sills and heads become box channels around the glass."""
    if t["profile"] != "fin":
        return t
    b, d = max(t["b"], 0.05), g + 0.04
    return {"profile": "box", "spec": {"shape": "box", "b": b, "d": d}, "b": b, "d": d, "c": 0.0}


SHAPES = {"box": "box", "shs": "box", "rhs": "box", "rect": "rect", "flat": "rect", "plate": "rect", "solid": "rect",
          "round": "tube", "chs": "tube", "tube": "tube", "pipe": "tube", "rod": "circle", "i": "I", "ub": "I", "uc": "I",
          "h": "I", "t": "T", "tee": "T", "c": "C", "pfc": "C", "channel": "C", "l": "L", "angle": "L"}


def parse_section(text, default, what):
    """'box 100x100x6' -> section spec (m). Numbers are mm: depth (in the truss plane) x width [x wall]; round and
    rod take a diameter [x wall]."""
    s = str(text or "").strip().lower().replace("×", "x")
    m = re.fullmatch(r"([a-z]*)\s*(\d+(?:\.\d+)?)(?:\s*[x*]\s*(\d+(?:\.\d+)?))?(?:\s*[x*]\s*(\d+(?:\.\d+)?))?", s)
    shape = SHAPES.get(m.group(1) or "box") if m else None
    if not shape:
        if s:
            notes.append("%s section %r not understood: using '%s'" % (what, text, default))
        return parse_section(default, "box 100x100", what)
    n = [float(x) / 1000.0 for x in m.groups()[1:] if x]
    if not n[0] > 0:
        notes.append("%s section %r has no size: using '%s'" % (what, text, default))
        return parse_section(default, "box 100x100", what)
    if shape in ("tube", "circle"):
        spec = {"shape": shape, "d": n[0], "b": n[0]}
        if len(n) > 1 and n[1] > 0:
            spec["t"] = n[1]
    else:
        spec = {"shape": shape, "d": n[0], "b": n[1] if len(n) > 1 and n[1] > 0 else n[0]}
        if len(n) > 2 and n[2] > 0:
            spec["t"] = spec["tf"] = spec["tw"] = n[2]
    return spec


def parse_heights(text):
    vals, reps = [], []
    for tok in re.split(r"[,;\s]+", str(text or "").strip()):
        if not tok:
            continue
        rep = tok.startswith("@")
        try:
            v = float(tok[1:] if rep else tok)
        except ValueError:
            notes.append("transom height %r ignored" % tok)
            continue
        if rep and v <= 0:
            notes.append("transom spacing %r ignored" % tok)
        elif rep:
            reps.append(v)
        else:
            vals.append(v)
    return vals, reps


def band_extent(corners, origin, along, normal, v0, v1):
    """How far a member footprint (corner points) reaches along `along` inside the depth band v0..v1 (model units,
    measured along `normal` from origin); None when it misses the band. Plain floats: vector maths through .NET
    costs more than the whole calculation."""
    ox, oy, ax, ay, nx, ny = origin.X, origin.Y, along.X, along.Y, normal.X, normal.Y
    poly = [((x - ox) * ax + (y - oy) * ay, (x - ox) * nx + (y - oy) * ny) for x, y in corners]
    for lim, sign in ((v0 + EPS, 1.0), (v1 - EPS, -1.0)):
        out = []
        for i in range(len(poly)):
            p, q = poly[i - 1], poly[i]
            fp, fq = sign * (p[1] - lim), sign * (q[1] - lim)
            if (fp >= 0) != (fq >= 0):
                k = fp / (fp - fq)
                out.append((p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k))
            if fq >= 0:
                out.append(q)
        poly = out
        if not poly:
            return None
    return max(x for x, _ in poly)


def divide(c, w, fit, sliver):
    """Points along one smooth stretch, both ends included, about w apart (chords)."""
    L = c.GetLength()
    a, b = c.PointAtStart, c.PointAtEnd
    full = list(c.DivideEquidistant(w) or []) or [a]
    rem = full[-1].DistanceTo(b)
    if fit == "equal divide":
        n = max(3 if c.IsClosed else 1, int(round(len(full) - 1 + rem / w)))
        pts = [c.PointAt(t) for t in c.DivideByCount(n, True)]
        if c.IsClosed:
            pts.append(b)
    elif fit == "centred" and rem > EPS and len(full) > 1:
        ok, t_last = c.ClosestPoint(full[-1])
        left = L - c.GetLength(rg.Interval(c.Domain.T0, t_last))
        ok, ts = c.LengthParameter(left / 2)
        sub = c.Trim(ts, c.Domain.T1) if ok else None
        inner = list(sub.DivideEquidistant(w) or []) if sub else []
        pts = [a] + (inner or [c.PointAtLength(left / 2)]) + [b]
    else:
        pts = full + [b]
    out = [pts[0]]
    for p in pts[1:]:
        if p.DistanceTo(out[-1]) > EPS:
            out.append(p)
    if len(out) > 2 and out[-1].DistanceTo(out[-2]) < sliver:
        del out[-2]
    if len(out) > 2 and out[1].DistanceTo(out[0]) < sliver:
        del out[1]
    return out


def stretches(c):
    """The path split at its corners (G1 kinks); a closed path starts at a corner when it has one."""
    def kinks():
        d, ts, t = c.Domain, [], c.Domain.T0
        for _ in range(10000):
            ok, t = c.GetNextDiscontinuity(rg.Continuity.G1_continuous, t, d.T1)
            if not ok or t >= d.T1 - 1e-9:
                break
            ts.append(t)
        return ts
    ks = kinks()
    if c.IsClosed and ks and c.TangentAtStart * c.TangentAtEnd > 0.9998:  # a smooth seam: start at a corner instead
        c.ChangeClosedCurveSeam(ks[0])
        ks = kinks()
    if not ks:
        return [c]
    ts = [c.Domain.T0] + ks + [c.Domain.T1]
    return [s for s in (c.Trim(ts[i], ts[i + 1]) for i in range(len(ts) - 1)) if s is not None and s.GetLength() > EPS]


# ── inputs ──
g = number(glazing, 28, 4, 80) / 1000.0
w_m, d_m = number(width, 50, 10, 400), number(depth, 150, 20, 1000)
mull = member_type(profile, w_m, d_m, g)
tp = str(transom_profile or "same").strip().lower()
tran = member_type(mull["profile"] if tp in ("", "same") else tp, number(transom_width, 0, 0, 400) or w_m,
                   number(transom_depth, 0, 0, 1000) or d_m, g)
jamb, sill = channel(mull, g), channel(tran, g)
fit = str(fit or "equal divide").strip().lower()
fit = fit if fit in ("equal divide", "fixed from start", "centred") else "equal divide"
truss_kind = str(truss or "none").strip().lower()
truss_kind = truss_kind if truss_kind in ("warren", "pratt", "howe", "vierendeel") else "none"
off = number(offset, 0, -50, 50)

# ── levels and heights (m) ──
wired = bim.read(levels, "level")
lv = [dict(r, elevation=float(r.get("elevation") or 0.0)) for r in bim.levels_of(levels)]  # tolerate sparse records


def find_level(ref, fallback):
    s = str(ref if ref is not None else "").strip()
    if not s:
        return fallback
    r = bim.level_at(lv, s)
    if r is None:
        notes.append("no level %r: using %s" % (s, fallback.get("name")))
        return fallback
    return r


base_lv = find_level(base, lv[0])
z0m = float(base_lv["elevation"])
top_lv = None
if height and height > 0:
    z1m = z0m + float(height)
else:
    top_lv = find_level(top, lv[-1])
    z1m = float(top_lv["elevation"])
    if z1m <= z0m + 0.1:
        z1m = bim.level_top(lv, base_lv)
        notes.append("top level %s is not above the base: one storey (%.2f m)" % (top_lv.get("name"), z1m - z0m))
        top_lv = bim.level_above(lv, base_lv)
H = z1m - z0m
spanned = [r for r in lv if z0m - 1e-6 <= float(r["elevation"]) <= z1m + 1e-6]
breaks = [float(r["elevation"]) for r in lv if z0m + 1e-6 < float(r["elevation"]) < z1m - 1e-6]

# transom heights: levels first (they win when two are too close), then the typed heights
vals, reps = parse_heights(transom_heights)
bounds = [z0m] + breaks + [z1m] if str(measure or "").strip().lower() != "wall base" else [z0m, z1m]
lo, hi = z0m + sill["b"] + tran["b"] / 2 + 0.02, z1m - sill["b"] - tran["b"] / 2 - 0.02
gap = tran["b"] + 0.05  # at least 50 mm of glass between transoms
for s in [s for s in reps if s < gap]:
    notes.append("transom spacing @%g is tighter than the transom: @%.2f" % (s, gap))
reps = [max(s, gap) for s in reps]
typed, used = [], set()
for a, b in zip(bounds, bounds[1:]):  # each height stays inside its own storey
    for v in vals:
        z = a + v if v >= 0 else b + v
        if a < z < b and lo < z < hi:
            typed.append(z)
            used.add(v)
    for s in reps:
        typed += [a + s * k for k in range(1, min(int((b - a) / s - 1e-9) + 1, 5000))]
missed = [v for v in vals if v not in used]
if missed:
    notes.append("transom heights outside the %s ignored: %s" % ("storeys" if len(bounds) > 2 else "wall", ", ".join("%g" % v for v in missed)))


def accept(cands, taken):
    taken = sorted(taken)
    for z in sorted(cands):
        i = bisect.bisect_left(taken, z)
        if lo < z < hi and all(abs(z - taken[j]) >= gap - 1e-6 for j in (i - 1, i) if 0 <= j < len(taken)):
            taken.insert(i, z)
    return taken


level_rows = accept(breaks if at_levels else [], [])
rows = accept(typed, list(level_rows))

# ── path ──
src = path if isinstance(path, rg.Curve) else None
if src is None:
    if path is not None:
        notes.append("path is not a curve: sample line")
    src = rg.LineCurve(rg.Point3d(0, 0, 0), rg.Point3d(12 * u, 0, 0))
crv = rg.Curve.ProjectToPlane(src, rg.Plane.WorldXY) or src.DuplicateCurve()
closed = crv.IsClosed
side = 1.0
if closed and crv.ClosedCurveOrientation(Z) == rg.CurveOrientation.Clockwise:
    side = -side
if flip:
    side = -side
length = crv.GetLength()

# performance guard: few enough cells, mullions and rows
w = number(panel, 1.5, 0.01, 1000.0)
w_min = mull["b"] + 0.05
if w < w_min:
    notes.append("panel %.2f m is narrower than the mullion: %.2f m" % (w, w_min))
    w = w_min
if (len(rows) + 1) * (length / u / w) > MAX_CELLS and len(rows) > len(level_rows):
    notes.append("%d transoms x %d columns is too many: kept the level transoms only" % (len(rows), length / u / w))
    rows = list(level_rows)
w_cap = max(length / u / MAX_MULLIONS, length / u * (len(rows) + 1) / MAX_CELLS)
if w < w_cap:
    notes.append("panel %.2f m would make too many members: widened to %.2f m" % (w, w_cap))
    w = w_cap
sliver = min(max(mull["b"] + 0.05, 0.1), w / 2) * u

pts = []
for s in stretches(crv):
    sp = divide(s, w * u, fit, sliver)
    if pts and pts[-1].DistanceTo(sp[0]) < EPS:
        sp = sp[1:]
    pts += sp
if closed and len(pts) > 1 and pts[-1].DistanceTo(pts[0]) < EPS:
    pts.pop()

mullions, transoms, frame, glass, trusses, plan, grid, panels, records = [], [], [], [], [], [], [], [], []
lengths = {"mullions": 0.0, "transoms": 0.0, "frame": 0.0, "trusses": 0.0}
glass_area, cells, widths = 0.0, 0, []
wid = "CW-" + hashlib.md5(json.dumps(bim.curve_record(crv)).encode()).hexdigest()[:4]

N = len(pts)
nf = N if closed else N - 1
if length < 0.2 * u or nf < 1 or H < 0.1:
    notes.append("nothing to build: path %.2f m, height %.2f m" % (length / u, H))
    nf = 0

# ── facets and mullion frames (model units, z = 0) ──
facets = []
for f in range(nf):
    a, b = pts[f], pts[(f + 1) % N]
    t = b - a
    t.Unitize()
    n = rg.Vector3d.CrossProduct(t, Z) * side
    facets.append((t, n))
frames = []  # (glazing point G, tangent, normal, mitre factor)
for i in range(N if nf else 0):
    prv = facets[i - 1] if (closed or i > 0) else None
    nxt = facets[i] if (closed or i < N - 1) else None
    tv = (prv[0] if prv else rg.Vector3d.Zero) + (nxt[0] if nxt else rg.Vector3d.Zero)
    nv = (prv[1] if prv else rg.Vector3d.Zero) + (nxt[1] if nxt else rg.Vector3d.Zero)
    if nv.Length < 1e-9:  # the path doubles back
        tv, nv = rg.Vector3d((nxt or prv)[0]), rg.Vector3d((nxt or prv)[1])
    tv.Unitize()
    nv.Unitize()
    k = 1.0 / max(nv * (nxt or prv)[1], 0.25)
    frames.append((pts[i] + nv * (off * u * k), tv, nv, k))

z0, z1 = z0m * u, z1m * u
zcut = z0 + number(cut, 1.0, -1e6, 1e6) * u
if z0 - EPS <= zcut <= z1 + EPS:  # a cut right at the base or the top still cuts, just inside
    zcut = min(max(zcut, z0 + 10 * EPS), z1 - 10 * EPS)
cutting = z0 < zcut < z1
if nf and not cutting:
    notes.append("plan cut at +%.2f m is outside the wall" % ((zcut - z0) / u))
is_jamb = [not closed and (i == 0 or i == N - 1) for i in range(len(frames))]
_templates, _outlines = {}, {}


def make(spec, s, e, up):
    """A member solid from s to e (model units), section depth along `up`. Each section and length is built once and
    copied into place, several times faster than building every member. The plan outline is the section carried along
    the member's axis onto the cut plane (cutting solids with a plane costs more than building them)."""
    axis = e - s
    L = axis.Length
    if L < tol:
        return None
    sk = json.dumps(spec, sort_keys=True)
    key = (sk, int(round(L / u * 1e5)))
    if key not in _templates:  # a member from the origin up +Z, depth along +Y: its profile is section() as drawn
        _templates[key] = bim.member(rg.Point3d.Origin, rg.Point3d(0, 0, L), spec, up=rg.Vector3d.YAxis)
    tb = _templates[key]
    axis.Unitize()
    x = rg.Vector3d.CrossProduct(up, axis)
    if tb is None or x.Length < 1e-9:
        return None
    xf = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, rg.Plane(s, x, up))
    b = tb.DuplicateBrep()
    b.Transform(xf)
    if cutting and abs(axis.Z) > 1e-6:
        if min(s.Z, e.Z) < zcut < max(s.Z, e.Z):
            if sk not in _outlines:
                _outlines[sk] = bim.section(spec)
            to_cut = rg.Transform.ProjectAlong(rg.Plane(rg.Point3d(0, 0, zcut), Z), axis) * xf
            for c in _outlines[sk]:
                c = c.DuplicateCurve()
                c.Transform(to_cut)
                plan.append(c)
    elif cutting:  # level members (transoms) only when the cut runs through one
        bb = b.GetBoundingBox(False)
        if bb.Min.Z < zcut < bb.Max.Z:
            ok, crvs, _ = rg.Intersect.Intersection.BrepPlane(b, rg.Plane(rg.Point3d(0, 0, zcut), Z), tol)
            if ok and crvs:
                plan.extend(rg.Curve.JoinCurves(crvs, tol) or crvs)
    return b


def mullion_rect(i):
    """Footprint corners of mullion i, its centre and its type."""
    G, tv, nv, k = frames[i]
    mt = jamb if is_jamb[i] else mull
    A = G + nv * (mt["c"] * u * k)
    hb, hd = mt["b"] * u / 2, mt["d"] * u / 2
    return [A + tv * x + nv * y for x, y in ((-hb, -hd), (hb, -hd), (hb, hd), (-hb, hd))], A, mt


rects = [mullion_rect(i) for i in range(len(frames))]
flat_rects = [[(p.X, p.Y) for p in r[0]] for r in rects]
_insets = {}


def inset(i, f, at_start, v0, v1, gap_m):
    """Where something in the depth band v0..v1 (m, from the facet's glazing line) must stop short of mullion i on
    facet f: the mullion's face, or the mitre at a corner, or a joint."""
    key = (i, f, at_start, v0, v1, gap_m)
    if key not in _insets:
        G, tv, nv, k = frames[i]
        t, n = facets[f]
        along = t if at_start else -t
        reach = band_extent(flat_rects[i], G, along, n, v0 * u, v1 * u)
        cn, ca = nv * n, nv * along
        m = [v0 * u * ca / cn, v1 * u * ca / cn] if cn > 1e-6 else [0.0, 0.0]
        if reach is None and abs(ca) > 1e-6:
            # a corner with nothing in the band (frameless glass, fins behind): butt joint, the piece ending here runs
            # past the corner and the one starting here stops at its face plus the joint
            _insets[key] = max(m) + 2 * gap_m * u if at_start else min(m)
        else:
            _insets[key] = max(reach if reach is not None else gap_m * u, max(m), gap_m * u)
    return _insets[key]


def overlaps(t):
    return t["c"] - t["d"] / 2 < g / 2 - 1e-6 and t["c"] + t["d"] / 2 > -g / 2 + 1e-6


def add(target, brep, length_key, length_m):
    if brep is not None:
        target.append(brep)
        lengths[length_key] += length_m


# mullions and jambs: full height
for i in range(len(frames)):
    corners, A, mt = rects[i]
    brep = make(mt["spec"], A + Z * z0, A + Z * z1, frames[i][2])
    add(frame if is_jamb[i] else mullions, brep, "frame" if is_jamb[i] else "mullions", H)

# horizontal lines: (z of the grid line, member type, centre z, glass stop below, glass stop above)
lines = [(z0m, sill, z0m + sill["b"] / 2, 0.0, sill["b"] if overlaps(sill) else JOINT / 2)]
for z in rows:
    half = tran["b"] / 2 if overlaps(tran) else JOINT / 2
    lines.append((z, tran, z, half, half))
lines.append((z1m, sill, z1m - sill["b"] / 2, sill["b"] if overlaps(sill) else JOINT / 2, 0.0))


def storey_at(z):
    below = [r for r in lv if float(r["elevation"]) <= z + 1e-6]
    return below[-1].get("name") if below else None


for f in range(nf):
    ia, ib = f, (f + 1) % N
    Ga, Gb = frames[ia][0], frames[ib][0]
    t, n = facets[f]
    Lf = Ga.DistanceTo(Gb)
    widths.append(Lf / u)
    t = Gb - Ga
    t.Unitize()
    # transoms, sills and heads between the mullions' faces
    for li, (zg, tt, zm, _, _) in enumerate(lines):
        v0, v1 = tt["c"] - tt["d"] / 2, tt["c"] + tt["d"] / 2
        ua, ub = inset(ia, f, True, v0, v1, 0.0), inset(ib, f, False, v0, v1, 0.0)
        if Lf - ua - ub < EPS:
            continue
        P = Ga + n * (tt["c"] * u) + Z * (zm * u)
        brep = make(tt["spec"], P + t * ua, P + t * (Lf - ub), n)
        edge = li in (0, len(lines) - 1)
        add(frame if edge else transoms, brep, "frame" if edge else "transoms", (Lf - ua - ub) / u)
    # glass and cell surfaces
    ga, gb = inset(ia, f, True, -g / 2, g / 2, JOINT / 2), inset(ib, f, False, -g / 2, g / 2, JOINT / 2)
    plane = rg.Plane(Ga, t, Z)  # x along the facet, y up, z = t x Z
    for r in range(len(lines) - 1):
        (za, _, _, _, up_a), (zb, _, _, dn_b, _) = lines[r], lines[r + 1]
        lo_z, hi_z = (za + up_a) * u, (zb - dn_b) * u
        pane = bim.box(plane, ga, Lf - gb, lo_z, hi_z, -g / 2 * u, g / 2 * u)
        corners = [Ga + Z * (za * u), Gb + Z * (za * u), Gb + Z * (zb * u), Ga + Z * (zb * u)]
        if side < 0:
            corners = [corners[0], corners[3], corners[2], corners[1]]
        cell = rg.Brep.CreateFromCornerPoints(corners[0], corners[1], corners[2], corners[3], tol)
        if cell is not None:
            panels.append(cell)
        gw, gh = max(0.0, (Lf - ga - gb) / u), max(0.0, (hi_z - lo_z) / u)
        if pane is not None:
            glass.append(pane)
            glass_area += gw * gh
            if cutting and lo_z < zcut < hi_z:
                q = [Ga + t * x + n * (y * u) + Z * zcut for x, y in ((ga, -g / 2), (Lf - gb, -g / 2), (Lf - gb, g / 2), (ga, g / 2), (ga, -g / 2))]
                plan.append(rg.PolylineCurve(q))
        cells += 1
        records.append({"alien": "curtain_panel", "id": "%s/%d.%d" % (wid, f + 1, r + 1), "wall": wid, "column": f + 1,
                        "row": r + 1, "storey": storey_at((za + zb) / 2), "width": Lf / u, "height": zb - za,
                        "glass": [gw, gh] if pane is not None else None, "area": gw * gh if pane is not None else 0.0,
                        "corners": [bim.pt(p, u) for p in corners], "normal": [round(n.X, 6) + 0.0, round(n.Y, 6) + 0.0, 0.0]})

tallest = max([lines[r + 1][0] - lines[r][0] for r in range(len(lines) - 1)] or [0])
if nf and tallest > 6:
    notes.append("panes up to %.1f m tall: add transom heights (e.g. '@3')" % tallest)

# ── trusses behind the mullions ──
truss_note = ""
if truss_kind != "none" and frames:
    cs = parse_section(chord, "box 100x100", "chord")
    ws = parse_section(web, "round 60", "web")
    p = number(truss_depth, 0.8, 0.05, 50)
    bays = max(1, int(round(H / p)))
    if truss_kind != "vierendeel" and bays % 2:
        bays += 1
    if bays > MAX_BAYS:
        notes.append("%d truss bays: capped at %d" % (bays, MAX_BAYS))
        bays = MAX_BAYS
    zk = [z0m + H * k / bays for k in range(bays + 1)]
    webs = []  # (outer?, z) -> (outer?, z) pairs in metres
    if truss_kind == "warren":
        webs = [((k % 2 == 0, zk[k]), ((k + 1) % 2 == 0, zk[k + 1])) for k in range(bays)]
        webs += [((True, zk[0]), (False, zk[0])), ((True, zk[-1]), (False, zk[-1]))]
    else:
        webs = [((True, z), (False, z)) for z in zk]  # struts (a truss's "verticals")
        for k in range(bays if truss_kind != "vierendeel" else 0):
            lower = k < bays / 2  # diagonals mirror about mid-height
            if truss_kind == "pratt":
                webs.append(((True, zk[k]), (False, zk[k + 1])) if lower else ((True, zk[k + 1]), (False, zk[k])))
            else:
                webs.append(((False, zk[k]), (True, zk[k + 1])) if lower else ((False, zk[k + 1]), (True, zk[k])))
    per = 2 + len(webs)
    step = max(1, int(math.ceil(len(frames) * per / float(MAX_TRUSS))))
    if step > 1:
        notes.append("%d trusses of %d members is too many: one every %d mullions" % (len(frames), per, step))
    for i in range(0, len(frames), step):
        corners, A, mt = rects[i]
        G, tv, nv, k = frames[i]
        outer = A + nv * (-(mt["d"] + cs["d"]) / 2 * u)
        inner = outer + nv * (-p * u)
        add(trusses, make(cs, outer + Z * z0, outer + Z * z1, nv), "trusses", H)
        add(trusses, make(cs, inner + Z * z0, inner + Z * z1, nv), "trusses", H)
        for (oa, za), (ob, zb) in webs:
            s = (outer if oa else inner) + Z * (za * u)
            e = (outer if ob else inner) + Z * (zb * u)
            axis = e - s
            upv = rg.Vector3d.CrossProduct(axis, tv)
            if upv.Length < 1e-9:
                continue
            upv.Unitize()
            add(trusses, make(ws, s, e, upv), "trusses", axis.Length / u)
    truss_note = "%s trusses %.2f m deep, %d bays" % (truss_kind, p, bays)

# ── grid ──
for G, _, _, _ in frames:
    grid.append(rg.LineCurve(G + Z * z0, G + Z * z1))
for zg, _, _, _, _ in lines:
    ring = [G + Z * (zg * u) for G, _, _, _ in frames]
    if closed and ring:
        ring.append(ring[0])
    if len(ring) > 1:
        grid.append(rg.PolylineCurve(ring))

# ── outputs ──
mat_m = "steel" if mull["profile"].lower() in STEEL else "aluminium"
mat_t = "steel" if tran["profile"].lower() in STEEL else "aluminium"
solids = mullions + transoms + frame + glass + trusses
colours = ([bim.colour(mat_m)] * len(mullions) + [bim.colour(mat_t)] * len(transoms) + [bim.colour("aluminium")] * len(frame)
           + [bim.colour("glass", 110)] * len(glass) + [bim.colour("steel")] * len(trusses))

base_rec = crv.DuplicateCurve()
base_rec.Translate(Z * z0)
wall = bim.pack(
    "curtain_wall", id=wid, path=bim.curve_record(base_rec, u), closed=closed, exterior="right" if side > 0 else "left",
    offset=off, base=base_lv.get("name"), top=top_lv.get("name") if top_lv else None, elevation=z0m, height=H,
    levels=[r.get("name") for r in spanned], sample_levels=not wired, panel_width=float(panel or 0), panel_used=w, fit=fit,
    columns=nf, rows=len(lines) - 1 if nf else 0, transoms=[round(z - z0m, 4) for z in rows],
    transom_rule={"heights": str(transom_heights or ""), "measure": str(measure or "each storey"), "at_levels": bool(at_levels)},
    mullion={"profile": mull["profile"], "width": round(mull["b"] * 1000, 1), "depth": round(mull["d"] * 1000, 1)},
    transom={"profile": tran["profile"], "width": round(tran["b"] * 1000, 1), "depth": round(tran["d"] * 1000, 1)},
    glass=round(g * 1000, 1), truss={"type": truss_kind, "depth": number(truss_depth, 0.8, 0.05, 50), "chord": str(chord), "web": str(web)} if truss_kind != "none" else None,
    panels=cells, glass_area=glass_area, lengths=lengths)
records = [wall] + [bim.dump(r) for r in records]

span = "%.2f" % min(widths) if widths and max(widths) - min(widths) < 0.005 else "%.2f-%.2f" % (min(widths), max(widths)) if widths else "-"
info = "%s: %.2f m long x %.2f m high (%s to %s%s); %d columns x %d rows = %d panels, %s m wide (%s); %d mullions, %d jambs" % (
    wid, length / u, H, base_lv.get("name"), top_lv.get("name") if top_lv else "+%.2f m" % H, ", sample levels" if not wired else "",
    nf, len(lines) - 1 if nf else 0, cells, span, fit, len(mullions), sum(is_jamb))
info += "\nglass %.1f m2; mullions %.1f m, transoms %.1f m, frame %.1f m%s" % (
    glass_area, lengths["mullions"], lengths["transoms"], lengths["frame"], ", trusses %.1f m" % lengths["trusses"] if trusses else "")
info += "\n%s %gx%g mm, transoms %s %gx%g mm, glass %g mm%s" % (
    mull["profile"], mull["b"] * 1000, mull["d"] * 1000, tran["profile"], tran["b"] * 1000, tran["d"] * 1000, g * 1000,
    "; " + truss_note if truss_note else "")
if notes:
    info += "\n" + "\n".join("note: " + s for s in notes)
