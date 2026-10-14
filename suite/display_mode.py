# NODE_INPUTS: description:str | "the look you want" default="white clay model with soft shadows and thin black edges", image:str | "reference image (optional): a render or drawing whose look to copy" default="" image
# NODE_INPUTS: base:str | "built-in mode to start from (auto = the model's pick)" default="auto" options="auto|Shaded|Rendered|Arctic|Technical|Pen|Ghosted"
# NODE_INPUTS: name:str | "mode name: Rhino lists it as 'Alien · <name>' (empty = the model's name)" default=""
# NODE_INPUTS: variant:int | "another take on the same request" min=0 max=50 default=0
# NODE_INPUTS: generate:bool | "ask the model for the look (paid, about US$0.01); a look made before comes back free" button
# NODE_INPUTS: install:bool | "create or update the Rhino display mode 'Alien · <name>' from the look" button, apply:bool | "show the mode in the active viewport (installs it first)" button
# NODE_OUTPUTS: mode:str | "the display mode's name in Rhino", spec:str | "the look as JSON, checked and clamped", info:str | "what the look sets, model, cost, install state"
# CATEGORY: Create
# Display mode: describe a look ("white clay model with soft shadows", "blueprint") or give a reference image, and a model designs a Rhino display mode for it.
#
# Generate asks the model for the look as a small JSON spec (background, shading colour, lighting, shadows, edges,
# silhouettes, curves, grid), which is checked, clamped and cached by request; base and name are applied at install,
# so changing them never calls the model. Install (a button) copies the base mode as "Alien · <name>" (or resets the one
# made before back to the base) and sets the look on it: typed RhinoCommon display attributes, plus an .ini edit for
# what RhinoCommon doesn't expose (the shading colour and Technical/Pen line colours). Built-in modes are never changed.
# Apply (a button) shows it in the active viewport. Looks are saved to the asset library (spec, .ini, a capture).
import json
import os
import re

import Rhino
import Rhino.Geometry as rg
import System
import System.Drawing
from Rhino.Display import DisplayModeDescription as DMD

import alien_ai
import alien_assets
import alien_codegen as cg

DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_DESCRIPTION = "white clay model with soft shadows and thin black edges"
CACHE = "display_modes"
PREFIX = "Alien · "
BASES = ("Shaded", "Rendered", "Arctic", "Technical", "Pen", "Ghosted")
TECHNICAL = ("Technical", "Pen")  # the hidden-line pipeline: silhouettes and creases, no shading by default

# The default request's look (from claude-sonnet-5.5), so a new node shows a spec without a model call.
SAMPLE_SPEC = {'name': 'White Clay',
               'base': 'Arctic',
               'background': {'style': 'solid', 'colour': [255, 255, 255]},
               'shading': {'on': True, 'colour': [245, 244, 240], 'transparency': 0.0, 'shine': 0.0, 'flat': False},
               'lighting': 'ambient occlusion',
               'shadows': {'on': True, 'colour': [0, 0, 0], 'intensity': 0.45, 'softness': 0.8, 'ground': True},
               'edges': {'on': True, 'colour': [0, 0, 0], 'thickness': 1},
               'curves': {'colour': [0, 0, 0], 'thickness': 1},
               'isocurves': False,
               'grid': False,
               'xray': False}

