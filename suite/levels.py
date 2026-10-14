# NODE_INPUTS: count:int | "storeys from the ground up" min=1 max=100 default=4, height:float | "floor-to-floor height (m)" min=0.5 max=20 default=3.2
# NODE_INPUTS: heights:list[str] | "per-storey heights (m), from the lowest up, e.g. '4.5, 3, , 3.6' (a blank keeps height)"
# NODE_INPUTS: base:float | "ground level above the site origin (m)" min=-20 max=50 default=0, outline:Curve | "floor plate to copy to every storey (optional; else the site boundary or a 20 x 12 m sample)"
# NODE_INPUTS: site:object | "site record from bim_site: places the levels, datum gives RLs, scale sizes the text"
# NODE_INPUTS: naming:str | "level names" default="Level 1" options="Level 1|L1|L01|Level 01|LVL 1|custom", prefix:str | "custom names: prefix + number, or # for the number ('Floor #', '##F' gives 01F)" default="Floor #"
# NODE_INPUTS: start:int | "number of the first numbered level" min=-5 max=100 default=1, ground:str | "name of the ground level" default="Ground" options="Ground|Ground Floor|GF|G|numbered"
# NODE_INPUTS: basements:int | "storeys below ground (Basement 1 is just below)" min=0 max=10 default=0, roof_level:bool | "add a Roof level on top of the last storey" default=true
# NODE_INPUTS: view:str | "level lines for which elevations" default="both" options="front (along X)|side (along Y)|both", ends:str | "level heads at" default="right" options="right|left|both|none"
# NODE_INPUTS: margin:float | "level lines run past the floor plates (m)" min=0 max=20 default=2, scale:str | "drawing scale of the annotation" default="site" options="site|1:50|1:100|1:200|1:500"
# NODE_INPUTS: text:float | "text height on paper (mm) at the drawing scale" min=1 max=10 step=0.5 default=3.5
# NODE_OUTPUTS: planes:list[Plane] | "one per storey", elevations:list[float] | "storey z (m)", floor_heights:list[float] | "floor-to-floor of each storey (m)"
# NODE_OUTPUTS: names:list[str] | "storey names", plates:list[Curve] | "floor plate at each storey", roof:Plane | "top of the last storey", total:float | "ground to roof (m)"
# NODE_OUTPUTS: levels:list[str] | "level records, storeys and roof: wire into the building nodes' levels input", lines:list[Curve] | "level lines for elevations and sections"
# NODE_OUTPUTS: heads:list[Curve] | "level heads: datum symbol, name and RL at the line ends", annotations:list[Curve] | "plan label on each level: 'LEVEL 1  RL +3.200'"
# NODE_OUTPUTS: labels:list[str] | "one per level", rl:list[float] | "reduced level of each level (m): datum + elevation", info:str
# CATEGORY: Building elements
# Levels: a stack of named building levels (basements, storeys, roof) with RLs from the site datum, level lines and heads for elevations, plan labels and floor plates.
#
# Elevations are model z in metres; with a site they start from the site origin's z and RL = datum + elevation. The
# planes, elevations, floor_heights, names and plates outputs give one item per storey (as before the roof level
# existed, so old wires keep their meaning); levels, lines, heads, annotations, labels and rl give every level, roof
# included. Text sizes are paper millimetres at the drawing scale (the site's scale by default, 1:100 without a
# site), the way annotation is specified on a drawing: 3.5 mm at 1:100 is 0.35 m in the model.
import re

import Rhino.Geometry as rg

import alien_bim as ab

u = ab.units()
s = ab.site_of(site)
splane = ab.site_plane(s, u)
datum = float(s.get("datum") or 0.0)
notes = []

count = max(1, int(count or 1))
height = height if height and height > 0 else 3.2
nb = max(0, int(basements or 0))

# per-storey overrides: '4.5, 3, , 3.6' or one value per line; blanks and junk keep the default
custom = []
for item in heights or []:
    for tok in re.split(r"[,;\n]", str(item)):
        tok = tok.strip()
        try:
            v = float(tok) if tok else None
        except ValueError:
            v = -1
        if v is not None and v <= 0:
            notes.append("not a height: %r (used %g m)" % (tok, height))
            v = None
        custom.append(v)
n = nb + count
floor_heights = [(custom[i] if i < len(custom) and custom[i] else height) for i in range(n)]

