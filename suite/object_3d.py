# NODE_INPUTS: thing:str | "what to model" default="a dining chair", style:str | "style, materials, details (optional)" default=""
# NODE_INPUTS: image:str | "reference image: drop one on the node, or a file path or URL (optional)" default="" image, detail:str | "how much to model" default="detailed" options="simple|detailed"
# NODE_INPUTS: variant:int | "another design for the same request" min=0 max=50 default=0
# NODE_INPUTS: generate:bool | "build it with the model (paid); a design built before comes back free from the cache" button
# NODE_INPUTS: width:float | "width (m); 0 = as built" min=0 max=10 default=0, depth:float | "depth (m); 0 = as built" min=0 max=10 default=0
# NODE_INPUTS: height:float | "height (m); 0 = as built" min=0 max=10 default=0, plane:Plane | "where it stands, front facing the plane's -Y (default: world origin)"
# NODE_OUTPUTS: parts:list[Brep] | "the object's solids", names:list[str] | "part names", materials:list[str] | "material per part", colours:list[Color] | "preview colour per part"
# NODE_OUTPUTS: size:str | "width x depth x height (m)", info:str | "model, cost, attempts", code:str | "the generated build code"
# CATEGORY: Massing & objects
# 3D object: describe a thing (or give a picture), press Generate, and a model writes RhinoCommon code that builds it from solids at real-world size.
#
# Only Generate calls the model: changing the description waits for it, and a design built before comes back free
# from the cache. Width, depth and height resize the built design without asking the model again: after a build their
# sliders show its size and span half to twice it (NODE_CONTROLS), and they reset when the design changes. The code runs
# with only `rg` (Rhino.Geometry) and `math`; each design is saved once to the Alien asset library, at its built size.
import hashlib
import json
import math
import os
import re
import traceback

import Rhino
import Rhino.Geometry as rg
import System.Drawing

import alien_ai
import alien_assets

DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
ATTEMPTS = 3
DEFAULT_THING = "a dining chair"

# The default request's design (built by claude-sonnet-5.5), so a new node shows a chair without a model call.
SAMPLE_CODE = '''def build(rg, math):
    parts = []

    def box(name, mat, x0, x1, y0, y1, z0, z1):
        b = rg.Box(rg.Plane.WorldXY, rg.Interval(x0, x1), rg.Interval(y0, y1), rg.Interval(z0, z1)).ToBrep()
        if b is not None:
            parts.append((name, mat, b))

    # Dimensions
    w = 0.44          # overall width
    hw = w / 2
    yf = -0.21        # front edge of seat
    yb = 0.21         # back edge of seat
    lt = 0.04         # leg thickness
    seat_z0 = 0.42
    seat_z1 = 0.45

    # Seat (rounded corners)
    pts = [rg.Point3d(-hw, yf, 0), rg.Point3d(hw, yf, 0), rg.Point3d(hw, yb, 0),
           rg.Point3d(-hw, yb, 0), rg.Point3d(-hw, yf, 0)]
    pl = rg.PolylineCurve(pts)
    fc = rg.Curve.CreateFilletCornersCurve(pl, 0.03, 0.001, 0.01)
    seat = None
    if fc is not None:
        ex = rg.Extrusion.Create(fc, -0.04, True)
        if ex is not None:
            seat = ex.ToBrep()
            if seat is not None:
                seat.Translate(rg.Vector3d(0, 0, seat_z1))
                parts.append(("seat", "oak", seat))
    if seat is None:
        box("seat", "oak", -hw, hw, yf, yb, 0.41, seat_z1)

    # Front legs
    box("front left leg", "oak", -hw, -hw + lt, yf, yf + lt, 0, seat_z0)
    box("front right leg", "oak", hw - lt, hw, yf, yf + lt, 0, seat_z0)
    # Back legs (continue up as back posts)
    top = 0.90
    box("back left leg", "oak", -hw, -hw + lt, yb - lt, yb, 0, top)
    box("back right leg", "oak", hw - lt, hw, yb - lt, yb, 0, top)

    # Seat aprons
    ah0, ah1 = 0.34, 0.42
    box("front apron", "oak", -hw + lt, hw - lt, yf, yf + 0.02, ah0, ah1)
    box("back apron", "oak", -hw + lt, hw - lt, yb - 0.02, yb, ah0, ah1)
    box("left apron", "oak", -hw, -hw + 0.02, yf + lt, yb - lt, ah0, ah1)
    box("right apron", "oak", hw - 0.02, hw, yf + lt, yb - lt, ah0, ah1)

    # Stretchers
    box("left stretcher", "oak", -hw + 0.01, -hw + 0.03, yf + lt, yb - lt, 0.15, 0.18)
    box("right stretcher", "oak", hw - 0.03, hw - 0.01, yf + lt, yb - lt, 0.15, 0.18)
    box("rear stretcher", "oak", -hw + lt, hw - lt, yb - 0.03, yb - 0.01, 0.15, 0.18)

    # Backrest rails and slats
    box("top rail", "oak", -hw, hw, yb - lt, yb, 0.82, top)
    box("lower back rail", "oak", -hw + lt, hw - lt, yb - 0.03, yb - 0.01, 0.58, 0.64)
    n = 3
    gap = (w - 2 * lt) / n
    for i in range(n):
        cx = -hw + lt + gap * (i + 0.5)
        box("back slat %d" % (i + 1), "oak", cx - 0.02, cx + 0.02, yb - 0.03, yb - 0.01, 0.64, 0.82)

    # Seat cushion pad
    box("seat pad", "fabric", -hw + 0.03, hw - 0.03, yf + 0.03, yb - 0.05, seat_z1, seat_z1 + 0.02)

    return parts
'''