SYSTEM = """You design Rhino 8 viewport display modes. Given a description of a look (and maybe a reference image), reply with one ```json block in exactly this shape (every key except name is optional; leave out what the look doesn't care about):

{
  "name": "Clay",
  "base": "Arctic",
  "background": {"style": "solid", "colour": [255, 255, 255]},
  "shading": {"on": true, "colour": [235, 235, 230], "transparency": 0.0, "flat": false, "shine": 0.0},
  "lighting": "ambient occlusion",
  "shadows": {"on": true, "colour": [0, 0, 0], "intensity": 0.5, "softness": 0.6, "ground": true},
  "edges": {"on": true, "colour": [0, 0, 0], "thickness": 1},
  "silhouettes": {"on": false, "colour": [0, 0, 0], "thickness": 2},
  "curves": {"colour": [0, 0, 0], "thickness": 1},
  "isocurves": false,
  "grid": false,
  "xray": false
}

Meaning:
- name: 1-3 words for the mode (Rhino lists it as "Alien · <name>").
- base: the built-in mode it starts from: "Shaded" (plain shaded), "Rendered" (render materials and lights), "Arctic" (white with ambient occlusion), "Ghosted" (see-through), "Technical" or "Pen" (hidden-line drawings with silhouettes and creases, shading off). Prefer Shaded, Rendered, Arctic or Ghosted; use Technical or Pen only for a line-drawing look.
- background: style "solid" with "colour", "gradient" with "top" and "bottom" colours, or "viewport" (Rhino's default).
- shading: on/off; colour overrides every object's colour (null keeps the objects' own colours); transparency 0-1; flat facets; shine 0-1.
- lighting: "default", "scene" (the document's lights), "ambient occlusion" (soft contact shading) or "none" (flat colour).
- shadows: cast shadows on/off; colour; intensity 0-1; softness 0 (crisp) to 1 (very soft); ground true = shadows fall on an invisible ground plane under the model.
- edges: surface edges drawn as lines; colour (null = object colour); thickness in pixels 1-10.
- silhouettes: outlines of curved forms and creases (works only with base Technical or Pen); colour; thickness 1-10.
- curves: colour of curves (null = object colour); thickness in pixels 1-10.
- isocurves: surface isocurves on/off. grid: construction grid and axes on/off. xray: see objects through each other.
Colours are [r, g, b], 0-255. Reply with the JSON only."""


# ── the look: checked and clamped ──

def _colour(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", s):
            return [int(s[i:i + 2], 16) for i in (0, 2, 4)]
        return {"white": [255, 255, 255], "black": [0, 0, 0]}.get(s.lower())
    try:
        return [max(0, min(255, int(round(float(x))))) for x in list(v)[:3]] if len(list(v)) >= 3 else None
    except (TypeError, ValueError):
        return None


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _flag(v, default=None):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "on", "1")
    return default


def normalise(raw):
    """The model's JSON -> a clean spec with only known keys, valid values and ranges (raises if it isn't a look)."""
    if not isinstance(raw, dict):
        raise ValueError("the reply had no JSON object")
    out = {"name": re.sub(r"[^\w\s\-&'()]", "", str(raw.get("name") or "")).strip()[:40] or "Look"}
    b = str(raw.get("base") or "").strip().title()
    out["base"] = b if b in BASES else "Shaded"
    bg = raw.get("background")
    if isinstance(bg, dict):
        style = str(bg.get("style") or "solid").lower()
        if style == "gradient" and _colour(bg.get("top")) and _colour(bg.get("bottom")):
            out["background"] = {"style": "gradient", "top": _colour(bg["top"]), "bottom": _colour(bg["bottom"])}
        elif style in ("viewport", "default"):
            out["background"] = {"style": "viewport"}
        elif _colour(bg.get("colour", bg.get("color"))):
            out["background"] = {"style": "solid", "colour": _colour(bg.get("colour", bg.get("color")))}
    sh = raw.get("shading")
    if isinstance(sh, dict):
        s = {"on": _flag(sh.get("on"), True)}
        if "colour" in sh or "color" in sh:
            s["colour"] = _colour(sh.get("colour", sh.get("color")))
        for k, lo, hi in (("transparency", 0.0, 0.95), ("shine", 0.0, 1.0)):
            if k in sh:
                s[k] = _num(sh[k], lo, hi, 0.0)
        if "flat" in sh:
            s["flat"] = _flag(sh["flat"], False)
        out["shading"] = s
    lt = str(raw.get("lighting") or "").strip().lower()
    if lt:
        out["lighting"] = {"ao": "ambient occlusion", "ambient": "ambient occlusion", "occlusion": "ambient occlusion",
                           "flat": "none", "off": "none"}.get(lt, lt)
        if out["lighting"] not in ("default", "scene", "ambient occlusion", "none"):
            out["lighting"] = "default"
    sd = raw.get("shadows")
    if isinstance(sd, dict):
        out["shadows"] = {"on": _flag(sd.get("on"), True),
                          "colour": _colour(sd.get("colour", sd.get("color"))) or [0, 0, 0],
                          "intensity": _num(sd.get("intensity"), 0.0, 1.0, 0.6),
                          "softness": _num(sd.get("softness"), 0.0, 1.0, 0.3),
                          "ground": _flag(sd.get("ground"), True)}
    elif isinstance(sd, bool):
        out["shadows"] = {"on": sd, "colour": [0, 0, 0], "intensity": 0.6, "softness": 0.3, "ground": True}
    for key in ("edges", "silhouettes"):
        e = raw.get(key)
        if isinstance(e, bool):
            e = {"on": e}
        if isinstance(e, dict):
            out[key] = {"on": _flag(e.get("on"), True), "colour": _colour(e.get("colour", e.get("color"))),
                        "thickness": int(_num(e.get("thickness"), 1, 10, 1))}
    cv = raw.get("curves")
    if isinstance(cv, dict):
        out["curves"] = {"colour": _colour(cv.get("colour", cv.get("color"))),
                         "thickness": int(_num(cv.get("thickness"), 1, 10, 1))}
    for key in ("isocurves", "grid", "xray"):
        if key in raw:
            out[key] = _flag(raw[key], False)
    return out


