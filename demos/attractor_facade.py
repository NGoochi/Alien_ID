# NODE_INPUTS: facade:Brep | "facade surface (a curved 30 x 14 m sample when unwired)", attractor:Point3d | "attractor point (wired or clicked); unwired: u and v place it on the facade"
# NODE_INPUTS: u:float | "attractor along the facade, 0-1 (when no point is given)" min=0 max=1 step=0.01 default=0.3
# NODE_INPUTS: v:float | "attractor up the facade, 0-1 (when no point is given)" min=0 max=1 step=0.01 default=0.55
# NODE_INPUTS: columns:int | "panels across" min=2 max=60 default=24, rows:int | "panels up" min=1 max=30 default=10
# NODE_INPUTS: shape:str | "opening shape" default="circle" options="circle|square|diamond|slot"
# NODE_INPUTS: smallest:float | "opening size far from the attractor, share of the panel" min=0 max=0.95 step=0.01 default=0.08
# NODE_INPUTS: largest:float | "opening size at the attractor, share of the panel" min=0.05 max=0.95 step=0.01 default=0.86
# NODE_INPUTS: reach:float | "how far the attractor's pull reaches (m)" min=1 max=60 default=12
# NODE_INPUTS: invert:bool | "largest openings far from the attractor instead" default=false
# NODE_INPUTS: depth:float | "panel thickness (mm)" min=2 max=300 default=40, joint:float | "open joint between panels (mm)" min=0 max=300 default=30
# NODE_OUTPUTS: panels:list[Brep] | "perforated panels, one per cell", colours:list[Color] | "colour per panel: darker where it is more open"
# NODE_OUTPUTS: glass:list[Brep] | "glazing set back behind the panels", openings:list[Curve] | "opening outlines", openness:list[float] | "opening size per panel, 0-1"
# NODE_OUTPUTS: point:Point3d | "the attractor used", info:str
# CATEGORY: Massing & objects
# Attractor facade: a perforated rainscreen on any facade surface whose openings swell towards an attractor point (circles, squares, diamonds or slots), with flat panels, open joints and glazing behind.
#
# Each cell of the facade's u/v grid becomes a flat panel in the plane fitted through its corners (so curved facades
# come out faceted, as they are built), shrunk by half the joint all round. The opening size eases from `smallest` to
# `largest` as the distance to the attractor falls from `reach` to 0 (smoothstep), and panels are tinted by it. Sizes
# are metres and millimetres whatever the model's units.
import math

import Rhino
import Rhino.Geometry as rg
import System.Drawing

doc = Rhino.RhinoDoc.ActiveDoc
u_ = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
tol = doc.ModelAbsoluteTolerance if doc else 0.001
notes = []

# ── the facade surface ──
srf = None
if isinstance(facade, rg.Brep) and facade.Faces.Count > 0:
    srf = facade.Faces[0]
    if facade.Faces.Count > 1:
        notes.append("facade has %d faces: used the first" % facade.Faces.Count)
elif isinstance(facade, rg.Surface):
    srf = facade
sample = srf is None
if sample:
    # a gently curved facade: an arc 30 m wide with 3 m of bow, 14 m high
    arc = rg.Arc(rg.Point3d(0, 0, 0), rg.Point3d(15 * u_, -3 * u_, 0), rg.Point3d(30 * u_, 0, 0)).ToNurbsCurve()
    srf = rg.Surface.CreateExtrusion(arc, rg.Vector3d(0, 0, 14 * u_))
U, V = srf.Domain(0), srf.Domain(1)
# "across" is whichever surface direction runs more level, "up" the other, rising: any facade reads the same way
pu = srf.PointAt(U.Max, V.Mid) - srf.PointAt(U.Min, V.Mid)
pv = srf.PointAt(U.Mid, V.Max) - srf.PointAt(U.Mid, V.Min)
swap = abs(pu.Z) / max(pu.Length, 1e-12) > abs(pv.Z) / max(pv.Length, 1e-12)
rising = (pu.Z if swap else pv.Z) >= 0


def at(a, b):
    """The facade point `a` of the way across and `b` of the way up (0-1)."""
    b = b if rising else 1.0 - b
    return srf.PointAt(U.ParameterAt(b), V.ParameterAt(a)) if swap else srf.PointAt(U.ParameterAt(a), V.ParameterAt(b))


nu, nv = max(2, int(columns or 24)), max(1, int(rows or 10))
lo, hi = max(0.0, min(0.95, smallest or 0.0)), max(0.05, min(0.95, largest or 0.86))
reach_m = max(1.0, reach or 12.0)

if isinstance(attractor, rg.Point3d) and attractor.IsValid:
    point = attractor
else:
    point = at(max(0.0, min(1.0, u if u is not None else 0.3)), max(0.0, min(1.0, v if v is not None else 0.55)))


def corner(i, j):
    return at(float(i) / nu, float(j) / nv)