SYSTEM = """You write Python that builds a 3D model of one object for an architectural model in Rhino 8, using RhinoCommon.

Reply with exactly one ```python code block defining:

def build(rg, math):
    parts = []        # list of (name, material, brep)
    ...
    return parts

Rules:
- Units are metres. Z is up. The object stands on the floor (lowest point at z = 0), centred on the origin in plan, with its front facing -Y.
- Use real-world dimensions typical for this kind of object unless told otherwise (e.g. dining chair seat 0.45 m high and 0.42-0.46 m deep; dining table top 0.74 m; toilet pan rim 0.40 m and about 0.70 m deep including cistern; sofa seat 0.42 m; bed mattress top 0.50-0.55 m).
- Build from simple, robust solids, each a valid closed Brep:
  rg.Box(rg.Plane.WorldXY, rg.Interval(x0, x1), rg.Interval(y0, y1), rg.Interval(z0, z1)).ToBrep()
  rg.Cylinder(rg.Circle(rg.Plane(rg.Point3d(x, y, z), rg.Vector3d.ZAxis), r), h).ToBrep(True, True)
  rg.Sphere(rg.Point3d(x, y, z), r).ToBrep(); rg.Cone(plane, h, r).ToBrep(True)
  rg.Extrusion.Create(closed_planar_curve, h, True).ToBrep()   (a negative h extrudes the other way)
  rg.Brep.CreatePipe(curve, r, False, rg.PipeCapMode.Round, True, 0.001, 0.01)[0]
  rg.RevSurface.Create(profile_curve, rg.Line(p0, p1)).ToBrep() then .CapPlanarHoles(0.001) if needed
  rg.Brep.CreateFromLoft(closed_section_curves, rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False)[0].CapPlanarHoles(0.001)
  Rounded shapes: rg.Curve.CreateFilletCornersCurve(polyline_curve, radius, 0.001, 0.01) on a closed rectangle, then extrude.
- Avoid boolean operations and Brep fillets (they often fail). Overlapping or touching parts are fine. Check results are not None before using them.
- Name parts plainly ("seat", "front left leg") and give each a one-word material ("oak", "walnut", "steel", "chrome", "ceramic", "fabric", "leather", "glass", "plastic", "concrete", "marble", "rattan").
- Model what makes the object recognisable at 1:20: 4 to 40 parts, proportions right, no screws or text. "simple" detail means the essential forms only.
- Only the names rg and math exist: no imports, no files, no network, no print. Use 0.001 as tolerance.
- Return the list. Don't add anything to a document."""

MATERIAL_COLOURS = {
    "oak": (196, 160, 112), "walnut": (110, 74, 48), "timber": (180, 140, 95), "wood": (180, 140, 95),
    "plywood": (205, 175, 130), "bamboo": (205, 185, 120), "rattan": (190, 155, 100), "cork": (180, 140, 100),
    "steel": (150, 155, 160), "chrome": (205, 210, 215), "aluminium": (190, 192, 196), "brass": (196, 165, 85),
    "iron": (70, 72, 75), "metal": (150, 155, 160), "ceramic": (240, 240, 236), "porcelain": (244, 244, 240),
    "fabric": (120, 130, 150), "leather": (120, 72, 45), "glass": (190, 220, 225), "plastic": (225, 225, 225),
    "concrete": (165, 165, 160), "marble": (230, 228, 222), "stone": (170, 165, 155), "foam": (230, 225, 210),
    "rubber": (40, 40, 40), "paper": (245, 240, 230), "terrazzo": (215, 210, 200),
}

SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len", "list", "map",
    "max", "min", "pow", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip",
    "Exception", "ValueError", "TypeError", "ZeroDivisionError", "IndexError", "KeyError", "AttributeError", "None", "True", "False",
) if (n in __builtins__ if isinstance(__builtins__, dict) else hasattr(__builtins__, n))}


def extract_code(reply):
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", reply, re.S)
    code = next((b for b in blocks if "def build" in b), None)
    if code is None and "def build" in reply:
        code = reply[reply.index("def build"):]
    return code


def run_build(code):
    """Execute the generated code with only rg and math -> list of (name, material, Brep)."""
    scope = {"__builtins__": SAFE_BUILTINS, "rg": rg, "math": math}
    exec(compile(code, "build.py", "exec"), scope)
    if "build" not in scope:
        raise ValueError("the code does not define build(rg, math)")
    parts = scope["build"](rg, math)
    clean = []
    for i, item in enumerate(parts or []):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            raise ValueError("part %d is not a (name, material, brep) tuple" % i)
        name, material, geom = item[0], item[1], item[2]
        if isinstance(geom, rg.Extrusion):
            geom = geom.ToBrep()
        if isinstance(geom, (rg.Surface,)):
            geom = geom.ToBrep()
        if not isinstance(geom, rg.Brep):
            raise ValueError("part %d (%s) is %s, not a Brep" % (i, name, type(geom).__name__))
        if not geom.IsValid:
            raise ValueError("part %d (%s) is not a valid Brep" % (i, name))
        clean.append((str(name), str(material).lower(), geom))
    if not clean:
        raise ValueError("build() returned no parts")
    return clean


def check(parts):
    box = rg.BoundingBox.Empty
    for _, _, b in parts:
        box.Union(b.GetBoundingBox(True))
    dims = box.Max - box.Min
    biggest = max(dims.X, dims.Y, dims.Z)
    if biggest > 8 or biggest < 0.03:
        return "the object is %.2f x %.2f x %.2f m overall, which is not a plausible real size; use metres" % (dims.X, dims.Y, dims.Z)
    return None


def generate_design(spec, image_part, model, variant):
    """Ask the model for build code, run it, send failures back. -> (parts, code, cost, attempts, model)."""
    content = [{"type": "text", "text": spec + ("" if not variant else "\nGive design variation %d." % variant)}]
    if image_part:
        content.append(image_part)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
    cost, problem = 0.0, None
    for attempt in range(1, ATTEMPTS + 1):
        reply, c = alien_ai.chat(messages, model, max_tokens=8000)
        cost += c
        code = extract_code(reply)
        if code is None:
            problem = "Your reply had no ```python block defining build(rg, math)."
        else:
            try:
                parts = run_build(code)
                problem = check(parts)
                if problem is None:
                    return parts, code, cost, attempt
            except Exception as ex:
                tb = traceback.format_exc().strip().splitlines()
                where = next((l.strip() for l in reversed(tb) if 'File "build.py"' in l), "")
                problem = "%s: %s %s" % (type(ex).__name__, ex, ("(" + where + ")") if where else "")
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "That failed: %s\nFix it and reply with the complete corrected code." % problem}]
    raise RuntimeError("No working model after %d attempts. Last problem: %s" % (ATTEMPTS, problem))


# ── run ──

def nice_range(n):
    """Slider range for a built size n (m): half to twice it, on round numbers."""
    step = 0.05 if n >= 0.4 else 0.01
    return max(step, math.floor(0.5 * n / step) * step), math.ceil(2.0 * n / step) * step


thing = (thing or "").strip() or DEFAULT_THING
detail = detail or "detailed"
model = alien_ai.setting("model3d") or DEFAULT_MODEL
img_part, img_hash, img_file = None, "", {}
if image and image.strip():
    data, mime = alien_ai.load_image(image)
    img_part, img_hash = alien_ai.image_part(data, mime), hashlib.sha1(data).hexdigest()[:12]
    img_file = {"reference." + mime.split("/")[-1].replace("jpeg", "jpg"): data}  # kept with the asset
# What the model is asked for. Sizes are not part of it: they resize the result, so sliders never call the model.
spec = "Object: %s.%s Detail: %s.%s" % (
    thing, (" Style: %s." % style.strip()) if style and style.strip() else "", detail,
    " Model it on the reference image." if img_part else "")
design = alien_ai.key_of(model, spec, img_hash, int(variant or 0))

cache = os.path.join(alien_ai.cache_dir("objects"), design + ".json")
source, cost, attempts = None, 0.0, 0
if os.path.exists(cache):
    with open(cache) as f:
        saved = json.load(f)
    code, attempts, source = saved["code"], saved["attempts"], "cache"
