# NODE_INPUTS: thing:str | "what to draw, and in plan or elevation" default="a queen bed with pillows, in plan", style:str | "drawing style (optional): fills, hatches, how much detail" default=""
# NODE_INPUTS: image:str | "reference image: drop one on the node, or a file path or URL (optional)" default="" image, detail:str | "how much to draw" default="detailed" options="simple|detailed"
# NODE_INPUTS: variant:int | "another design for the same request" min=0 max=50 default=0
# NODE_INPUTS: generate:bool | "draw it with the model (paid); a design drawn before comes back free from the cache" button
# NODE_INPUTS: width:float | "width (m, along X); 0 = as drawn" min=0 max=30 default=0, depth:float | "depth (m, along Y: length in plan, height in elevation); 0 = as drawn" min=0 max=30 default=0
# NODE_INPUTS: plane:Plane | "where it goes (default: world XY at the origin; an XZ plane stands an elevation up)"
# NODE_OUTPUTS: curves:list[Curve] | "all linework, outlines of fills and hatches included", fills:list[Brep] | "filled areas (planar)", hatches:list[Geometry] | "a solid hatch per fill and the pattern hatches, for baking"
# NODE_OUTPUTS: colours:list[Color] | "preview colour per fill", names:list[str] | "element name per curve", size:str | "width x depth (m)", info:str | "model, cost, attempts, elements", code:str | "the generated build code"
# CATEGORY: Create
# 2D object: describe a flat thing (or give a picture), press Generate, and a model writes RhinoCommon code that draws it at real size: plan symbols, cars, trees, people, fixtures, patterns.
#
# Only Generate calls the model: changing the description waits for it, and a design drawn before comes back free from
# the cache. Width and depth resize the drawn design without asking the model again (their sliders fit the design after
# a build and reset when the design changes). The code runs with only `rg` (Rhino.Geometry) and `math`. Plan drawings
# are centred on the plane's origin; elevations (anything drawn standing on y = 0, or placed on an upright plane) stand
# on it. Each design is saved once to the Alien asset library as a drawing (in metres, as drawn), so the panel can bake
# it as a block.
import math

import Rhino
import Rhino.Geometry as rg

import alien_ai
import alien_assets
import alien_codegen as cg

DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_THING = "a queen bed with pillows, in plan"
CACHE = "objects2d"

# The default request's design (drawn by claude-sonnet-5.5), so a new node shows a bed without a model call.
SAMPLE_CODE = '''def build(rg, math):
    items = []
    P = rg.Point3d

    def rect(x0, y0, x1, y1):
        return rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(x0, x1), rg.Interval(y0, y1)).ToNurbsCurve()

    def rrect(x0, y0, x1, y1, r):
        c = rect(x0, y0, x1, y1)
        f = rg.Curve.CreateFilletCornersCurve(c, r, 0.001, 0.01)
        return f if f else c

    def line(a, b, c, d):
        return rg.LineCurve(P(a, b, 0), P(c, d, 0))

    # Queen mattress 1.52 x 2.03; frame slightly larger
    # Headboard at +Y, foot towards -Y
    items.append(("headboard", "fill", [rect(-0.88, 1.06, 0.88, 1.18)], {"tone": 0.5}))
    items.append(("frame", "fill", [rect(-0.82, -1.06, 0.82, 1.06)], {"tone": 0.9}))
    items.append(("mattress", "fill", [rect(-0.78, -1.02, 0.78, 1.02)], {"tone": 1.0}))

    # Pillows
    for i, x in enumerate((-0.38, 0.38)):
        p = rrect(x - 0.34, 0.62, x + 0.34, 1.0, 0.06)
        items.append(("pillow%d" % i, "fill", [p], {"tone": 0.95}))
        inner = rrect(x - 0.28, 0.67, x + 0.28, 0.95, 0.05)
        items.append(("pillow_inner%d" % i, "line", inner))

    # Duvet turned down: fold line and duvet area
    items.append(("duvet", "fill", [rect(-0.78, -1.02, 0.78, 0.5)], {"tone": 0.88}))
    items.append(("turndown", "fill", [rect(-0.78, 0.30, 0.78, 0.5)], {"tone": 0.97}))
    items.append(("fold", "line", line(-0.78, 0.30, 0.78, 0.30)))

    # Quilting lines
    for i in range(1, 4):
        x = -0.78 + i * 0.39
        items.append(("quilt_v%d" % i, "line", line(x, -1.02, x, 0.30)))
    for y in (-0.6, -0.2):
        items.append(("quilt_h", "line", line(-0.78, y, 0.78, y)))

    return items
'''

