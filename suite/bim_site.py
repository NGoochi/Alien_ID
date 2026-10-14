# NODE_INPUTS: name:str | "project name" default="Project", boundary:list[Curve] | "site boundary, e.g. vic_site_map's site (optional; several lots are joined)"
# NODE_INPUTS: origin:Point3d | "project base point (optional; else set by anchor)", anchor:str | "where the origin goes when none is wired" default="corner" options="corner|centre|world origin"
# NODE_INPUTS: rotation:float | "project grid rotation (deg, CCW from world X); with align, turns the aligned grid further" min=-180 max=180 default=0 decimals=1
# NODE_INPUTS: align:str | "take the grid direction from the boundary" default="off" options="off|long axis|longest edge"
# NODE_INPUTS: north:float | "true north (deg, CCW from world +Y; 0 for vic_site_map)" min=-180 max=180 default=0 decimals=1
# NODE_INPUTS: datum:float | "AHD (m) of model z = 0: wire vic_site_map's datum" min=-20 max=2000 default=0 decimals=3
# NODE_INPUTS: scale:str | "drawing scale for the suite's annotation (levels, grids, symbols)" default="1:100" options="1:50|1:100|1:200|1:500|1:1000"
# NODE_OUTPUTS: site:str | "site record: wire into the building nodes' site input", plane:Plane | "project plane: at the origin, X along the grid"
# NODE_OUTPUTS: boundary:list[Curve] | "the joined site boundary", arrow:list[Curve] | "north point (true north; a PN tick when the grid is turned)"
# NODE_OUTPUTS: marker:list[Curve] | "project base point: circle, cross, X/Y axes, datum", info:str
# CATEGORY: Building elements
# BIM site: the project's base for the building suite: name, base point, grid rotation, true north and AHD datum as a site record, with north point and base point symbols.
#
# Every building node (levels, grids, walls ...) reads this record through its `site` input: the origin and rotation
# set the project plane (grids start at the origin and run along its X), datum turns elevations into RLs, and scale
# sizes annotation. Without a wired origin it goes to the boundary's lower-left corner in the project's axes, so grids
# and levels drawn from it land on the lot (vic_site_map centres the lot on the world origin, which would leave a
# grid from there covering a quarter of it). "centre" uses the lot's centroid, "world origin" keeps 0,0,0.
import math

import Rhino.Geometry as rg

import alien_bim as ab

u = ab.units()
tol = ab.tol()
jtol = max(tol, 0.001 * u)  # joining lot lines: 1 mm
notes = []

try:
    den = max(1, int(str(scale or "1:100").split(":")[-1]))
except ValueError:
    den = 100
paper = den / 1000.0 * u  # model units per paper millimetre

# ── boundary: join lots into one outline where they touch ──

curves = [c for c in (boundary or []) if isinstance(c, rg.Curve) and c.IsValid]
closed = [c for c in curves if c.IsClosed]
if not closed and curves:
    closed = [c for c in rg.Curve.JoinCurves(curves, jtol) if c.IsClosed]
if len(closed) > 1:
    union = rg.Curve.CreateBooleanUnion(closed, jtol)
    if union:
        closed = list(union)


def area(c):
    amp = rg.AreaMassProperties.Compute(c) if c.IsPlanar(jtol) else None
    return amp.Area if amp else 0.0


closed.sort(key=area, reverse=True)
outline = closed or sorted(curves, key=lambda c: c.GetLength(), reverse=True)
main = outline[0] if outline else None
if len(closed) > 1:
    notes.append("%d separate outlines: the record keeps the largest" % len(closed))
elif curves and not closed:
    notes.append("the boundary is open: used for its extents only")


def points_of(c):
    ok, pl = c.TryGetPolyline()
    if ok:
        return list(pl)
    return [c.PointAt(t) for t in c.DivideByCount(96, True)]


pts = [p for c in outline for p in points_of(c)]


def hull(xy):
    """Convex hull (monotone chain) of (x, y) pairs, counter-clockwise."""
    xy = sorted(set(xy))
    if len(xy) < 3:
        return xy

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h[:-1]

    return half(xy) + half(reversed(xy))


