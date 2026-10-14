# NODE_INPUTS: geometry:tree[Geometry] | "parts: one branch per group (e.g. per level), or a flat list exploded part by part; unwired: a sample building"
# NODE_INPUTS: mode:str | "how the groups move apart" default="vertical stack" options="vertical stack|height bands|along vector|radial"
# NODE_INPUTS: spacing:float | "gap opened between neighbouring groups (m); radial: how far the outermost group moves" min=0 max=50 default=3
# NODE_INPUTS: axis:Vector3d | "direction for 'along vector' (unwired: world Z)", band:float | "band height for 'height bands' (m)" min=0.1 max=50 default=3.2
# NODE_INPUTS: keep_base:bool | "the first (lowest) group stays where it is" default=true, beside:bool | "draw the result beside the original (along +X) instead of over it" default=false
# NODE_INPUTS: leaders:str | "dashed explosion lines between where each group was and where it is" default="corners" options="none|corners|centres"
# NODE_INPUTS: names:list[str] | "a label per group, drawn beside it (in branch order; height bands: bottom up)"
# NODE_INPUTS: angle:str | "isometric view from" default="SW" options="SW|SE|NE|NW"
# NODE_INPUTS: display:str | "display mode saved with the named view" default="Shaded", name:str | "named view name" default="Alien axo · Exploded"
# NODE_INPUTS: view:bool | "create (or replace) a parallel isometric named view framing the result" button
# NODE_OUTPUTS: exploded | "the parts moved apart, in the same tree structure as geometry", lines:list[Curve] | "dashed explosion lines", labels:list[Curve] | "group labels as curves, facing the view"
# NODE_OUTPUTS: transforms:list[Transform] | "the move of each group (branch order; a flat list: part order; height bands: bottom up)", info:str
# CATEGORY: Views & drawings
# Exploded axo: pulls a building or object apart by group (stacked, by height bands, along a vector or radially), with dashed explosion lines, labels and an isometric named view.
#
# Each branch is a group (a flat list: each part, or height bands of a set height). Stacked and vector modes open a
# `spacing` gap between neighbours in order (lowest first), so a building's floors lift off one another; radial
# moves groups away from the centre in proportion to their distance (the outermost by `spacing`). Explosion lines
# join matching corners (or centres) across each gap. The view button saves a parallel isometric named view that
# frames the result; it replaces the view saved under the same name.
import math

import Rhino
import Rhino.Geometry as rg

import alien_bim as ab
import alien_views as av

u = ab.units()
tol = ab.tol()
d = Rhino.RhinoDoc.ActiveDoc
notes = []
Z = rg.Vector3d.ZAxis

# ── input: branches of geometry ──

branches = [[av.as_geometry(g) for g in (br or [])] for br in (geometry or [])]
if not any(g is not None for br in branches for g in br):  # sample: three storeys and a roof, one branch each
    branches = []
    for i in range(3):
        z = i * 3.2
        br = [ab.box(rg.Plane.WorldXY, 0, 16 * u, 0, 10 * u, (z - 0.2) * u, z * u)]
        br += [ab.box(rg.Plane.WorldXY, a * u, b * u, c * u, e * u, z * u, (z + 3.0) * u)
               for a, b, c, e in ((0, 16, 0, 0.2), (0, 16, 9.8, 10), (0, 0.2, 0, 10), (15.8, 16, 0, 10), (7, 7.15, 0, 10 if i % 2 == 0 else 5))]
        branches.append(br)
    branches.append([ab.box(rg.Plane.WorldXY, -0.4 * u, 16.4 * u, -0.4 * u, 10.4 * u, 9.4 * u, 9.7 * u)])
    if not names:
        names = ["GROUND", "LEVEL 1", "LEVEL 2", "ROOF"]
    notes.append("sample building (wire geometry: one branch per group)")

mode = mode if mode in ("vertical stack", "height bands", "along vector", "radial") else "vertical stack"
step = max(spacing if spacing is not None else 3.0, 0.0) * u

# groups: lists of (branch, item) in input order
groups = []
items = [(i, j) for i, br in enumerate(branches) for j, g in enumerate(br) if g is not None and g.GetBoundingBox(False).IsValid]
if mode == "height bands":
    h = max(band if band else 3.2, 0.1) * u
    z0 = min(branches[i][j].GetBoundingBox(True).Min.Z for i, j in items) if items else 0.0
    by = {}
    for i, j in items:
        k = int(math.floor((branches[i][j].GetBoundingBox(True).Min.Z - z0 + tol) / h))
        by.setdefault(k, []).append((i, j))
    groups = [by[k] for k in sorted(by)]
elif len(branches) == 1:
    groups = [[ij] for ij in items]
else:
    groups = [[(i, j) for (i, j) in items if i == b] for b in range(len(branches))]
    groups = [g for g in groups if g]