SYSTEM = """You write Python that draws one flat thing for an architectural drawing in Rhino 8, using RhinoCommon.

Reply with exactly one ```python code block defining:

def build(rg, math):
    items = []        # list of (name, kind, geometry) or (name, kind, geometry, style)
    ...
    return items

Rules:
- Units are metres, real size, in the XY plane (z = 0).
- Plan drawings (furniture, cars, trees, fixtures seen from above): centred on the origin, X across, Y up the page, the object's front or foot towards -Y. Elevations (people, trees or things seen from the side): standing on y = 0 (the ground), centred on x = 0, Y up. Patterns: a small field centred on the origin.
- kind is "line" (linework: geometry is a curve or a list of curves, open or closed), "fill" (a filled area: closed curves; give an outline and the holes inside it together in one list) or "hatch" (a pattern inside closed curves).
- Fills and hatches get their boundary drawn as linework automatically: don't add their outlines again as lines. Later items draw over earlier ones.
- style (optional dict): fill {"tone": 0-1 (0 black, 1 white; default 0.8)} or {"colour": [r, g, b]}; hatch {"pattern": "lines"|"cross"|"dash"|"grid60"|"plus"|"squares", "spacing": metres between lines (e.g. 0.03), "angle": degrees}.
- Geometry helpers:
  rg.LineCurve(rg.Point3d(x0, y0, 0), rg.Point3d(x1, y1, 0))
  rg.PolylineCurve([rg.Point3d(...), ...])   (repeat the first point to close it)
  rg.Circle(rg.Point3d(x, y, 0), r).ToNurbsCurve(); rg.Arc(p_start, p_mid, p_end).ToNurbsCurve()
  rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(x0, x1), rg.Interval(y0, y1)).ToNurbsCurve()
  rg.Ellipse(rg.Plane(rg.Point3d(x, y, 0), rg.Vector3d.ZAxis), rx, ry).ToNurbsCurve()
  rg.Curve.CreateInterpolatedCurve(points, 3) for smooth open shapes; closed: rg.Curve.CreateInterpolatedCurve(points + [points[0]], 3, rg.CurveKnotStyle.ChordPeriodic)
  rounded rectangle: rg.Curve.CreateFilletCornersCurve(rectangle_curve, radius, 0.001, 0.01)
  move: c.Translate(rg.Vector3d(dx, dy, 0)); turn: c.Rotate(radians, rg.Vector3d.ZAxis, rg.Point3d(x, y, 0)); mirror a copy: c2 = c.DuplicateCurve(); c2.Transform(rg.Transform.Mirror(rg.Plane.WorldYZ))
- Draw it the way an architect's CAD library symbol looks at 1:50: clean outlines, real dimensions, and the features that make it recognisable (a bed's pillows and turned-down sheet; a car's windscreen, bonnet, roof and mirrors; a tree canopy's lobed edge and trunk; a person's clear silhouette). "simple" detail means the outline and one or two features; "detailed" adds the characteristic inner lines. 5 to 60 items.
- Only the names rg and math exist: no imports, no files, no print. Use 0.001 as tolerance. Check results are not None.
- Return the list. Don't add anything to a document."""


def build_elements(code):
    fn, _ = cg.run_code(code, "build")
    return cg.elements(fn(rg, math), 0.002)


def judge(reply):
    """A reply -> its code, if the code runs and draws something of a plausible size (else raise for the model)."""
    code = cg.extract_code(reply)
    if code is None:
        raise ValueError("Your reply had no ```python block defining build(rg, math).")
    els = build_elements(code)
    box = cg.bounds(els)
    dims = box.Max - box.Min
    if max(dims.X, dims.Y) > 200 or max(dims.X, dims.Y) < 0.02:
        raise ValueError("the drawing is %.2f x %.2f m overall, which is not a plausible real size; use metres" % (dims.X, dims.Y))
    problem = cg.check_counts(els)
    if problem:
        raise ValueError(problem)
    return code


thing = (thing or "").strip() or DEFAULT_THING
detail = detail if detail in ("simple", "detailed") else "detailed"
model = alien_ai.setting("model2d") or alien_ai.setting("model3d") or DEFAULT_MODEL
doc = Rhino.RhinoDoc.ActiveDoc
units = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
tol = doc.ModelAbsoluteTolerance if doc else 0.001

curves, fills, hatches, colours, names, size, code_out = [], [], [], [], [], "", ""
NODE_CONTROLS = {}
info = ""
img_part, img_hash, img_file = cg.reference(image)
# What the model is asked for. Sizes are not part of it: they resize the result, so sliders never call the model.
spec = "Draw: %s.%s Detail: %s.%s" % (
    thing, (" Style: %s." % style.strip()) if style and style.strip() else "", detail,
    " Draw it from the reference image." if img_part else "")
