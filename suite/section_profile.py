# NODE_INPUTS: family:str | "catalogue family, or custom (shape and sizes below)" default="UB" options="UB|UC|PFC|SHS|RHS|CHS|EA|UA|glulam|LVL|sawn|concrete rect|concrete round|custom"
# NODE_INPUTS: size:str | "size in the family: UB 310 40.4, UC 250 72.9, PFC 250, SHS 150x9, RHS 200x100x6, CHS 168.3x7.1, EA 75x6, UA 100x75x8, glulam 360x85 (or GL17 360x85), LVL 300x63, sawn 90x45 (or F17 190x45), concrete 600x400, round 600; or a full name like 310UB40.4 (blank: a common size; info lists the family's sizes)" default=""
# NODE_INPUTS: shape:str | "custom: the section shape" default="I" options="I|C|T|L|box|tube|rect|circle"
# NODE_INPUTS: d:float | "custom: depth (mm)" min=10 max=2000 default=300, b:float | "custom: width (mm; circle and tube: ignored)" min=10 max=2000 default=150
# NODE_INPUTS: t:float | "custom: wall thickness of box, tube and L (mm)" min=1 max=100 default=6
# NODE_INPUTS: tf:float | "custom: flange thickness of I, C and T (mm)" min=1 max=100 default=10, tw:float | "custom: web thickness of I, C and T (mm)" min=1 max=100 default=6
# NODE_INPUTS: material:str | "custom: material (sets kg/m and colour)" default="steel" options="steel|aluminium|timber|concrete"
# NODE_INPUTS: name:str | "profile name (blank: the catalogue name, or one made from the custom sizes)" default=""
# NODE_INPUTS: plane:Plane | "where the section drawing lies and the sample stands (default: world XY at the origin)"
# NODE_INPUTS: save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: profile:str | "profile record: wire into beams_columns' column or beam, or structure_edit's section", name:str | "profile name"
# NODE_OUTPUTS: outline:list[Curve] | "the section at true size on the plane: outer edge, then holes", fill:list[Brep] | "the section as a flat surface (for previews)", hatch | "solid hatch of the section (for baking)"
# NODE_OUTPUTS: dims:list[Curve] | "dimension lines, ticks and figures (mm) and the name label", sample:list[Brep] | "a 1 m length standing beside the drawing", colours:list[Color] | "colour per sample solid (also suits the fill)"
# NODE_OUTPUTS: info:str
# CATEGORY: Building elements
# Section profile: a structural section from the Australian catalogue (UB, UC, PFC, SHS, RHS, CHS, angles, glulam, LVL, sawn, concrete) or a custom shape, drawn at true size with dimensions, as a profile record for beams and columns.
#
# Pick a family and type a size in it ("310 40.4" under UB, "150x9" under SHS) or any full name ("310UB40.4"); a
# size that isn't in the catalogue snaps to the nearest one with a note, and an unreadable one falls back to the
# family's common size. Custom builds alien_bim's shapes from d, b, t, tf, tw in mm. The profile record carries the
# section spec (metres, as beams_columns and structure_edit use it) plus area, Ix, Iy, Zx, Zy and r computed from the
# drawn outline with AreaMassProperties: sharp corners and no root radii, so a few percent under the catalogue's
# figures for rolled and cold-formed steel. kg/m is the catalogue's where it has one. Ix is about the horizontal
# axis of the drawing (the strong axis of a UB), not the principal axes of an angle.
import math
import re
import time

import alien_bim as ab
import alien_sections as S

import Rhino
import Rhino.Geometry as rg
import System.Drawing

t0 = time.time()
u = ab.units()
tol = ab.tol()
notes = []

FAMILIES = ("UB", "UC", "PFC", "SHS", "RHS", "CHS", "EA", "UA", "glulam", "LVL", "sawn", "concrete rect", "concrete round", "custom")
COMMON = {"UB": "310UB40.4", "UC": "250UC72.9", "PFC": "250PFC", "SHS": "SHS 100x5", "RHS": "RHS 150x100x6",
          "CHS": "CHS 168.3x7.1", "EA": "EA 75x75x6", "UA": "UA 100x75x8", "glulam": "GL18 360x85", "LVL": "LVL 300x63",
          "sawn": "MGP10 90x45", "concrete rect": "Concrete 600x400", "concrete round": "Concrete 600 dia"}
