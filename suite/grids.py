# NODE_INPUTS: site:object | "site record from bim_site: grids start at its origin and run along its rotation"
# NODE_INPUTS: x_spacing:str | "bays along project X (m): '7.2, 7.2, 8.4' or '3x7.2'; empty = no grids that way" default="3x7.2", y_spacing:str | "bays along project Y (m)" default="2x6"
# NODE_INPUTS: labels:str | "numbered or lettered grids along X (the other way gets the other)" default="numbers along X" options="numbers along X|letters along X"
# NODE_INPUTS: x_start:str | "first label of the grids along X ('1', '4', 'C', 'X1')" default="1", y_start:str | "first label of the grids along Y" default="A"
# NODE_INPUTS: skip_io:bool | "skip I and O in letters (they read as 1 and 0)" default=true, extension:float | "grid lines run past the outermost grids (m)" min=0 max=20 default=3
# NODE_INPUTS: bubble:float | "head diameter on paper (mm) at the drawing scale" min=4 max=30 step=0.5 default=10, ends:str | "heads at" default="both" options="both|start|end|none"
# NODE_INPUTS: scale:str | "drawing scale of the heads and dimensions" default="site" options="site|1:50|1:100|1:200|1:500"
# NODE_INPUTS: custom:list[Curve] | "extra grids: angled or curved curves (optional)", custom_names:list[str] | "names of the custom grids (default G1, G2 ...)"
# NODE_INPUTS: dimensions:bool | "bay and overall dimension strings (mm) on the start sides" default=true
# NODE_OUTPUTS: grids:list[str] | "grid records: wire into the building nodes' grids input", lines:list[Curve] | "grid lines", heads:list[Curve] | "bubbles: circle and name"
# NODE_OUTPUTS: names:list[str] | "one per grid line", points:list[Point3d] | "grid intersections", point_names:list[str] | "'A/1' ...", dims:list[Curve] | "dimension linework", info:str
# CATEGORY: Building elements
# Grids: architectural column grids like Revit or ArchiCAD: numbered and lettered grids from bay sizes, on the site's origin and rotation, with heads, intersections and dimensions.
#
# Grids along the project X are spaced by x_spacing from the site origin (numbered 1, 2, 3 ... by default) and run
# across the whole Y range; grids along Y are lettered A, B, C ... (I and O skipped). Start labels may be numbers,
# letters or a prefix + number ('X1'), read in the direction's style ('3' on a lettered set starts at C). Custom
# curves add angled or curved grids (group "custom"); a curved one keeps its curve in the record ("curve"), and its
# crossings are found on the curve itself. Bubble and text sizes are paper millimetres at the drawing scale.
import re

import Rhino.Geometry as rg

import alien_bim as ab

u = ab.units()
tol = ab.tol()
s = ab.site_of(site)
splane = ab.site_plane(s, u)
X, Y = splane.XAxis, splane.YAxis
notes = []
MAX_GRIDS = 200

try:
    den = int(str(scale).split(":")[-1]) if scale and scale != "site" else int(s.get("scale") or 100)
except ValueError:
    den = 100
paper = den / 1000.0 * u  # model units per paper millimetre
D = max(bubble or 10.0, 1.0) * paper
R = D / 2
ext = (extension if extension is not None and extension >= 0 else 3.0) * u


def bays(text, axis):
    """'7.2, 7.2, 8.4', '3x7.2', '2 x 6; 8.4' -> bay sizes (m). Values over 100 are taken as millimetres."""
    out = []
    for tok in re.split(r"[,;\s]+(?![xX*×])(?<![xX*×]\s)", str(text or "").strip()):
        tok = tok.replace(" ", "")
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)[xX*×]([-+\d.eE]+)", tok)
        try:
            k, v = (int(m.group(1)), float(m.group(2))) if m else (1, float(tok))
        except ValueError:
            notes.append("%s: %r is not a bay size" % (axis, tok))
            continue
        if v > 100:
            notes.append("%s: %g read as millimetres" % (axis, v))
            v /= 1000.0
        if v <= 0:
            if v < 0 or k > 1 or out:
                notes.append("%s: skipped bay %g" % (axis, v))
            if v == 0:
                out.append(0.0)  # a lone '0' gives one grid line
            continue
        out += [v] * k
    if len(out) >= MAX_GRIDS:
        notes.append("%s: only the first %d bays" % (axis, MAX_GRIDS - 1))
        out = out[:MAX_GRIDS - 1]
    return out