def bbox(group, xf=None):
    b = rg.BoundingBox.Empty
    for i, j in group:
        bb = branches[i][j].GetBoundingBox(True)
        if xf is not None:
            bb.Transform(xf)
        b.Union(bb)
    return b


boxes = [bbox(g) for g in groups]
whole = rg.BoundingBox.Empty
for b in boxes:
    whole.Union(b)
diag = whole.Diagonal.Length if whole.IsValid else u

# ── offsets ──

direction = Z
if mode == "along vector":
    direction = rg.Vector3d(axis) if isinstance(axis, rg.Vector3d) else Z
    if direction.Length < 1e-9:
        notes.append("axis has no length: used world Z")
        direction = Z
    direction.Unitize()

if mode == "along vector":
    order = sorted(range(len(groups)), key=lambda k: (rg.Vector3d(boxes[k].Center) * direction, k))
else:
    order = sorted(range(len(groups)), key=lambda k: (round(boxes[k].Min.Z / tol), boxes[k].Center.Z, k))
base = order[0] if order else None
offsets = [rg.Vector3d.Zero for _ in groups]
prev = [rg.Vector3d.Zero for _ in groups]   # where each group's explosion lines start from
if mode == "radial":
    c = whole.Center
    far = max([(b.Center - c).Length for b in boxes] or [0.0])
    for k, b in enumerate(boxes):
        v = b.Center - c
        offsets[k] = v * (step / far) if far > tol else rg.Vector3d.Zero
    if keep_base and base is not None:
        offsets[base] = rg.Vector3d.Zero
else:
    for rank, k in enumerate(order):
        n = rank if keep_base else rank + 1
        offsets[k] = direction * (step * n)
        prev[k] = direction * (step * (n - 1)) if n > 0 else rg.Vector3d.Zero
if beside and whole.IsValid:  # clear of the original, one gap further along X
    aside = rg.Vector3d(whole.Diagonal.X + max(step, 2.0 * u), 0, 0)
    offsets = [v + aside for v in offsets]
    prev = [v + aside for v in prev]
transforms = [rg.Transform.Translation(v) for v in offsets]

# ── the exploded parts, same structure as the input ──

where = {}
for k, g in enumerate(groups):
    for ij in g:
        where[ij] = k
exploded = []
for i, br in enumerate(branches):
    row = []
    for j, g in enumerate(br):
        if g is None or (i, j) not in where:
            row.append(g)
            continue
        dup = g.Duplicate()
        dup.Transform(transforms[where[(i, j)]])
        row.append(dup)
    exploded.append(row)

# ── explosion lines ──


def points_of(g):
    """Points that outline a part (vertices, edge and curve samples; box corners otherwise)."""
    if isinstance(g, rg.Extrusion):
        g = g.ToBrep()
    if isinstance(g, rg.Brep):
        pts = [v.Location for v in g.Vertices]
        for e in g.Edges:
            if not e.IsLinear(tol):
                pts += [e.PointAt(t) for t in e.DivideByCount(8, True) or []]
        return pts
    if isinstance(g, rg.Mesh):
        vs = g.Vertices
        stride = max(1, vs.Count // 1500)
        return [rg.Point3d(vs[k]) for k in range(0, vs.Count, stride)]
    if isinstance(g, rg.Curve):  # its corners (polyline vertices, segment ends) and samples along it
        ok, pl = g.TryGetPolyline()
        if ok:
            return list(pl)
        return [g.PointAt(t) for t in g.DivideByCount(24, True) or []] + [x.PointAtStart for x in g.DuplicateSegments() or []] + [g.PointAtEnd]
    if isinstance(g, rg.Point):
        return [g.Location]
    return list(g.GetBoundingBox(True).GetCorners())


def hull(pts2):
    """Convex hull (monotone chain) of (u, v) tuples, counter-clockwise."""
    pts2 = sorted(set(pts2))
    if len(pts2) < 3:
        return pts2
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts2:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts2):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def simplify(poly, keep=8):
    """Drop the hull corners that matter least (smallest triangle): all the near-straight ones, then down to `keep`."""
    poly = list(poly)
    if len(poly) < 3:
        return poly
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    small = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) * 1e-4
    while len(poly) > 3:
        areas = []
        for k in range(len(poly)):
            a, b, c = poly[k - 1], poly[k], poly[(k + 1) % len(poly)]
            areas.append(abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])))
        if len(poly) <= keep and min(areas) > small:
            break
        poly.pop(areas.index(min(areas)))
    return poly