elif generate:
    _, code, cost, attempts = generate_design(spec, img_part, model, int(variant or 0))
    source = "model"
    with open(cache, "w") as f:
        json.dump({"code": code, "attempts": attempts, "cost": cost, "spec": spec, "model": model}, f)
elif thing == DEFAULT_THING and not img_part and not (style or "").strip() and not variant and detail == "detailed":
    code, source = SAMPLE_CODE, "sample"

parts, names, materials, colours, size, code_out = [], [], [], [], "", ""
NODE_CONTROLS = {}
if source is None:
    info = ("Press Generate to build '%s' with %s (about US$0.01-0.03; nothing is sent until you do). "
            "Designs built before come back free." % (thing, model))
    print("Waiting for Generate")
else:
    built = run_build(code)

    # Normalise: standing on z = 0, centred in plan. This is the design as built (and as saved to the library).
    box = rg.BoundingBox.Empty
    for _, _, b in built:
        box.Union(b.GetBoundingBox(True))
    move = rg.Transform.Translation(-(box.Min.X + box.Max.X) / 2.0, -(box.Min.Y + box.Max.Y) / 2.0, -box.Min.Z)
    natural = [box.Max.X - box.Min.X, box.Max.Y - box.Min.Y, box.Max.Z - box.Min.Z]
    local = []
    for name, material, b in built:
        b = b.DuplicateBrep()
        b.Transform(move)
        local.append((name, material, b))

    # Resize: each given axis on its own (a wider sofa stays sofa-height); 0 keeps the built size.
    factors = [t / n if t and t > 0 and n > 1e-6 else 1.0 for t, n in zip((width, depth, height), natural)]
    resize = rg.Transform.Scale(rg.Plane.WorldXY, factors[0], factors[1], factors[2])
    doc = Rhino.RhinoDoc.ActiveDoc
    units = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
    target = plane if plane is not None else rg.Plane.WorldXY
    place = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, target) * rg.Transform.Scale(rg.Point3d.Origin, units) * resize

    for name, material, b in local:
        placed = b.DuplicateBrep()
        placed.Transform(place)
        parts.append(placed)
        names.append(name)
        materials.append(material)
        rgb = MATERIAL_COLOURS.get(material) or MATERIAL_COLOURS.get(material.split()[-1] if material else "", None)
        if rgb is None:  # unknown material: a stable muted colour from its name
            h = int(hashlib.sha1(material.encode()).hexdigest()[:6], 16)
            rgb = (90 + h % 120, 90 + (h >> 8) % 120, 90 + (h >> 16) % 120)
        colours.append(System.Drawing.Color.FromArgb(*rgb))
    final = [n * k for n, k in zip(natural, factors)]
    size = "%.2f x %.2f x %.2f m" % tuple(final)
    built_size = "%.2f x %.2f x %.2f m" % tuple(natural)
    code_out = code

    # Sliders fitted to this design; values set for another design reset (the key changes).
    for axis, n in zip(("width", "depth", "height"), natural):
        lo, hi = nice_range(n)
        NODE_CONTROLS[axis] = {"min": round(lo, 3), "max": round(hi, 3), "value": round(n, 2 if n >= 0.2 else 3),
                               "decimals": 2 if n >= 0.2 else 3, "key": design}

    # The library keeps each design once, as built: resizing never makes a new asset.
    asset_params = {"variant": int(variant or 0), "model": model, "image": img_hash}
    if source == "model" or not alien_assets.exists("object", spec, asset_params):
        try:
            geo = [b for _, _, b in local]
            alien_assets.save("object", thing, spec, asset_params, "object_3d",
                              files=dict(img_file, **{"build.py": code.encode()}), geometry=geo, names=names, colours=colours,
                              thumbnail=alien_assets.thumbnail_from_geometry(geo, colours=colours),
                              extra={"size": built_size, "materials": sorted(set(materials)),
                                     "inputs": {"thing": thing, "style": style or "", "image": image or "", "detail": detail,
                                                "variant": int(variant or 0)}})
        except Exception as ex:
            print("could not save to the asset library: %s" % ex)

    info = "\n".join(l for l in [
        "%s: %d parts, %s%s" % (thing, len(parts), size, "" if factors == [1.0, 1.0, 1.0] else " (resized from %s as built)" % built_size),
        "Built by %s, %s" % (model, {"cache": "from cache (free)", "sample": "built-in sample (free)"}.get(
            source, "%d attempt%s, US$%.3f" % (attempts, "" if attempts == 1 else "s", cost))),
        "Materials: " + ", ".join(sorted(set(materials))),
    ] if l)
code = code_out