def judge(reply):
    spec = normalise(cg.extract_json(reply))
    if len(spec) < 3:
        raise ValueError("the JSON set almost nothing; describe the look with background, shading, lighting, edges and so on")
    return spec


def summary(spec):
    def c(v):
        return "object colours" if v is None else "%d,%d,%d" % tuple(v)
    bits = ["%s base" % spec["base"]]
    bg = spec.get("background")
    if bg:
        if bg["style"] == "solid":
            bits.append("background %s" % c(bg["colour"]))
        elif bg["style"] == "gradient":
            bits.append("gradient %s to %s" % (c(bg["top"]), c(bg["bottom"])))
        else:
            bits.append("viewport background")
    sh = spec.get("shading")
    if sh:
        bits.append(("shading %s" % c(sh.get("colour"))) if sh["on"] else "no shading")
        if sh.get("transparency"):
            bits.append("%d%% transparent" % round(100 * sh["transparency"]))
    if spec.get("lighting"):
        bits.append("no lighting" if spec["lighting"] == "none" else spec["lighting"] + " lighting")
    sd = spec.get("shadows")
    if sd:
        bits.append("%d%% shadows, softness %.1f%s" % (round(100 * sd["intensity"]), sd["softness"],
                                                        " on the ground" if sd["ground"] else "") if sd["on"] else "no shadows")
    for key in ("edges", "silhouettes"):
        e = spec.get(key)
        if e:
            bits.append(("%s %s %d px" % (key, c(e["colour"]), e["thickness"])) if e["on"] else "no " + key)
    if spec.get("curves"):
        bits.append("curves %s %d px" % (c(spec["curves"]["colour"]), spec["curves"]["thickness"]))
    for key in ("isocurves", "grid", "xray"):
        if key in spec:
            bits.append(("" if spec[key] else "no ") + key)
    return ", ".join(bits)


# ── installing it in Rhino ──

def C(v):
    return System.Drawing.Color.FromArgb(*v)


def find_mode(full):
    return next((m for m in DMD.GetDisplayModes() if m.EnglishName == full), None)


def read_ini(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def write_ini(path, text):
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)


def patch_ini(text, changes):
    """Set keys inside sections of an exported mode. changes: {section after the mode id ("" = the main section,
    "\\Objects\\Technical", ...): {key: value}}. Keys missing from the export are left alone."""
    out, section = [], None
    for line in text.splitlines():
        m = re.match(r"^\[DisplayMode\\[0-9a-fA-F-]{36}(.*)\]\s*$", line.strip())
        if m:
            section = m.group(1)
        elif section in changes and "=" in line:
            key = line.split("=", 1)[0]
            if key in changes[section]:
                line = "%s=%s" % (key, changes[section][key])
        out.append(line)
    return "\n".join(out) + "\n"