# ground at the site origin's z + base; basements stack down from it
z0 = splane.Origin.Z / u + (base or 0.0)
elevations = [z0 - sum(floor_heights[i:nb]) for i in range(nb)]
z = z0
for h in floor_heights[nb:]:
    elevations.append(z)
    z += h
elevations = [round(e, 4) for e in elevations]
top = round(z, 4)
total = round(top - z0, 4)

# ── names ──

LONG = {"Level 1": "Level %d", "L1": "L%d", "L01": "L%02d", "Level 01": "Level %02d", "LVL 1": "LVL %d"}
BASEMENT = {"Level 1": "Basement %d", "L1": "B%d", "L01": "B%02d", "Level 01": "Basement %02d", "LVL 1": "BSMT %d"}
ROOF = {"L1": "RF", "L01": "RF", "LVL 1": "ROOF"}
scheme = naming if naming in LONG or naming == "custom" else "Level 1"
first = int(start if start is not None else 1)


def numbered(k):
    if scheme != "custom":
        return LONG[scheme] % k
    p = str(prefix if prefix is not None else "")
    m = re.search(r"#+", p)
    if not m:
        return p + str(k)
    return p[:m.start()] + str(k).zfill(len(m.group())) + p[m.end():]


rows = []  # (name, code, number, is_roof)
for i in range(nb, 0, -1):
    rows.append((BASEMENT.get(scheme, "B%d") % i, "B%02d" % i, -i, False))
for i in range(count):
    if i == 0 and ground != "numbered":
        rows.append((str(ground or "Ground"), "GF", 0, False))
        continue
    k = first + i - (1 if ground != "numbered" else 0)
    rows.append((numbered(k), "L%02d" % k, i, False))
if roof_level:
    rows.append((ROOF.get(scheme, "Roof"), "RF", count, True))
names = [r[0] for r in rows[:n]]
if len(set(r[0] for r in rows)) < len(rows):
    notes.append("some level names repeat: check start, ground and the prefix")


def rl_text(e):
    v = datum + e
    if datum:
        return "RL %.3f" % v
    return "RL ±0.000" if abs(v) < 5e-4 else "RL %+.3f" % v


lv_elev = elevations + ([top] if roof_level else [])
lv_height = floor_heights + ([0.0] if roof_level else [])
rl = [round(datum + e, 4) for e in lv_elev]
labels = ["%s  %s" % (r[0].upper(), rl_text(e)) for r, e in zip(rows, lv_elev)]
levels = [ab.pack("level", name=r[0], code=r[1], index=i, elevation=e, height=h, rl=v, number=r[2], roof=r[3])
          for i, (r, e, h, v) in enumerate(zip(rows, lv_elev, lv_height, rl))]

# ── planes and plates ──


def at_z(e):
    return rg.Plane(rg.Point3d(splane.Origin.X, splane.Origin.Y, e * u), splane.XAxis, splane.YAxis)


planes = [at_z(e) for e in elevations]
roof = at_z(top)

if outline is None:
    outline = ab.curve_from(s.get("boundary"), u)  # the lot as an envelope, else a 20 x 12 m sample on the site plane
    if outline is None:
        outline = rg.Rectangle3d(splane, 20 * u, 12 * u).ToNurbsCurve()
z_start = outline.PointAtStart.Z
plates = []
for e in elevations:
    c = outline.DuplicateCurve()
    c.Translate(rg.Vector3d(0, 0, e * u - z_start))
    plates.append(c)

# ── level lines, heads and plan labels, in the site's frame ──

try:
    den = int(str(scale).split(":")[-1]) if scale and scale != "site" else int(s.get("scale") or 100)
except ValueError:
    den = 100
t = max(text or 3.5, 0.5) * den / 1000.0 * u   # text height, model units
rad = 0.9 * t                                   # datum symbol radius
g = 0.4 * t
box = outline.GetBoundingBox(splane)            # plate extents in the site plane's coordinates
x0, x1, y0, y1 = box.Min.X, box.Max.X, box.Min.Y, box.Max.Y
ok, poly = outline.TryGetPolyline()
local = []
for p in (list(poly) if ok else [outline.PointAt(k) for k in outline.DivideByCount(64, True)]):
    ok2, a, b = splane.ClosestParameter(p)
    local.append((a, b))
