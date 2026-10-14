# NODE_INPUTS: description:str | "the annotation symbol to draw" default="north arrow: a circle with a solid arrow and a bold N", text:str | "text for the symbol, values separated by | (e.g. A | A201); empty = the sample text" default=""
# NODE_INPUTS: image:str | "reference image: drop one on the node, or a file path or URL (optional)" default="" image
# NODE_INPUTS: variant:int | "another design for the same request" min=0 max=50 default=0
# NODE_INPUTS: generate:bool | "draw it with the model (paid); a design drawn before comes back free from the cache" button
# NODE_INPUTS: site:object | "site record from bim_site (optional): drawing scale, and true north for north arrows"
# NODE_INPUTS: scale:str | "drawing scale: the symbol is drawn at paper size and scaled to the model (1:1 = paper mm)" default="site" options="site|1:1|1:20|1:50|1:100|1:200|1:250|1:500|1:1000"
# NODE_INPUTS: size:float | "size on paper (mm, the longer side); 0 = as designed" min=0 max=150 default=0
# NODE_INPUTS: points:list[Point3d] | "where to place copies (default: the plane's origin)", rotation:float | "rotation (degrees, CCW)" min=-180 max=180 default=0 decimals=1
# NODE_INPUTS: plane:Plane | "orientation, and the position when no points are given (default: world XY)"
# NODE_INPUTS: block:bool | "define or update the Rhino block 'Alien · symbol · <name>' and insert it at the points" button
# NODE_OUTPUTS: curves:list[Curve] | "linework, text and outlines, placed and scaled to the model", fills:list[Brep] | "solid areas (arrows, text, filled halves)", hatches:list[Geometry] | "a solid hatch per fill and the pattern hatches, for baking"
# NODE_OUTPUTS: info:str | "name, text slots, paper and model size, model, cost", code:str | "the generated build code"
# CATEGORY: Create
# Symbol: describe an annotation symbol (north arrow, section marker, level marker, door tag, grid bubble, revision cloud, scale bar, logo) and a model draws it at paper size with text slots, placed at the drawing scale.
#
# Only Generate calls the model; a symbol drawn before comes back free from the cache. The text, scale, size, points,
# rotation and plane only re-run the cached code: the model writes build(rg, math, text, label) in paper millimetres,
# where text[i] fills the symbol's slots (its SLOTS list names them and gives sample values), label() draws text as
# closed outlines and SCALE (the drawing scale) serves scale bars. Copies are scaled by the drawing scale (the site
# record's, or 1:100 without one); north arrows turn to the site's true north. Block (a button) defines or updates the Rhino block "Alien · symbol · <name>" in paper
# mm and inserts scaled instances at the points on the "Alien symbols" layer; pressing it again replaces the instances
# that block placed before (tagged "alien.symbol"). Each design is saved once to the asset library (kind symbol, mm).
import math
import os
import re

import Rhino
import Rhino.Geometry as rg

import alien_ai
import alien_assets
import alien_bim as ab
import alien_codegen as cg

DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_DESCRIPTION = "north arrow: a circle with a solid arrow and a bold N"
CACHE = "symbols"
TAG = "alien.symbol"
LAYER = "Alien symbols"

# The default request's design (drawn by claude-sonnet-5.5), so a new node shows a north arrow without a model call.
SAMPLE_CODE = '''NAME = "North arrow"
SLOTS = []

def build(rg, math, text, label):
    items = []
    r = 10.0
    circle = rg.Circle(rg.Point3d(0, 0, 0), r).ToNurbsCurve()
    if circle is not None:
        items.append(("circle", "line", circle))

    # solid arrow: half of the arrowhead filled, pointing to +Y
    arrow = rg.PolylineCurve([
        rg.Point3d(0, 9, 0),
        rg.Point3d(3, -5, 0),
        rg.Point3d(0, -3, 0),
        rg.Point3d(-3, -5, 0),
        rg.Point3d(0, 9, 0),
    ])
    items.append(("arrow", "fill", [arrow]))

    # bold N above the circle
    n = label("N", 0, r + 1.5, 4.0, align="center", valign="bottom", bold=True)
    if n:
        items.append(("N", "fill", list(n)))
    return items
'''