def corners(group, along):
    """The outline corners of a group on its face that trails the move (where the explosion lines start)."""
    a = rg.Vector3d(along)
    a.Unitize()
    U = rg.Vector3d.CrossProduct(a, Z if abs(a.Z) < 0.9 else rg.Vector3d.XAxis)
    U.Unitize()
    V = rg.Vector3d.CrossProduct(a, U)
    pts = [p for i, j in group for p in points_of(branches[i][j])]
    if not pts:
        return []
    O = rg.Point3d.Origin
    low = {}  # each outline position -> its point furthest back along the move (a real corner of the part)
    for p in pts:
        key = (round((p - O) * U, 6), round((p - O) * V, 6))
        t = (p - O) * a
        if key not in low or t < low[key]:
            low[key] = t
    return [O + U * x + V * y + a * low[(x, y)] for x, y in simplify(hull(list(low)))]


dash = diag / 120.0
lines = []
if leaders in ("corners", "centres"):
    for k, g in enumerate(groups):
        move = offsets[k] - prev[k]
        if move.Length < tol:
            continue
        starts = corners(g, move) if leaders == "corners" else [boxes[k].Center]
        for p in starts:
            lines += av.dashed(p + prev[k], p + offsets[k], dash, dash * 0.6)

# ── labels, beside each group as seen from the axo ──

dv = av.iso_direction(angle)
right = rg.Vector3d.CrossProduct(dv, av.up_for(dv))
right.Unitize()
labels = []
th = min(max(diag * 0.025, 0.1 * u), 2.0 * u)
named = [str(s) for s in (names or []) if s is not None]
for k, g in enumerate(groups):
    if k >= len(named) or not named[k].strip():
        continue
    b = bbox(g, transforms[k])
    reach = max((p - rg.Point3d.Origin) * right for p in b.GetCorners())
    c = b.Center
    origin = c + right * (reach - (c - rg.Point3d.Origin) * right + th)
    labels += av.letters(named[k].strip(), rg.Plane(origin, right, Z), th, "left", "middle")
if len(named) > len(groups):
    notes.append("%d labels for %d groups: the extra ones are not drawn" % (len(named), len(groups)))

# ── info ──

far = max([(v - (aside if beside and whole.IsValid else rg.Vector3d.Zero)).Length for v in offsets] or [0.0])
lines_info = ["%d group%s (%s), %s%s; furthest move %.2f m%s" % (
    len(groups), "" if len(groups) == 1 else "s", "branches" if len(branches) > 1 and mode != "height bands" else "height bands of %.1f m" % (max(band or 3.2, 0.1)) if mode == "height bands" else "parts",
    mode, (" along (%.2f, %.2f, %.2f)" % (direction.X, direction.Y, direction.Z)) if mode == "along vector" else "",
    far / u, ", lowest group kept in place" if keep_base and groups else "")]
if beside and whole.IsValid:
    lines_info.append("drawn beside the original, %.2f m along X" % (aside.X / u))
if lines:
    lines_info.append("%d dashes of explosion lines (%s)" % (len(lines), leaders))
if labels:
    lines_info.append("labels: %s" % ", ".join(s for s in named[:len(groups)] if s.strip()))

# ── named view (button only) ──

vname = str(name).strip() if name and str(name).strip() else "Alien axo · Exploded"
mode_ = av.find_mode(display)
if display and str(display).strip() and mode_ is None:
    notes.append("no display mode %r: view saved without one" % display)
if view and d is not None and groups:
    pts = []
    for row in exploded:
        for g in row:
            if g is not None:
                pts += list(g.GetBoundingBox(True).GetCorners())
    for cv in labels + lines:
        pts += list(cv.GetBoundingBox(True).GetCorners())
    up = av.up_for(dv)
    cen = whole.Center
    x0, x1, y0, y1, z0, z1 = av.extents(pts, cen, right, up, dv)
    mg = 0.05 * max(x1 - x0, y1 - y0) + 0.5 * u
    spec = {"name": vname, "kind": "axo", "d": dv, "up": up, "centre": cen + right * ((x0 + x1) / 2) + up * ((y0 + y1) / 2) + dv * ((z0 + z1) / 2),
            "hw": (x1 - x0) / 2 + mg, "hh": (y1 - y0) / 2 + mg, "clip": None, "deep": z1 - z0}
    av.clear_views(d, "exploded_axo", vname)
    made, skipped = av.save_views(d, [spec], "exploded_axo", vname, mode_)
    if made:
        lines_info.append("Saved the named view '%s' (isometric from %s, %.1f x %.1f m%s)" % (
            vname, str(angle).upper(), 2 * spec["hw"] / u, 2 * spec["hh"] / u, ", " + mode_.EnglishName if mode_ else ""))
    for n, why in skipped:
        notes.append("not saved: %s: %s" % (n, why))
elif groups:
    lines_info.append("Press view to save an isometric named view '%s'." % vname)

info = "\n".join(lines_info + notes)