lx, ly = max(local, key=lambda q: q[1] - q[0])  # the plate's back-left-most point: where the plan label goes
m = (margin if margin is not None and margin >= 0 else 2.0) * u
X, Y, Z = splane.XAxis, splane.YAxis, rg.Vector3d.ZAxis


def world(x, y, e):
    p = splane.PointAt(x, y)
    return rg.Point3d(p.X, p.Y, e * u)


def quadrant(c, d, up, sx, sy):
    """A quarter disc of the datum symbol in the quadrant (sx, sy) of the head plane: outline plus hatch lines, so it
    reads as filled in line drawings too."""
    a, b = c + d * rad * sx, c + up * rad * sy
    mid = c + (d * sx + up * sy) * (rad * 0.70710678)
    pc = rg.PolyCurve()
    pc.Append(rg.Line(c, a))
    pc.Append(rg.Arc(a, mid, b))
    pc.Append(rg.Line(b, c))
    zig = []
    for k in range(1, 8):  # hatch as one zigzag: rows alternate direction, joined along the axis and the arc
        h = rad * k / 8.0
        row = [c + up * h * sy, c + up * h * sy + d * (rad * rad - h * h) ** 0.5 * sx]
        zig += row if k % 2 else row[::-1]
    return [pc, rg.PolylineCurve(zig)]


memo = {}


def letters(s, plane, height, h="center", v="middle"):
    """ab.text_curves, outlined once per string and alignment, then copied (outlining is most of the solve time)."""
    key = (s, h, v)
    if key not in memo:
        memo[key] = ab.text_curves(s, rg.Plane.WorldXY, 1.0, h, v)
    xf = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, plane) * rg.Transform.Scale(rg.Point3d.Origin, height)
    out = []
    for c in memo[key]:
        c = c.DuplicateCurve()
        c.Transform(xf)
        out.append(c)
    return out


def head(p, d, side, row, e):
    """Datum symbol centred on the line end p, name above and RL below, on the outside of the line (side +1 = end)."""
    hp = rg.Plane(p, d, Z)
    out = [rg.Circle(hp, rad).ToNurbsCurve()] + quadrant(p, d, Z, 1, 1) + quadrant(p, d, Z, -1, -1)
    a = p + d * (rad + g) * side
    h = "left" if side > 0 else "right"
    out += letters(row[0].upper(), rg.Plane(a + Z * g, d, Z), t, h, "bottom")
    out += letters(rl_text(e), rg.Plane(a - Z * g, d, Z), t * 0.8, h, "top")
    return out


dirs = {"front (along X)": ["x"], "side (along Y)": ["y"]}.get(view, ["x", "y"])
sides = {"right": [1], "left": [-1], "none": []}.get(ends, [-1, 1])
lines, heads, annotations = [], [], []
for row, e in zip(rows, lv_elev):
    for d in dirs:
        # front: along X on the plates' front edge (seen in the Front view); side: along Y on the right edge
        a, b, vec = (world(x0 - m, y0, e), world(x1 + m, y0, e), X) if d == "x" else (world(x1, y0 - m, e), world(x1, y1 + m, e), Y)
        lines.append(rg.LineCurve(a, b))
        for sd in sides:
            heads += head(b if sd > 0 else a, vec, sd, row, e)
    # plan label just beyond the plate's back-left corner, clear of the base point and grid dimensions at the front
    annotations += letters("%s  %s" % (row[0].upper(), rl_text(e)), rg.Plane(world(lx, ly + g * 2, e), X, Y), t, "left", "bottom")

info = "%d levels: %s%d storeys%s, ground %s (%.3f m), roof %s (%.3f m); %.2f m ground to roof" % (
    len(rows), "%d basement + " % nb if nb else "", count, " + roof" if roof_level else "",
    rl_text(z0), z0, rl_text(top), top, total)
shown = [r[0] for r in rows]
info += "\nnames: %s" % ", ".join(shown if len(shown) <= 12 else shown[:6] + ["..."] + shown[-3:])
info += "\n%s; text %.1f mm at 1:%d (%.2f m)" % ("datum RL %.3f m AHD" % datum if datum else "no datum: RLs relative to model z 0",
                                                 text or 3.5, den, t / u)
if notes:
    info += "\n" + "\n".join(notes)