SYSTEM = """You write Python that draws one annotation symbol for architectural drawings in Rhino 8, using RhinoCommon.

Reply with exactly one ```python code block containing:

NAME = "Section marker"                          # a short name for the symbol
SLOTS = [("section", "A"), ("sheet", "A201")]    # the text it carries: (what, sample value) pairs; [] when it has none

def build(rg, math, text, label):
    items = []        # list of (name, kind, geometry) or (name, kind, geometry, style)
    ...
    return items

Rules:
- Units are paper millimetres at printed size: a north arrow about 15-25 mm, a section or elevation marker head 10-14 mm, a grid bubble 8-12 mm, a door or window tag 6-10 mm, text 2.5-3.5 mm high, a scale bar 50-100 mm long. Draw in the XY plane (z = 0), +Y up the page (a north arrow points to +Y).
- The origin is the insertion point: the centre of a bubble, arrow, tag or marker head, the point a level marker marks, the left end of a scale bar.
- text[i] is the value for SLOTS[i] (the sample when the user gives none); never hard-code those values in build. Size text so the longest likely value fits.
- SCALE is the drawing scale's denominator (100 at 1:100: 1 paper mm = SCALE mm real). Use it only for things measured in real units, like a scale bar's divisions and labels (pick round real lengths, e.g. 0, 1, 2, 5 m at 1:100, and convert them to paper mm).
- label(s, x, y, height, align="center", valign="middle", bold=False) returns closed outline curves of the string s, text height `height` mm (align left|center|right, valign top|middle|bottom, about the letters' extent; bold=True for a bold face). Add them as a "fill" for solid text.
- kind is "line" (curves, open or closed), "fill" (solid black areas from closed curves; give an outline and its holes together in one list) or "hatch" (a pattern inside closed curves). Fills and hatches get their boundary drawn automatically. Later items draw over earlier ones.
- style (optional dict): fill {"tone": 0-1 (0 black, 1 white; default 0)}; hatch {"pattern": "lines"|"cross"|"dash"|"grid60"|"plus"|"squares", "spacing": mm between lines, "angle": degrees}.
- Geometry helpers:
  rg.LineCurve(rg.Point3d(x0, y0, 0), rg.Point3d(x1, y1, 0))
  rg.PolylineCurve([rg.Point3d(...), ...])   (repeat the first point to close it)
  rg.Circle(rg.Point3d(x, y, 0), r).ToNurbsCurve(); rg.Arc(p_start, p_mid, p_end).ToNurbsCurve()
  rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(x0, x1), rg.Interval(y0, y1)).ToNurbsCurve()
  a filled half circle: rg.Curve.JoinCurves([rg.Arc(p0, pmid, p1).ToNurbsCurve(), rg.LineCurve(p1, p0)])[0]
  move: c.Translate(rg.Vector3d(dx, dy, 0)); turn: c.Rotate(radians, rg.Vector3d.ZAxis, rg.Point3d(x, y, 0))
- Follow common drafting conventions (AS 1100 / ISO 128 office standards): crisp solid black arrows and fills, thin circles and lines, text centred in bubbles, nothing decorative unless asked.
- Only the names rg, math, text, label and SCALE exist: no imports, no files, no print. Use 0.01 as tolerance. Check results are not None.
- Return the list. Don't add anything to a document."""


def parse_slots(scope):
    """SLOTS from the module -> [(what, sample)]."""
    out = []
    for s in scope.get("SLOTS") or []:
        if isinstance(s, (list, tuple)) and s:
            out.append((str(s[0]), str(s[1]) if len(s) > 1 else ""))
        elif isinstance(s, str):
            out.append((s, ""))
    return out


def build_symbol(code, values, den=100):
    """Run the code with text values (samples fill the gaps) and the drawing scale's denominator as SCALE ->
    (elements in paper mm, NAME, slots, text used)."""
    fn, scope = cg.run_code(code, "build", {"SCALE": den})
    slots = parse_slots(scope)
    used = cg.Slots([values[i] if i < len(values) and values[i] else sample for i, (_, sample) in enumerate(slots)]
                    + [v for v in values[len(slots):]])
    els = cg.elements(fn(rg, math, used, cg.label), 0.05)
    return els, str(scope.get("NAME") or ""), slots, used


def judge(reply):
    code = cg.extract_code(reply)
    if code is None:
        raise ValueError("Your reply had no ```python block defining build(rg, math, text, label).")
    els, _, _, _ = build_symbol(code, [])
    box = cg.bounds(els)
    dims = box.Max - box.Min
    if max(dims.X, dims.Y) > 400 or max(dims.X, dims.Y) < 1:
        raise ValueError("the symbol is %.1f x %.1f mm overall; draw it in paper millimetres at printed size" % (dims.X, dims.Y))
    problem = cg.check_counts(els, 3000)
    if problem:
        raise ValueError(problem)
    return code