def long_axis():
    """Direction of the long side of the smallest rectangle around the boundary (rad)."""
    hp = hull([(round(p.X, 6), round(p.Y, 6)) for p in pts])

    def box_at(a):
        cs, sn = math.cos(a), math.sin(a)
        us = [x * cs + y * sn for x, y in hp]
        vs = [-x * sn + y * cs for x, y in hp]
        return max(us) - min(us), max(vs) - min(vs)

    best = None  # the smallest box has a side on a hull edge
    for i in range(len(hp)):
        (x1, y1), (x2, y2) = hp[i], hp[(i + 1) % len(hp)]
        if (x2 - x1) ** 2 + (y2 - y1) ** 2 < tol * tol:
            continue
        a = math.atan2(y2 - y1, x2 - x1)
        w, h = box_at(a)
        if best is None or w * h < best[0] - 1e-9:
            best = (w * h, a, a if w >= h else a + math.pi / 2)
    if not best:
        return None
    # round (a circle, a regular polygon): boxes at other angles fit about as well, so there is no axis
    if all(box_at(best[1] + k * math.pi / 8)[0] * box_at(best[1] + k * math.pi / 8)[1] < best[0] * 1.02 for k in (1, 2, 3)):
        notes.append("the boundary is round: no long axis, grid not turned")
        return None
    return best[2]


def longest_edge():
    segs = [s for s in (main.DuplicateSegments() or []) if s.IsLinear(jtol)] if main else []
    if not segs:
        return None
    s = max(segs, key=lambda s: s.GetLength())
    d = s.PointAtEnd - s.PointAtStart
    return math.atan2(d.Y, d.X)


def half_turn(a):
    """An axis (rad) folded into (-45, 135] degrees: project X points east-ish, or north-ish for a north-south lot,
    so near-vertical lots don't flip between pointing north and south with a small change in shape."""
    while a <= -math.pi / 4 + 1e-9:
        a += math.pi
    while a > 3 * math.pi / 4 + 1e-9:
        a -= math.pi
    return a


angle = math.radians(rotation or 0.0)
mode = str(align or "off")
if mode != "off":
    if not pts:
        notes.append("align needs a boundary: rotation used as is")
    else:
        a = longest_edge() if mode == "longest edge" else None
        if a is None and mode == "longest edge":
            notes.append("no straight edge on the boundary: aligned to its long axis")
        a = long_axis() if a is None else a
        angle += half_turn(a) if a is not None else 0.0
rot = math.degrees(math.atan2(math.sin(angle), math.cos(angle)))  # (-180, 180]
ex = rg.Vector3d(math.cos(angle), math.sin(angle), 0)
ey = rg.Vector3d(-math.sin(angle), math.cos(angle), 0)

# ── origin ──

if origin is not None:
    o = rg.Point3d(origin)
    how = "wired"
elif pts and anchor == "corner":
    us = [p.X * ex.X + p.Y * ex.Y for p in pts]
    vs = [p.X * ey.X + p.Y * ey.Y for p in pts]
    o = rg.Point3d(0, 0, min(p.Z for p in pts)) + ex * min(us) + ey * min(vs)
    how = "boundary corner"
elif pts and anchor == "centre":
    amp = rg.AreaMassProperties.Compute(main) if main and main.IsClosed and main.IsPlanar(jtol) else None
    c0 = amp.Centroid if amp else rg.BoundingBox(pts).Center
    o = rg.Point3d(c0.X, c0.Y, min(p.Z for p in pts))
    how = "boundary centre"
else:
    o = rg.Point3d.Origin
    how = "world origin"
plane = rg.Plane(o, ex, ey)

# extents of the boundary in the project frame, from the origin (m)
if pts:
    us = [(p - o) * ex for p in pts]
    vs = [(p - o) * ey for p in pts]
    extents = [round(v / u, 6) + 0.0 for v in (min(us), min(vs), max(us), max(vs))]  # + 0.0: no -0.0 in the record