def install_mode(spec, base_name, full, folder):
    """Create "Alien · <name>" from the base (or reset ours to the base) and set the look on it. -> (mode, notes)."""
    base = DMD.FindByName(base_name) or next((m for m in DMD.GetDisplayModes() if m.EnglishName == base_name), None)
    if base is None:
        raise RuntimeError("Rhino has no '%s' display mode to start from" % base_name)
    notes = []
    mode = find_mode(full)
    if mode is None:
        mode_id = DMD.CopyDisplayMode(base.Id, full)
        if mode_id == System.Guid.Empty:
            raise RuntimeError("Rhino would not copy '%s'" % base_name)
        notes.append("created from %s" % base_name)
    else:
        # Reset ours to a fresh copy of the base through its .ini (same id, so viewports showing it keep it).
        mode_id = mode.Id
        mine = os.path.join(folder, "current.ini")
        DMD.ExportToFile(mode, mine)
        order = next((l.split("=", 1)[1] for l in read_ini(mine).splitlines() if l.startswith("Order=")), None)
        path = os.path.join(folder, "base.ini")
        DMD.ExportToFile(base, path)
        text = read_ini(path).replace(str(base.Id), str(mode_id))
        text = patch_ini(text, {"": dict({"Name": full}, **({"Order": order} if order else {}))})
        write_ini(path, text)
        DMD.ImportFromFile(path)
        notes.append("updated (reset to %s first)" % base_name)
    mode = DMD.GetDisplayMode(mode_id)
    a = mode.DisplayAttributes
    ini = {}

    bg = spec.get("background")
    if bg and bg["style"] == "solid":
        a.SetFill(C(bg["colour"]))
    elif bg and bg["style"] == "gradient":
        a.SetFill(C(bg["top"]), C(bg["bottom"]))
    elif bg and bg["style"] == "viewport":
        a.FillMode = Rhino.Display.DisplayPipelineAttributes.FrameBufferFillMode.DefaultColor

    sh = spec.get("shading")
    if sh:
        a.ShadingEnabled = bool(sh["on"])
        if "colour" in sh:
            # RhinoCommon has no setter for the mode's material colour: it goes in through the .ini below.
            ini.setdefault("\\Shading\\Material", {})["FrontIsCustom"] = "y" if sh["colour"] else "n"
            front = ini.setdefault("\\Shading\\Material\\Front Material", {})
            front["OverrideObjectColor"] = "y" if sh["colour"] else "n"
            if sh["colour"]:
                front["Diffuse"] = "%d,%d,%d" % tuple(sh["colour"])
        if "transparency" in sh:
            a.FrontMaterialTransparency = float(sh["transparency"])
            a.FrontOverrideObjectTransparency = sh["transparency"] > 0
        if "flat" in sh:
            a.FrontFlatShaded = bool(sh["flat"])
        if "shine" in sh:
            a.FrontMaterialShine = float(sh["shine"]) * 255.0  # stored on Rhino's 0-255 material scale

    lighting = spec.get("lighting")
    if lighting:
        import clr  # LightingSchema.None can't be named in Python: parse it
        L = clr.GetClrType(Rhino.Display.DisplayPipelineAttributes.LightingSchema)
        a.LightingScheme = System.Enum.Parse(L, {"default": "DefaultLighting", "scene": "SceneLighting",
                                                 "ambient occlusion": "AmbientOcclusion", "none": "None"}[lighting])

    sd = spec.get("shadows")
    if sd:
        a.ShadowsOn = bool(sd["on"])
        if sd["on"]:
            a.ShadowColor = C(sd["colour"])
            a.ShadowIntensity = int(round(100 * sd["intensity"]))
            a.ShadowEdgeBlur = 3.0 * sd["softness"]
            a.ShadowSoftEdgeQuality = int(round(4 + 8 * sd["softness"]))
            a.GroundPlaneUsage = type(a.GroundPlaneUsage).Custom
            a.CustomGroundPlaneOn = bool(sd["ground"])
            a.CustomGroundPlaneShadowOnly = True
            a.CustomGroundPlaneAutomaticAltitude = True

    e = spec.get("edges")
    if e:
        a.ShowSurfaceEdges = bool(e["on"])
        if e["on"]:
            use = type(a.SurfaceEdgeColorUsage)
            a.SurfaceEdgeColorUsage = use.ObjectColor if e["colour"] is None else use.SingleColorForAll
            if e["colour"] is not None:
                a.SurfaceEdgeColor = C(e["colour"])
            a.SurfaceEdgeThickness = int(e["thickness"])

    si = spec.get("silhouettes")
    if si:
        tech = ini.setdefault("\\Objects\\Technical", {})
        if base_name in TECHNICAL:
            if not si["on"]:
                tech["TechnicalMask"] = "0"
            else:
                for k in ("TSi", "TE", "TC", "TI", "TS"):
                    if si["colour"] is not None:
                        tech[k + "Color"] = "%d,%d,%d" % tuple(si["colour"])
                    tech[k + "Thickness"] = str(int(si["thickness"]))
        elif si["on"]:
            notes.append("silhouettes need base Technical or Pen (this look uses %s)" % base_name)

    cv = spec.get("curves")
    if cv:
        a.UseSingleCurveColor = cv["colour"] is not None
        if cv["colour"] is not None:
            a.CurveColor = C(cv["colour"])
        a.CurveThickness = int(cv["thickness"])

    if "isocurves" in spec:
        a.ShowIsoCurves = bool(spec["isocurves"])
    if "grid" in spec:
        v = a.ViewSpecificAttributes
        v.DrawGrid = v.DrawGridAxes = v.DrawWorldAxes = bool(spec["grid"])
    if "xray" in spec:
        a.XrayAllObjects = bool(spec["xray"])

    if not DMD.UpdateDisplayMode(mode):
        raise RuntimeError("Rhino did not accept the display attributes")
    if ini:
        path = os.path.join(folder, "patch.ini")
        DMD.ExportToFile(DMD.GetDisplayMode(mode_id), path)
        write_ini(path, patch_ini(read_ini(path), ini))
        DMD.ImportFromFile(path)
    return DMD.GetDisplayMode(mode_id), notes