CATALOGUE_FAMILY = {"concrete rect": "concrete", "concrete round": "concrete"}
STOCK = {"glulam": S.GLULAM, "LVL": S.LVL_SIZES, "sawn": S.SAWN}
SHAPES = {"i": "I", "c": "C", "t": "T", "l": "L", "box": "box", "tube": "tube", "rect": "rect", "circle": "circle"}


def has_words(text):
    """Whether a size names its own family or grade (letters other than the x between numbers or a mm unit)."""
    s = re.sub(r"(?<=\d)\s*[xX×*]\s*(?=\d)", " ", text)
    s = re.sub(r"(?<=\d)\s*mm\b", " ", s, flags=re.I)
    return bool(re.search(r"[A-Za-z]", s))


def compose(fam, text):
    """A bare size in a family -> a name alien_sections reads: '310 40.4' -> 310UB40.4, '150x9' -> SHS 150x9."""
    text = re.sub(r"(?<=\d)\s*mm\b", "", text, flags=re.I).strip()
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return None
    if fam in ("UB", "UC"):
        if len(nums) >= 2:
            return "%s%s%s" % (nums[0], fam, nums[1])
        x = float(nums[0])
        names = S.catalogue(fam)
        same = [n for n in names if x == int(x) and n.startswith("%d%s" % (x, fam))]
        if same:  # a depth: the lightest of that depth
            if len(same) > 1:
                notes.append("%s %s: the lightest of %s" % (fam, nums[0], ", ".join(same)))
            return same[0]
        best = min(names, key=lambda n: abs(S.CATALOGUE[n]["kg_m"] - x))  # else a mass (kg/m)
        notes.append("%s %s read as %s kg/m: %s" % (fam, nums[0], nums[0], best))
        return best
    if fam == "PFC":
        return "%sPFC" % nums[0]
    if fam in ("SHS", "RHS", "CHS", "EA", "UA"):
        return "%s %s" % (fam, text)
    if fam == "glulam":
        return "GL18 " + text
    if fam == "LVL":
        return "LVL " + text
    if fam == "sawn":
        return "MGP10 " + text
    if fam == "concrete rect":
        return "concrete " + text
    if len(nums) > 1:
        notes.append("a round section has one size: used %s" % nums[0])
    return "%s dia" % nums[0]  # concrete round