else:
    extents = None
lot_area = sum(area(c) for c in closed) / (u * u)

site = ab.pack("site", name=str(name or "Project"), origin=ab.pt(o, u), rotation=rot, north=float(north or 0.0),
               datum=float(datum or 0.0), boundary=ab.curve_record(main, u) if main else None, scale=den,
               area=round(lot_area, 2), extents=extents)
boundary = outline

# ── north point: true north from the north angle, beside the boundary's top-right corner ──

na = math.radians(north or 0.0)
nv = rg.Vector3d(-math.sin(na), math.cos(na), 0)   # true north
ev = rg.Vector3d(nv.Y, -nv.X, 0)                    # true east
R = 10 * paper                                      # 20 mm across on paper
box = rg.BoundingBox(pts) if pts else rg.BoundingBox(o - rg.Vector3d(5 * u, 5 * u, 0), o + rg.Vector3d(5 * u, 5 * u, 0))
c = rg.Point3d(box.Max.X + 2 * R, box.Max.Y + 2 * R, o.Z)


def pl(*ps):
    return rg.PolylineCurve([rg.Point3d(p) for p in ps])


tip, notch = c + nv * R * 1.15, c - nv * R * 0.35
left, right = c - ev * R * 0.5 - nv * R * 0.75, c + ev * R * 0.5 - nv * R * 0.75
arrow = [rg.Circle(rg.Plane(c, rg.Vector3d.ZAxis), R).ToNurbsCurve(),
         pl(tip, left, notch, right, tip),     # the dart
         pl(tip, notch)]                       # splits it into a dark and a light half
arrow += ab.text_curves("N", rg.Plane(tip + nv * R * 0.35, ev, nv), R * 0.45, "center", "bottom")
pn = math.radians(rot)
pnv = rg.Vector3d(-math.sin(pn), math.cos(pn), 0)  # project north = the grid's +Y
if abs(math.degrees(math.atan2(nv.X * pnv.Y - nv.Y * pnv.X, nv * pnv))) > 0.5:
    pe = rg.Vector3d(pnv.Y, -pnv.X, 0)
    arrow += [pl(c, c + pnv * R * 1.5)]
    arrow += ab.text_curves("PN", rg.Plane(c + pnv * R * 1.6, pe, pnv), R * 0.25, "center", "bottom")

# ── project base point: circle and cross along the grid, X/Y axis arrows, datum ──

r = 3 * paper
th = 2.5 * paper
marker = [rg.Circle(plane, r).ToNurbsCurve(), pl(o - ex * r * 1.5, o + ex * r * 1.5), pl(o - ey * r * 1.5, o + ey * r * 1.5)]
for axis, side, label in ((ex, ey, "X"), (ey, -ex, "Y")):
    end = o + axis * r * 4
    marker += [pl(o + axis * r, end), pl(end - axis * r * 0.6 + side * r * 0.3, end, end - axis * r * 0.6 - side * r * 0.3)]
    marker += ab.text_curves(label, rg.Plane(end + axis * th * 0.9, ex, ey), th)
dval = (datum or 0.0) + o.Z / u
marker += ab.text_curves("RL %.3f" % dval if datum else "±0.000" if abs(dval) < 5e-4 else "%+.3f" % dval,
                         rg.Plane(o + ex * r * 1.3 - ey * r * 1.3, ex, ey), th, "left", "top")

info = "%s: origin %.2f, %.2f, %.2f m (%s), grid rotation %.1f deg%s, true north %.1f deg, datum %s, scale 1:%d" % (
    name or "Project", o.X / u, o.Y / u, o.Z / u, how, rot, " (aligned to the %s)" % mode if mode != "off" and pts else "",
    north or 0.0, "RL %.3f m AHD" % datum if datum else "0 (relative levels)", den)
if extents:
    info += "\nboundary %.1f x %.1f m in the project axes%s" % (
        extents[2] - extents[0], extents[3] - extents[1], ", area %.1f m2" % lot_area if lot_area else "")
if notes:
    info += "\n" + "\n".join(notes)