def opening_curve(pl, w, h, s):
    """The opening, centred on the panel plane, s = share of the panel's smaller side (slots: of its height)."""
    m = min(w, h)
    if shape == "square":
        a = s * m / 2
        return rg.PolylineCurve([pl.PointAt(-a, -a), pl.PointAt(a, -a), pl.PointAt(a, a), pl.PointAt(-a, a), pl.PointAt(-a, -a)])
    if shape == "diamond":
        a = s * m / 2 * 1.2
        return rg.PolylineCurve([pl.PointAt(0, -a), pl.PointAt(a, 0), pl.PointAt(0, a), pl.PointAt(-a, 0), pl.PointAt(0, -a)])
    if shape == "slot":
        hw, hh = 0.12 * w / 2, s * h / 2 * 0.95
        r = rg.Rectangle3d(pl, rg.Interval(-hw, hw), rg.Interval(-hh, hh))
        return r.ToNurbsCurve()
    return rg.Circle(pl, s * m / 2).ToNurbsCurve()


panels, colours, glass, openings, openness = [], [], [], [], []
half = (joint or 0) / 1000.0 * u_ / 2
thick = max(0.002, (depth or 40) / 1000.0) * u_
low, high = System.Drawing.Color.FromArgb(214, 196, 168), System.Drawing.Color.FromArgb(92, 70, 52)  # pale to dark bronze
skipped = 0
for j in range(nv):
    for i in range(nu):
        pts = [corner(i, j), corner(i + 1, j), corner(i + 1, j + 1), corner(i, j + 1)]
        c = rg.Point3d((pts[0].X + pts[1].X + pts[2].X + pts[3].X) / 4, (pts[0].Y + pts[1].Y + pts[2].Y + pts[3].Y) / 4,
                       (pts[0].Z + pts[1].Z + pts[2].Z + pts[3].Z) / 4)
        xd = pts[1] - pts[0] + pts[2] - pts[3]
        yd = pts[3] - pts[0] + pts[2] - pts[1]
        pl = rg.Plane(c, xd, yd)
        flat = [pl.ClosestPoint(p) for p in pts]
        q = [pl.ClosestParameter(p) for p in flat]
        w = (abs(q[1][1] - q[0][1]) + abs(q[2][1] - q[3][1])) / 2.0
        h = (abs(q[3][2] - q[0][2]) + abs(q[2][2] - q[1][2])) / 2.0
        inner = []
        for k in range(4):  # shrink each corner towards the centre by half the joint
            x_, y_ = q[k][1], q[k][2]
            inner.append(pl.PointAt(x_ - math.copysign(min(half, abs(x_)), x_), y_ - math.copysign(min(half, abs(y_)), y_)))
        outline = rg.PolylineCurve(inner + [inner[0]])

        dist_m = c.DistanceTo(point) / u_
        t = max(0.0, 1.0 - dist_m / reach_m)
        t = t * t * (3 - 2 * t)  # smoothstep: a soft swell
        if invert:
            t = 1.0 - t
        s = lo + (hi - lo) * t
        hole = opening_curve(pl, w - 2 * half, h - 2 * half, s) if s > 0.01 else None

        face = rg.Brep.CreatePlanarBreps([outline] + ([hole] if hole else []), tol)
        if not face:
            skipped += 1
            continue
        path = rg.LineCurve(rg.Point3d.Origin, rg.Point3d.Origin + pl.ZAxis * (-thick))
        solid = face[0].Faces[0].CreateExtrusion(path, True)
        if solid is None or not solid.IsValid:
            skipped += 1
            continue
        if solid.SolidOrientation == rg.BrepSolidOrientation.Inward:
            solid.Flip()
        panels.append(solid)
        colours.append(System.Drawing.Color.FromArgb(255, int(low.R + (high.R - low.R) * t), int(low.G + (high.G - low.G) * t),
                                                     int(low.B + (high.B - low.B) * t)))
        if hole:
            openings.append(hole)
        openness.append(round(s, 3))
        # glazing 150 mm behind the panel, the cell's full size
        back = rg.Plane(pl)
        back.Origin = pl.Origin - pl.ZAxis * (thick + 0.15 * u_)
        g = rg.Brep.CreatePlanarBreps(rg.PolylineCurve([back.ClosestPoint(p) for p in flat] + [back.ClosestPoint(flat[0])]), tol)
        if g:
            glass.append(g[0])

info = "%d panels (%d x %d), openings %s from %.0f%% to %.0f%%, reach %.1f m%s; attractor at %.1f, %.1f, %.1f m" % (
    len(panels), nu, nv, shape, lo * 100, hi * 100, reach_m, ", inverted" if invert else "",
    point.X / u_, point.Y / u_, point.Z / u_)
if sample:
    info += "\nSample facade: wire or pick a surface into facade."
if skipped:
    notes.append("%d cells skipped (degenerate or too small for the joint)" % skipped)
if notes:
    info += "\n" + "\n".join(notes)