def clean(s):
    return re.sub(r"[^\w\s\-\.,()&'/]", "", s).strip()[:60]


def define_block(name, geometry, colours, xforms, doc):
    """Create or update block `name` (geometry in model units), then replace the instances it placed before."""
    layer = doc.Layers.FindByFullPath(LAYER, -1)
    if layer < 0:
        new_layer = Rhino.DocObjects.Layer()
        new_layer.Name = LAYER
        layer = doc.Layers.Add(new_layer)
    attrs = []
    for c in colours:
        a = Rhino.DocObjects.ObjectAttributes()
        a.LayerIndex = layer
        if c is not None:
            a.ObjectColor = c
            a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
        attrs.append(a)
    idef = doc.InstanceDefinitions.Find(name)
    if idef is None:
        index = doc.InstanceDefinitions.Add(name, "Alien symbol (paper mm)", rg.Point3d.Origin, geometry, attrs)
        if index < 0:
            raise RuntimeError("Rhino refused the block definition '%s'" % name)
        updated = False
    else:
        index = idef.Index
        doc.InstanceDefinitions.ModifyGeometry(index, geometry, attrs)
        updated = True
    removed = 0
    for obj in list(doc.Objects.FindByUserString(TAG, name, True)):
        if doc.Objects.Delete(obj, True):
            removed += 1
    for xf in xforms:
        a = Rhino.DocObjects.ObjectAttributes()
        a.LayerIndex = layer
        a.SetUserString(TAG, name)
        doc.Objects.AddInstanceObject(index, xf, a)
    doc.Views.Redraw()
    return updated, removed


description = (description or "").strip() or DEFAULT_DESCRIPTION
model = alien_ai.setting("model2d") or alien_ai.setting("model3d") or DEFAULT_MODEL
doc = Rhino.RhinoDoc.ActiveDoc
units = ab.units()
tol = ab.tol()
s_rec = ab.site_of(site)
site_wired = bool(ab.first(site, "site"))

curves, fills, hatches, code_out = [], [], [], ""
info = ""
img_part, img_hash, img_file = cg.reference(image)
spec = "Symbol: %s.%s" % (description, " Draw it like the reference image." if img_part else "")
design = alien_ai.key_of(model, spec, img_hash, int(variant or 0))

saved = cg.cached(CACHE, design)
source, cost, attempts = None, 0.0, 0
if saved:
    code, attempts, source = saved["code"], saved["attempts"], "cache"
elif generate:
    code, _, cost, attempts = cg.ask(SYSTEM, spec, img_part, model, int(variant or 0), judge)
    source = "model"
    cg.store(CACHE, design, {"code": code, "attempts": attempts, "cost": cost, "spec": spec, "model": model})
elif SAMPLE_CODE and description == DEFAULT_DESCRIPTION and not img_part and not variant:
    code, source = SAMPLE_CODE, "sample"

if source is None:
    info = ("Press Generate to draw '%s' with %s (about US$0.01-0.03; nothing is sent until you do). "
            "Symbols drawn before come back free." % (description, model))
    print("Waiting for Generate")