def capture(mode, path):
    """The document's visible objects drawn in the mode, from the Perspective view's direction (PNG bytes, or None
    when the document has nothing to show). The camera is zoomed for the shot and put back straight after; the
    viewport's own display mode is never changed (CaptureToBitmap draws with the given mode)."""
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc is None:
        return None
    box = rg.BoundingBox.Empty
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.VisibleFilter, settings.HiddenObjects = True, False
    for o in doc.Objects.GetObjectList(settings):
        if o.Geometry is not None:
            box.Union(o.Geometry.GetBoundingBox(True))
    view = next((v for v in doc.Views.GetViewList(True, False) if v.ActiveViewport.Name == "Perspective"), None)
    if view is None or not box.IsValid:
        return None
    vp = view.ActiveViewport
    saved, target = Rhino.DocObjects.ViewportInfo(vp), vp.CameraTarget
    try:
        vp.ZoomBoundingBox(box)
        bmp = view.CaptureToBitmap(System.Drawing.Size(320, 240), mode)
    finally:
        vp.SetViewProjection(saved, True)
        vp.SetCameraTarget(target, False)
        view.Redraw()
    if bmp is None:
        return None
    bmp.Save(path)
    return cg.read_bytes(path)


def swatch(spec):
    """A drawn stand-in thumbnail: a box, a cylinder and a sphere in the look's colours (no viewport needed)."""
    bg = spec.get("background") or {}
    back = bg.get("colour") or bg.get("bottom") or [160, 160, 165]
    sh = spec.get("shading") or {}
    col = C(sh.get("colour") or [200, 200, 200])
    geo = [rg.Box(rg.Plane.WorldXY, rg.Interval(-1, 1), rg.Interval(-1, 1), rg.Interval(0, 1.6)).ToBrep(),
           rg.Cylinder(rg.Circle(rg.Plane(rg.Point3d(2.2, 0.6, 0), rg.Vector3d.ZAxis), 0.6), 2.4).ToBrep(True, True),
           rg.Sphere(rg.Point3d(0.4, -2.0, 0.7), 0.7).ToBrep()]
    return alien_assets.thumbnail_from_geometry(geo, colours=[col] * 3, background=tuple(back))