def positions(bs):
    if not bs:
        return []
    ps = [0.0]
    for b in bs:
        if b > 0:
            ps.append(ps[-1] + b)
    return ps


# ── labels ──

LETTERS = [ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if not (skip_io and ch in "IO")]


def letter(k):
    """0 -> A ... then AA, AB ... (Revit's sequence)."""
    n = len(LETTERS)
    if k < n:
        return LETTERS[k]
    k -= n
    return letter(k // n) + LETTERS[k % n] if k // n < n else "?"


def letter_index(text):
    k = 0
    for ch in text:
        if ch not in LETTERS:  # I or O when skipped: start at the next letter
            later = [c for c in LETTERS if c > ch]
            ch = later[0] if later else LETTERS[-1]
        k = k * len(LETTERS) + LETTERS.index(ch) + 1
    return k - 1


def sequence(style, start, count, axis):
    st = str(start or "").strip().upper()
    m = re.fullmatch(r"([A-Z]*?)(-?\d+)", st)
    if style == "numbers":
        if m:
            return ["%s%d" % (m.group(1), int(m.group(2)) + i) for i in range(count)]
        if st.isalpha():
            first_ = letter_index(st) + 1
            return [str(first_ + i) for i in range(count)]
        if st:
            notes.append("%s: start %r is not a label, used 1" % (axis, start))
        return [str(1 + i) for i in range(count)]
    if st.isalpha():
        k0 = letter_index(st)
    elif m and not m.group(1):
        k0 = max(int(m.group(2)) - 1, 0)
    else:
        if st:
            notes.append("%s: start %r is not a letter, used A" % (axis, start))
        k0 = 0
    return [letter(k0 + i) for i in range(count)]


xs = positions(bays(x_spacing, "x_spacing"))
ys = positions(bays(y_spacing, "y_spacing"))
x_style, y_style = ("numbers", "letters") if labels != "letters along X" else ("letters", "numbers")
x_names = sequence(x_style, x_start, len(xs), "x_start")
y_names = sequence(y_style, y_start, len(ys), "y_start")

# ── grid lines: each set spans the other set's range plus the extension ──

def world(x, y):
    return splane.PointAt(x * u, y * u)


ylo, yhi = (min(ys), max(ys)) if ys else (0.0, 0.0)
xlo, xhi = (min(xs), max(xs)) if xs else (0.0, 0.0)
e = ext / u
items = []  # (name, group, index, curve)
for i, (x, nm) in enumerate(zip(xs, x_names)):
    items.append((nm, x_style, i, rg.LineCurve(world(x, ylo - e), world(x, yhi + e))))
for i, (y, nm) in enumerate(zip(ys, y_names)):
    items.append((nm, y_style, i, rg.LineCurve(world(xlo - e, y), world(xhi + e, y))))
cnames = [str(n).strip() for n in (custom_names or []) if n is not None]
k = 0
for c in custom or []:
    if not isinstance(c, rg.Curve) or not c.IsValid or c.GetLength() < tol:
        continue
    nm = cnames[k] if k < len(cnames) and cnames[k] else "G%d" % (k + 1)
    items.append((nm, "custom", k, c.DuplicateCurve()))
    k += 1

grids, lines, names = [], [], []
for nm, grp, i, c in items:
    rec = {"name": nm, "group": grp, "index": i, "start": ab.pt(c.PointAtStart, u), "end": ab.pt(c.PointAtEnd, u)}
    if not c.IsLinear(tol):
        rec["curve"] = ab.curve_record(c, u)
    grids.append(ab.pack("grid", **rec))
    lines.append(c)
    names.append(nm)
if len(set(names)) < len(names):
    notes.append("some grid names repeat")

# ── heads: a circle beyond the line end with the name upright in the project's axes ──


def head(c, name, at_end):
    p = c.PointAtEnd if at_end else c.PointAtStart
    d = c.TangentAtEnd if at_end else -c.TangentAtStart
    centre = p + d * R
    pl = rg.Plane(centre, X, Y)
    txt = ab.text_curves(name, pl, D * 0.5)
    bb = rg.BoundingBox.Empty
    for t in txt:
        bb.Union(t.GetBoundingBox(pl))
    w = bb.Max.X - bb.Min.X if bb.IsValid else 0
    if w > D * 0.72:  # long names shrink to fit
        f = rg.Transform.Scale(centre, D * 0.72 / w)
        for t in txt:
            t.Transform(f)
    return [rg.Circle(pl, R).ToNurbsCurve()] + txt


heads = []
want = {"both": (0, 1), "start": (0,), "end": (1,)}.get(ends, ())
for nm, grp, i, c in items:
    for at_end in want:
        heads += head(c, nm, at_end)

# ── intersections: straight grids through alien_bim (as every reader does), curved ones on their curves ──

recs = [ab.read(g)[0] for g in grids]
found = [(nm, p) for nm, p, a, b in ab.intersections([r for r in recs if "curve" not in r], u)]
for i, (ra, ca) in enumerate(zip(recs, lines)):
    for rb, cb in zip(recs[i + 1:], lines[i + 1:]):
        if ra["group"] == rb["group"] or ("curve" not in ra and "curve" not in rb):
            continue
        # same naming rule as alien_bim.intersections: letters first, else alphabetical
        fa, fb = (ra, rb) if ra["group"] == "letters" or (rb["group"] != "letters" and str(ra["name"]) < str(rb["name"])) else (rb, ra)
        for x in rg.Intersect.Intersection.CurveCurve(ca, cb, tol, tol) or []:
            found.append(("%s/%s" % (fa["name"], fb["name"]), x.PointA))
points = [p for _, p in found]
point_names = [n for n, _ in found]

# ── dimensions: bay and overall strings on the start sides, in millimetres ──

dims = []
th = 2.5 * paper  # dimension text
row_gap = 7 * paper


def dim_string(ps, axis, side, off):
    """Bay and overall dimension lines along `axis`, `off` (model units) out on `side` from the origin line, with
    oblique ticks and the sizes in mm above the line as read (text reads along the axis, never mirrored)."""
    out = []
    up = rg.Vector3d.CrossProduct(splane.ZAxis, axis)
    tick = 1.5 * paper
    for row, vals in enumerate((ps, [ps[0], ps[-1]] if len(ps) > 2 else [])):
        if not vals:
            continue
        pts = [splane.Origin + axis * (v * u) + side * (off + row * row_gap) for v in vals]
        out.append(rg.LineCurve(pts[0] - axis * tick, pts[-1] + axis * tick))
        slash = (axis + up) * (tick * 0.7071)
        for p in pts:
            out.append(rg.LineCurve(p - slash, p + slash))
        for a, b, va, vb in zip(pts, pts[1:], vals, vals[1:]):
            out += ab.text_curves("%d" % round((vb - va) * 1000), rg.Plane((a + b) / 2 + up * paper, axis, up), th, "center", "bottom")
    return out


if dimensions:
    for ps, axis, side, lo in ((xs, X, -Y, ylo), (ys, Y, -X, xlo)):
        if len(ps) < 2:
            continue
        edge = -lo * u            # the outermost grid across, measured out along side from the origin line
        reach = ext + (D if 0 in want else 0)
        rows = 2 if len(ps) > 2 else 1
        # inside the extension when it is long enough, else beyond the heads with witness lines out to them
        base = 7 * paper if ext >= (7 + rows * 7 + 3) * paper else reach + 5 * paper
        dims += dim_string(ps, axis, side, edge + base)
        if base > ext:
            for v in ps:
                p = splane.Origin + axis * (v * u) + side * edge
                dims.append(rg.LineCurve(p + side * (reach + paper), p + side * (base + (rows - 1) * row_gap + 2 * paper)))

bx, by = (xhi - xlo), (yhi - ylo)
info = "%d grids: %d along X (%s), %d along Y (%s)%s; %.2f x %.2f m; %d intersections; heads %.0f mm at 1:%d (%.2f m)" % (
    len(items), len(xs), "-".join([x_names[0], x_names[-1]]) if xs else "none", len(ys),
    "-".join([y_names[0], y_names[-1]]) if ys else "none", ", %d custom" % k if k else "", bx, by, len(points),
    bubble or 10.0, den, D / u)
if notes:
    info += "\n" + "\n".join(notes)