def fmt(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def sizes_text(fam):
    """The family's sizes, for info (the size field's suggestions)."""
    if fam in ("UB", "UC", "PFC", "SHS", "RHS", "CHS", "EA", "UA"):
        names = S.catalogue(fam)
        return "%s sizes (%d, lightest first): %s" % (fam, len(names), ", ".join(names))
    if fam in STOCK:
        rows = sorted(STOCK[fam])
        return "%s stock sizes d x b mm (%d): %s; any grade %s" % (
            fam, len(rows), ", ".join("%dx%d" % r for r in rows),
            "/".join(k for k, v in S.TIMBER.items() if v[0] == fam))
    if fam.startswith("concrete"):
        return "concrete: any size in mm from 50 to 5000 (%s)" % ("600x400, 450x450" if fam.endswith("rect") else "450, 600, 900")
    return "custom: I, C, T, L, box, tube, rect or circle from d, b, t, tf, tw (mm)"


def clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def custom_spec():
    shp = SHAPES.get(str(shape or "").strip().lower())
    if shp is None:
        notes.append("shape '%s' unknown: used I" % shape)
        shp = "I"
    mat = str(material or "steel").strip().lower()
    if mat not in S.DENSITY:
        notes.append("material '%s' unknown: used steel" % material)
        mat = "steel"
    D, B = clamp(d, 1.0, 5000.0, 300.0), clamp(b, 1.0, 5000.0, 150.0)
    T, TF, TW = clamp(t, 0.5, 1000.0, 6.0), clamp(tf, 0.5, 1000.0, 10.0), clamp(tw, 0.5, 1000.0, 6.0)
    for label, given, used in (("d", d, D), ("b", b, B), ("t", t, T), ("tf", tf, TF), ("tw", tw, TW)):
        if given is not None and abs(float(given) - used) > 1e-9:
            notes.append("%s %s mm is out of range: used %s" % (label, fmt(float(given)), fmt(used)))

    def thin(label, v, limit):
        if v >= limit:
            notes.append("%s %s mm leaves no section: cut to %s mm" % (label, fmt(v), fmt(limit * 0.9)))
            return limit * 0.9
        return v

    sp = {"shape": shp, "family": "custom", "material": mat, "grade": ""}
    if shp in ("circle", "tube"):
        B = D
    if shp in ("I", "C"):
        TF, TW = thin("tf", TF, D / 2.0), thin("tw", TW, B)
        sp.update(tf=TF, tw=TW)
        title = "%s %sx%s tf%s tw%s" % (shp, fmt(D), fmt(B), fmt(TF), fmt(TW))
    elif shp == "T":
        TF, TW = thin("tf", TF, D), thin("tw", TW, B)
        sp.update(tf=TF, tw=TW)
        title = "T %sx%s tf%s tw%s" % (fmt(D), fmt(B), fmt(TF), fmt(TW))
    elif shp == "L":
        T = thin("t", T, min(D, B))
        sp.update(t=T)
        title = "L %sx%sx%s" % (fmt(D), fmt(B), fmt(T))
    elif shp == "box":
        T = thin("t", T, min(D, B) * 0.4 / 0.9)  # alien_bim keeps a box's hole at least a fifth of its size
        sp.update(t=T)
        title = "Box %sx%sx%s" % (fmt(D), fmt(B), fmt(T))
    elif shp == "tube":
        T = thin("t", T, D / 2.0)
        sp.update(t=T)
        title = "Tube %sx%s" % (fmt(D), fmt(T))
    elif shp == "circle":
        title = "Round %s" % fmt(D)
    else:
        title = "Rect %sx%s" % (fmt(D), fmt(B))
    sp["name"] = "%s %s" % (mat.capitalize(), title)
    sp.update(d=D / 1000.0, b=B / 1000.0)
    for k in ("t", "tf", "tw"):
        if k in sp:
            sp[k] = round(sp[k] / 1000.0, 6)
    sp["kg_m"] = S._kg(S.area_mm2(sp), S.DENSITY[mat])
    return sp


# ── the section ──

fam = next((f for f in FAMILIES if f.lower() == str(family or "").strip().lower()), None)
if fam is None:
    notes.append("family '%s' unknown: used UB" % family)
    fam = "UB"
text = str(size or "").strip()
source = "catalogue"
if fam == "custom":
    spec, source = custom_spec(), "custom"
else:
    hint = "timber" if fam in STOCK else "concrete" if fam.startswith("concrete") else "steel"
    if not text:
        want = COMMON[fam]
    elif has_words(text) or text.lstrip().startswith("{"):
        want = text
    else:
        want = compose(fam, text)
    spec, note = S.parse(want, hint) if want else (None, "no size in '%s'" % text)
    if spec is None:
        notes.append("%s: used %s" % (note, COMMON[fam]))
        spec, _ = S.parse(COMMON[fam], hint)
    else:
        if note:
            notes.append(note)
        expect = CATALOGUE_FAMILY.get(fam, fam)
        if text.lstrip().startswith("{"):
            notes.append("size holds a profile record: used as it is")
        elif spec.get("family") != expect:
            notes.append("'%s' is %s, not %s: the name wins" % (text, spec.get("family"), fam))
        elif fam == "concrete round" and spec.get("shape") != "circle":
            notes.append("'%s' is a rectangle: pick concrete rect, or give one size for a round column" % text)
        if spec.get("family") in STOCK:  # timber reads any size; say when it isn't a stock one
            dm, bm = round(spec["d"] * 1000), round(spec["b"] * 1000)
            stock = STOCK[spec["family"]]
            if (dm, bm) not in stock:
                near = min(stock, key=lambda r: abs(r[0] - dm) / r[0] + abs(r[1] - bm) / r[1])
                notes.append("%dx%d is not a stock %s size (nearest %dx%d)" % (dm, bm, spec["family"], near[0], near[1]))
    if text.lstrip().startswith("{"):
        source = "record"
spec = {k: spec[k] for k in S.SPEC_KEYS if k in spec}
catalogue_name = spec["name"]
given_name = str(name or "").strip()  # `name` is also an output: keep what was typed
if given_name:
    spec["name"] = given_name


# ── properties, from the outline drawn in mm ──

mm = ab.section(spec, 1000.0)
props = {}
region = rg.Brep.CreatePlanarBreps(mm, 0.001)
if region and len(region) == 1:
    amp = rg.AreaMassProperties.Compute(region[0])
    if amp is not None and amp.Area > 0:
        c, sm = amp.Centroid, amp.CentroidCoordinatesSecondMoments  # X: integral of x^2 dA, Y: of y^2 dA, about the centroid
        bb = region[0].GetBoundingBox(True)
        A, Ix, Iy = amp.Area, sm.Y, sm.X
        cy, cx = max(bb.Max.Y - c.Y, c.Y - bb.Min.Y), max(bb.Max.X - c.X, c.X - bb.Min.X)
        props = {"area_mm2": round(A, 1), "ix_mm4": round(Ix), "iy_mm4": round(Iy), "zx_mm3": round(Ix / cy),
                 "zy_mm3": round(Iy / cx), "rx_mm": round(math.sqrt(Ix / A), 1), "ry_mm": round(math.sqrt(Iy / A), 1),
                 "centroid_mm": [round(c.X, 2) + 0.0, round(c.Y, 2) + 0.0]}
if not props:
    notes.append("the outline could not be measured: no area or second moments")

rec = dict(spec)
rec.update(props)
rec.update({"source": source, "catalogue": catalogue_name if source == "catalogue" else None,
            "props": "computed from the drawn outline (sharp corners, no root radii)"})
profile = ab.pack("profile", **rec)


# ── drawing: true size on the plane, dimensions in mm ──

def drawing(pl):
    """(outline, fill, hatch, dims, sample) for the spec on a plane, model units."""
    xf = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, pl)
    curves = ab.section(spec, u)
    hb, hd = S.halves(spec)
    hb, hd = hb * u, hd * u
    size = 2 * max(hb, hd)
    th = size * 0.07           # text height
    off = size * 0.16          # dimension lines this far off the section
    gap, ext, tick = size * 0.03, size * 0.03, size * 0.035
    round_ = S.shape_of(spec) in S.TUBE_SHAPES + S.ROUND_SHAPES
    dims = []

    def line(a, b_):
        dims.append(rg.LineCurve(rg.Point3d(a[0], a[1], 0), rg.Point3d(b_[0], b_[1], 0)))

    def ticks(p, q):
        for x, y in (p, q):  # architectural ticks: short 45 degree strokes
            line((x - tick, y - tick), (x + tick, y + tick))

    # width (below); diameter for round sections
    yb = -hd - off
    line((-hb, yb), (hb, yb))
    for x in (-hb, hb):
        line((x, -hd - gap), (x, yb - ext))
    ticks((-hb, yb), (hb, yb))
    dims.extend(ab.text_curves(("Ø" if round_ else "") + fmt(2 * hb / u * 1000), rg.Plane(rg.Point3d(0, yb + th * 0.5, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis), th, "center", "bottom"))
    xd = hb + off
    if not round_:  # depth (right), read from the right
        line((xd, -hd), (xd, hd))
        for y in (-hd, hd):
            line((hb + gap, y), (xd + ext, y))
        ticks((xd, -hd), (xd, hd))
        dims.extend(ab.text_curves(fmt(2 * hd / u * 1000), rg.Plane(rg.Point3d(xd - th * 0.5, 0, 0), rg.Vector3d.YAxis, -rg.Vector3d.XAxis), th, "center", "bottom"))
    # label: the name, then thicknesses and mass
    parts = ["%s %s" % (k, fmt(spec[k] * 1000)) for k in ("tf", "tw", "t") if spec.get(k)]
    parts.append("%s kg/m" % fmt(spec.get("kg_m") or 0))
    y = yb - th * 1.2
    label = ab.text_curves(spec["name"], rg.Plane(rg.Point3d(0, y, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis), th * 1.3, "center", "top")
    y -= th * 1.3 * 1.5
    label += ab.text_curves("  ".join(parts), rg.Plane(rg.Point3d(0, y, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis), th * 0.85, "center", "top")
    dims.extend(label)
    for c in curves + dims:
        c.Transform(xf)
    fill = list(rg.Brep.CreatePlanarBreps(curves, tol) or [])
    d_ = Rhino.RhinoDoc.ActiveDoc
    solid_index = d_.HatchPatterns.FindName("Solid").Index if d_ and d_.HatchPatterns.FindName("Solid") else 0
    hatch_ = list(rg.Hatch.Create(curves, solid_index, 0.0, 1.0, tol) or [])
    # a 1 m sample standing beside the drawing, its section turned as drawn (depth along the plane's Y)
    xs = xd + th * 2.5 + hb + size * 0.1
    m = ab.member(rg.Point3d(xs, 0, 0), rg.Point3d(xs, 0, 1.0 * u), spec)
    sample_ = []
    if m is not None:
        m.Transform(xf)
        sample_.append(m)
    return curves, fill, hatch_, dims, sample_


base = plane if isinstance(plane, rg.Plane) and plane.IsValid else rg.Plane.WorldXY
outline, fill, hatch, dims, sample = drawing(base)
colours = [ab.colour(spec.get("material")) for _ in sample]
name = spec["name"]

saved = ""
if save:
    import alien_assets
    o2, f2, h2, d2, _ = drawing(rg.Plane.WorldXY)  # the library keeps it at the origin
    ink = ab.colour("paint")
    c = ab.colour(spec.get("material"))
    tone = System.Drawing.Color.FromArgb(*[int(v + (255 - v) * 0.45) for v in (c.R, c.G, c.B)])  # lighter: the thumbnail darkens fills
    inputs = {"family": fam, "name": given_name}
    if fam == "custom":
        inputs.update({"shape": shape, "d": d, "b": b, "t": t, "tf": tf, "tw": tw, "material": material})
    else:
        inputs["size"] = text
    alien_assets.save_type("profile", spec["name"], profile, "section_profile", inputs,
                           geometry=h2 + o2 + d2, colours=[tone for _ in h2] + [ink for _ in o2 + d2],
                           summary="%s, %s kg/m" % (catalogue_name, fmt(spec.get("kg_m") or 0)))
    saved = "saved to the library as %s" % spec["name"]

sz = "%s x %s mm" % (fmt(spec["d"] * 1000), fmt(spec["b"] * 1000)) if S.shape_of(spec) not in S.TUBE_SHAPES + S.ROUND_SHAPES else "Ø%s mm" % fmt(spec["d"] * 1000)
thk = ", ".join("%s %s" % (k, fmt(spec[k] * 1000)) for k in ("tf", "tw", "t") if spec.get(k))
fam_of = spec.get("family")
kg_from = ("from the record" if source == "record" else "catalogue" if fam_of in ("UB", "UC", "PFC") else
           "AS/NZS 1163 nominal geometry" if fam_of in ("SHS", "RHS", "CHS", "EA", "UA") else
           "typical density" if fam_of in STOCK else "2,500 kg/m3" if fam_of == "concrete" else
           "outline area x %s kg/m3" % fmt(S.DENSITY.get(str(spec.get("material")), S.STEEL)))
desc = str(fam_of)
if spec.get("grade") and spec.get("grade") != fam_of:
    desc += " " + spec["grade"]
if str(spec.get("material")) not in desc.lower():
    desc += " " + str(spec.get("material"))
lines = ["%s: %s, %s%s, %s kg/m (%s)" % (spec["name"], desc, sz, " (%s)" % thk if thk else "", fmt(spec.get("kg_m") or 0), kg_from)]
if spec["name"] != catalogue_name:
    lines.append("Section: %s" % catalogue_name)
if props:
    lines.append("Computed from the outline (sharp corners, no root radii): A %s mm2, Ix %.4g x10^6 mm4, Iy %.4g x10^6 mm4, "
                 "Zx %.4g x10^3 mm3, Zy %.4g x10^3 mm3, rx %s mm, ry %s mm" % (
                     fmt(props["area_mm2"]), props["ix_mm4"] / 1e6, props["iy_mm4"] / 1e6, props["zx_mm3"] / 1e3,
                     props["zy_mm3"] / 1e3, fmt(props["rx_mm"]), fmt(props["ry_mm"])))
lines.append(sizes_text(fam))
lines.append("%d ms" % ((time.time() - t0) * 1000))
info = "\n".join(lines + notes + ([saved] if saved else []))