design = alien_ai.key_of(model, spec, img_hash, int(variant or 0))

saved = cg.cached(CACHE, design)
source, cost, attempts = None, 0.0, 0
if saved:
    code, attempts, source = saved["code"], saved["attempts"], "cache"
elif generate:
    code, _, cost, attempts = cg.ask(SYSTEM, spec, img_part, model, int(variant or 0), judge)
    source = "model"
    cg.store(CACHE, design, {"code": code, "attempts": attempts, "cost": cost, "spec": spec, "model": model})
elif (SAMPLE_CODE and thing == DEFAULT_THING and not img_part and not (style or "").strip() and not variant
      and detail == "detailed"):
    code, source = SAMPLE_CODE, "sample"

if source is None:
    info = ("Press Generate to draw '%s' with %s (about US$0.01-0.03; nothing is sent until you do). "
            "Designs drawn before come back free." % (thing, model))
    print("Waiting for Generate")
else:
    els = build_elements(code)
    # Normalise: centred in X; centred in Y too, unless it stands on y = 0 (an elevation keeps its ground line).
    box = cg.bounds(els)
    w0, d0 = box.Max.X - box.Min.X, box.Max.Y - box.Min.Y
    upright = plane is not None and abs(plane.ZAxis.Z) < 0.5  # an XZ-like plane: an elevation, whatever was drawn
    standing = upright or abs(box.Min.Y) <= 0.02 * max(d0, 1e-6)
    local = cg.transform(els, rg.Transform.Translation(-(box.Min.X + box.Max.X) / 2.0,
                                                       -box.Min.Y if standing else -(box.Min.Y + box.Max.Y) / 2.0, 0))
    # Resize each axis on its own (0 keeps the drawn size), then scale to model units and place on the plane.
    fx = width / w0 if width and width > 0 and w0 > 1e-6 else 1.0
    fy = depth / d0 if depth and depth > 0 and d0 > 1e-6 else 1.0
    target = plane if plane is not None else rg.Plane.WorldXY
    place = (rg.Transform.PlaneToPlane(rg.Plane.WorldXY, target) * rg.Transform.Scale(rg.Point3d.Origin, units)
             * rg.Transform.Scale(rg.Plane.WorldXY, fx, fy, 1.0))
    curves, names, fills, colours, hatches, _ = cg.drawing(cg.transform(local, place), units, tol)
    size = "%.2f x %.2f m" % (w0 * fx, d0 * fy)
    drawn = "%.2f x %.2f m" % (w0, d0)
    code_out = code

    for axis, n in (("width", w0), ("depth", d0)):
        lo, hi = cg.nice_range(n)
        NODE_CONTROLS[axis] = {"min": round(lo, 3), "max": round(hi, 3), "value": round(n, 2 if n >= 0.2 else 3),
                               "decimals": 2 if n >= 0.2 else 3, "key": design}

    # The library keeps each design once, as drawn (metres, on world XY): resizing never makes a new asset.
    params = {"variant": int(variant or 0), "model": model, "image": img_hash}
    if source == "model" or not alien_assets.exists("drawing", spec, params):
        try:
            lc, ln, lf, lcol, lh, lhcol = cg.drawing(local, 1.0, 1e-5)
            alien_assets.save(
                "drawing", thing, spec, params, "object_2d",
                files=dict(img_file, **{"build.py": code.encode()}), geometry=lc + lh, names=ln + ["hatch"] * len(lh),
                colours=[None] * len(lc) + lhcol,
                thumbnail=cg.thumbnail(local),
                extra={"size": drawn, "view": "elevation" if standing else "plan",
                       "inputs": {"thing": thing, "style": style or "", "image": image or "", "detail": detail,
                                  "variant": int(variant or 0)}})
        except Exception as ex:
            print("could not save to the asset library: %s" % ex)

    kinds = {}
    for _, kind, _, _ in els:
        kinds[kind] = kinds.get(kind, 0) + 1
    info = "\n".join(l for l in [
        "%s: %s, %s%s" % (thing, "elevation standing on the plane" if standing else "plan centred on the plane", size,
                          "" if (fx, fy) == (1.0, 1.0) else " (resized from %s as drawn)" % drawn),
        "%d elements (%s): %d curves, %d fills, %d hatches" % (
            len(els), ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())), len(curves), len(fills), len(hatches)),
        "Drawn by %s, %s" % (model, {"cache": "from cache (free)", "sample": "built-in sample (free)"}.get(
            source, "%d attempt%s, US$%.3f" % (attempts, "" if attempts == 1 else "s", cost))),
    ] if l)
code = code_out