# ── run ──

description = (description or "").strip() or DEFAULT_DESCRIPTION
model = alien_ai.setting("modelDisplay") or DEFAULT_MODEL
img_part, img_hash, img_file = cg.reference(image)
request = "Look: %s.%s" % (description, " Copy the look of the reference image." if img_part else "")
key = alien_ai.key_of(model, request, img_hash, int(variant or 0))

saved = cg.cached(CACHE, key)
source, cost, attempts, look = None, 0.0, 0, None
if saved:
    look, attempts, source = saved["spec"], saved["attempts"], "cache"
elif generate:
    look, _, cost, attempts = cg.ask(SYSTEM, request, img_part, model, int(variant or 0), judge, max_tokens=2000)
    source = "model"
    cg.store(CACHE, key, {"spec": look, "attempts": attempts, "cost": cost, "request": request, "model": model})
elif SAMPLE_SPEC and description == DEFAULT_DESCRIPTION and not img_part and not variant:
    look, source = SAMPLE_SPEC, "sample"

mode, spec = "", ""
if look is None:
    info = ("Press Generate to design '%s' with %s (about US$0.01; nothing is sent until you do). "
            "Looks made before come back free.%s" % (
                description, model, " Install and apply need a look first." if install or apply else ""))
    print("Waiting for Generate")
else:
    look = normalise(look)
    look["base"] = base if base in BASES else look["base"]
    label = re.sub(r"[^\w\s\-&'()]", "", (name or "").strip())[:40] or look["name"]
    mode = PREFIX + label
    spec = json.dumps(look, indent=1)
    folder = alien_ai.cache_dir(CACHE)
    lines = ["%s: %s" % (mode, summary(look))]

    params = {"variant": int(variant or 0), "model": model, "image": img_hash}
    inputs = {"description": description, "image": image or "", "base": base or "auto", "name": name or "",
              "variant": int(variant or 0)}
    asset_files = dict(img_file, **{"spec.json": spec.encode()})
    thumb = None
    installed = find_mode(mode)
    if install or apply:
        desc, notes = install_mode(look, look["base"], mode, folder)
        installed = desc
        ini_path = os.path.join(folder, key + ".ini")
        DMD.ExportToFile(desc, ini_path)
        asset_files["mode.ini"] = cg.read_bytes(ini_path)
        try:
            shot = capture(desc, os.path.join(folder, key + ".png"))
            thumb = alien_assets.thumbnail_from_image(shot) if shot else None
        except Exception as ex:
            print("no capture for the thumbnail: %s" % ex)
        lines.append("Installed in Rhino: %s" % "; ".join(notes))
        if apply:
            doc = Rhino.RhinoDoc.ActiveDoc
            view = doc.Views.ActiveView if doc else None
            if view is not None:
                view.ActiveViewport.DisplayMode = desc
                view.Redraw()
                lines.append("Showing in the %s viewport" % view.ActiveViewport.Name)
    elif installed is not None:
        lines.append("Installed in Rhino (press install to update it from this look)")
    else:
        lines.append("Not in Rhino yet: press install (or apply)")

    if install or apply or source == "model" or not alien_assets.exists("display_mode", request, params):
        try:
            alien_assets.save("display_mode", label, request, params, "display_mode", files=asset_files,
                              thumbnail=thumb or swatch(look), extra={"size": summary(look), "mode": mode, "inputs": inputs})
        except Exception as ex:
            print("could not save to the asset library: %s" % ex)

    lines.append("Designed by %s, %s" % (model, {"cache": "from cache (free)", "sample": "built-in sample (free)"}.get(
        source, "%d attempt%s, US$%.3f" % (attempts, "" if attempts == 1 else "s", cost))))
    info = "\n".join(lines)