else:
    values = [v.strip() for v in (text or "").split("|")] if (text or "").strip() else []
    # Drawing scale: 1:den means one paper mm is den mm in the world.
    pick = str(scale or "site")
    den = int(s_rec.get("scale") or 100) if pick == "site" else int(pick.split(":")[-1])
    den = max(1, den)
    els, name, slots, used = build_symbol(code, values, den)
    name = clean(name) or clean(description.split(":")[0]) or "symbol"
    box = cg.bounds(els)
    w0, h0 = box.Max.X - box.Min.X, box.Max.Y - box.Min.Y
    k_size = size / max(w0, h0) if size and size > 0 and max(w0, h0) > 1e-6 else 1.0

    per_mm = units / 1000.0                  # model units per real millimetre
    turn = float(rotation or 0.0)
    north = site_wired and "north" in (name + " " + description).lower()
    if north:
        turn += float(s_rec.get("north") or 0.0)
    turn = (turn + 180.0) % 360.0 - 180.0

    base = plane if plane is not None else rg.Plane.WorldXY
    spots = [p for p in (points or []) if p is not None] or [base.Origin]
    paper = rg.Transform.Scale(rg.Point3d.Origin, k_size)                       # paper mm, resized
    xforms = []
    for p in spots:
        at = rg.Plane(p, base.XAxis, base.YAxis)
        xforms.append(rg.Transform.PlaneToPlane(rg.Plane.WorldXY, at)
                      * rg.Transform.Rotation(math.radians(turn), rg.Point3d.Origin)
                      * rg.Transform.Scale(rg.Point3d.Origin, den * per_mm))
    for xf in xforms:
        c, _, f, _, h, _ = cg.drawing(cg.transform(els, xf * paper), den * per_mm, tol, fill_default=(0, 0, 0))
        curves += c
        fills += f
        hatches += h
    code_out = code

    # The library keeps each design once, in paper mm with its sample text (Bake block brings it in at 1:1).
    params = {"variant": int(variant or 0), "model": model, "image": img_hash}
    if source == "model" or not alien_assets.exists("symbol", spec, params):
        try:
            sample_els, _, _, _ = build_symbol(code, [], den)
            lc, ln, _, _, lh, lhcol = cg.drawing(sample_els, 1.0, 0.001, fill_default=(0, 0, 0))
            path = os.path.join(alien_ai.cache_dir(CACHE), design + ".3dm")
            cg.write_3dm(path, lc + lh, Rhino.UnitSystem.Millimeters, ln + ["fill"] * len(lh), [None] * len(lc) + lhcol)
            sb = cg.bounds(sample_els)
            alien_assets.save(
                "symbol", name, spec, params, "symbol",
                files=dict(img_file, **{"build.py": code.encode(), "geometry.3dm": cg.read_bytes(path)}),
                thumbnail=cg.thumbnail(sample_els, fill_default=(0, 0, 0)),
                extra={"size": "%.0f x %.0f mm on paper" % (sb.Max.X - sb.Min.X, sb.Max.Y - sb.Min.Y),
                       "slots": [{"what": w, "sample": s} for w, s in slots], "units": "paper mm",
                       "inputs": {"description": description, "image": image or "", "variant": int(variant or 0)}})
        except Exception as ex:
            print("could not save to the asset library: %s" % ex)

    block_note = ""
    if block and doc is not None:
        shown = [v for v in list(used)[:len(slots)] if v]
        bname = "Alien · symbol · " + name + (" · " + ", ".join(shown) if shown else "")
        if "SCALE" in code:  # drawn for this scale (a scale bar): another scale is another block
            bname += " · 1:%d" % den
        bname = bname[:120]
        geo = cg.transform(els, rg.Transform.Scale(rg.Point3d.Origin, per_mm) * paper)   # paper mm in model units
        bc, _, _, _, bh, bhcol = cg.drawing(geo, per_mm, tol, fill_default=(0, 0, 0))
        inst = [rg.Transform.PlaneToPlane(rg.Plane.WorldXY, rg.Plane(p, base.XAxis, base.YAxis))
                * rg.Transform.Rotation(math.radians(turn), rg.Point3d.Origin) * rg.Transform.Scale(rg.Point3d.Origin, den)
                for p in spots]
        updated, removed = define_block(bname, bc + bh, [None] * len(bc) + bhcol, inst, doc)
        block_note = "Block '%s' %s; %d instance%s placed on the '%s' layer%s" % (
            bname, "updated" if updated else "defined", len(inst), "" if len(inst) == 1 else "s", LAYER,
            " (replacing %d)" % removed if removed else "")

    pw, ph = w0 * k_size, h0 * k_size
    info = "\n".join(l for l in [
        "%s: %.1f x %.1f mm on paper%s, 1:%d -> %.2f x %.2f m in the model, %d cop%s%s" % (
            name, pw, ph, "" if k_size == 1.0 else " (resized from %.1f x %.1f)" % (w0, h0), den,
            pw * den / 1000.0, ph * den / 1000.0, len(spots), "y" if len(spots) == 1 else "ies",
            ", turned %.1f deg" % turn if turn else ""),
        "Turned to the site's true north (%.1f deg)" % float(s_rec.get("north") or 0.0) if north else "",
        ("Text: " + ", ".join("%s = %s" % (w, used[i] or "(empty)") for i, (w, _) in enumerate(slots))
         + "  (type values separated by |)") if slots else "No text slots",
        "Drawn by %s, %s" % (model, {"cache": "from cache (free)", "sample": "built-in sample (free)"}.get(
            source, "%d attempt%s, US$%.3f" % (attempts, "" if attempts == 1 else "s", cost))),
        block_note,
    ] if l)
code = code_out
