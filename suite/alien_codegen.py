"""
Alien codegen helpers for the Create nodes (object_2d, symbol, display_mode): ask a model for code or JSON, run
model-written code with only rg and math, send failures back for another attempt, cache results by request, and the
2D drawing helpers both drawing nodes share (elements -> curves, fills and hatches; text slots; .3dm in any unit).
Keep this file next to the nodes that import it. object_3d keeps its own copy of the build loop.

Model-written code runs with only `rg` (Rhino.Geometry), `math` and a short list of builtins: no imports, files or
network. That is not a sandbox (Python introspection can escape one); the code comes from the user's own model call
and is cached and shown on the node's `code` output.
"""
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

_B = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
SAFE_BUILTINS = {n: _B[n] for n in (
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len", "list", "map",
    "max", "min", "pow", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "divmod",
    "Exception", "ValueError", "TypeError", "ZeroDivisionError", "IndexError", "KeyError", "AttributeError",
    "None", "True", "False",
) if n in _B}


# ── asking the model ─────────────────────────────────────────

def extract_code(reply, fn="build"):
    """The ```python block that defines `fn` (or the reply from `def fn` on)."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", reply or "", re.S)
    code = next((b for b in blocks if "def %s" % fn in b), None)
    if code is None and reply and "def %s" % fn in reply:
        code = reply[reply.index("def %s" % fn):]
        code = code.split("```")[0]
    return code


def extract_json(reply):
    """The first JSON object in a reply (a ```json block, else the outermost braces)."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", reply or "", re.S)
    for b in blocks + [reply or ""]:
        s, e = b.find("{"), b.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(b[s:e + 1])
            except ValueError:
                continue
    return None


def run_code(code, fn, names=None):
    """Execute model code with only rg, math (and `names`) -> (the function `fn`, the module scope)."""
    scope = {"__builtins__": SAFE_BUILTINS, "rg": rg, "math": math}
    scope.update(names or {})
    exec(compile(code, "build.py", "exec"), scope)
    if not callable(scope.get(fn)):
        raise ValueError("the code does not define %s(...)" % fn)
    return scope[fn], scope


def problem_of(ex):
    """An exception as one line for the model, with the line of build.py it came from."""
    tb = traceback.format_exc().strip().splitlines()
    where = next((l.strip() for l in reversed(tb) if 'File "build.py"' in l), "")
    return "%s: %s %s" % (type(ex).__name__, ex, ("(" + where + ")") if where else "")


def ask(system, text, image_part, model, variant, attempt, attempts=3, max_tokens=8000, reasoning="low"):
    """
    Ask `model` and judge each reply with attempt(reply) -> result (raise ValueError / any error = send the problem
    back and ask again). -> (result, reply, US$ cost, attempts used). The variant asks for a different take.
    """
    content = [{"type": "text", "text": text + ("" if not variant else "\nGive design variation %d." % variant)}]
    if image_part:
        content.append(image_part)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    cost, problem = 0.0, None
    for n in range(1, attempts + 1):
        reply, c = alien_ai.chat(messages, model, max_tokens=max_tokens, reasoning=reasoning)
        cost += c
        try:
            return attempt(reply), reply, cost, n
        except Exception as ex:
            problem = problem_of(ex)
        fix = "That failed: %s\nFix it and reply with the complete corrected answer." % problem
        messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": fix}]
    raise RuntimeError("No working answer after %d attempts (US$%.3f spent). Last problem: %s" % (attempts, cost, problem))


# ── cache (by request: never by sizes, placement or text values) ──

def cache_path(kind, key):
    return os.path.join(alien_ai.cache_dir(kind), key + ".json")


def cached(kind, key):
    path = cache_path(kind, key)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def store(kind, key, data):
    with open(cache_path(kind, key), "w") as f:
        json.dump(data, f)


def reference(image):
    """A reference image input -> (image part for the model, short hash for keys, {filename: bytes} for the asset)."""
    if not image or not str(image).strip():
        return None, "", {}
    try:
        data, mime = alien_ai.load_image(image)
    except Exception as ex:
        raise ValueError("Can't read the reference image '%s' (%s): drop an image on the node, or give a file path or URL"
                         % (str(image).strip()[:120], ex))
    ext = mime.split("/")[-1].replace("jpeg", "jpg")
    return alien_ai.image_part(data, mime), hashlib.sha1(data).hexdigest()[:12], {"reference." + ext: data}


# ── 2D drawings: what build() returns ─────────────────────────

# Friendly hatch names -> Rhino's default patterns (found by name in the document; Solid when missing).
PATTERNS = {
    "solid": "Solid", "lines": "Hatch1", "hatch": "Hatch1", "diagonal": "Hatch1", "double": "Hatch2", "triple": "Hatch3",
    "dash": "HatchDash", "dashed": "HatchDash", "grid": "Grid", "cross": "Grid", "crosshatch": "Grid", "grid60": "Grid60",
    "triangles": "Grid60", "plus": "Plus", "squares": "Squares",
}


def _curves(g):
    """Anything curve-like the model may return -> [Curve]."""
    if g is None:
        return []
    if isinstance(g, (list, tuple)):
        out = []
        for x in g:
            out += _curves(x)
        return out
    if isinstance(g, rg.Curve):
        return [g.DuplicateCurve()]
    if isinstance(g, rg.Line):
        return [rg.LineCurve(g)]
    if isinstance(g, rg.Polyline):
        return [rg.PolylineCurve(g)]
    if isinstance(g, rg.Arc):
        return [rg.ArcCurve(g)]
    if isinstance(g, rg.Circle):
        return [rg.ArcCurve(g)]
    if isinstance(g, (rg.Rectangle3d, rg.Ellipse)):
        return [g.ToNurbsCurve()]
    if isinstance(g, rg.Brep):
        return list(rg.Curve.JoinCurves([e.DuplicateCurve() for e in g.Edges]) or [])
    raise ValueError("%s is not a curve (use rg curves, lines, polylines, circles, arcs or rectangles)" % type(g).__name__)


def _rgb(v, default):
    """A style colour: [r, g, b], a 0-1 tone (0 black, 1 white), or a name we know."""
    names = {"black": (0, 0, 0), "white": (255, 255, 255), "grey": (160, 160, 160), "gray": (160, 160, 160),
             "red": (200, 40, 40), "green": (90, 150, 80), "blue": (60, 100, 190)}
    if v is None:
        return default
    if isinstance(v, str):
        return names.get(v.strip().lower(), default)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        t = max(0.0, min(1.0, float(v)))
        return (int(255 * t),) * 3
    try:
        r, g, b = [max(0, min(255, int(round(float(x))))) for x in list(v)[:3]]
        return (r, g, b)
    except Exception:
        return default


def elements(items, closed_tol):
    """
    build()'s list -> [(name, kind, [Curve], style)] in the XY plane. Kinds: line, fill, hatch. Open curves in a fill
    or hatch close themselves when their ends are within closed_tol; otherwise that's an error the model must fix.
    """
    if not isinstance(items, (list, tuple)):
        raise ValueError("build() must return a list of (name, kind, geometry) tuples, not %s" % type(items).__name__)
    flatten = rg.Transform.PlanarProjection(rg.Plane.WorldXY)
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            raise ValueError("item %d is not a (name, kind, geometry[, style]) tuple" % i)
        name, kind, geom = str(item[0]), str(item[1]).strip().lower(), item[2]
        style = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
        if kind in ("lines", "curve", "curves", "outline"):
            kind = "line"
        if kind in ("solid", "fills", "filled", "region"):
            kind = "fill"
        if kind not in ("line", "fill", "hatch"):
            raise ValueError("item %d (%s) has kind %r; use 'line', 'fill' or 'hatch'" % (i, name, kind))
        curves = []
        for c in _curves(geom):
            if c is None or not c.IsValid:
                raise ValueError("item %d (%s) has an invalid curve" % (i, name))
            c.Transform(flatten)
            if kind != "line" and not c.IsClosed:
                if c.PointAtStart.DistanceTo(c.PointAtEnd) <= closed_tol and c.MakeClosed(closed_tol):
                    pass
                else:
                    raise ValueError("item %d (%s) is a %s but one of its curves is open: fills and hatches need "
                                     "closed curves" % (i, name, kind))
            curves.append(c)
        if curves:
            out.append((name, kind, curves, style))
    if not out:
        raise ValueError("build() returned no geometry")
    return out


def bounds(els):
    box = rg.BoundingBox.Empty
    for _, _, cs, _ in els:
        for c in cs:
            box.Union(c.GetBoundingBox(True))
    return box


def transform(els, xf):
    """A copy of the elements with every curve transformed."""
    out = []
    # Arcs inside polycurves drift apart under a non-uniform scale (the curve opens and its fill is lost): NURBS don't.
    stretch = xf.SimilarityType == rg.TransformSimilarityType.NotSimilarity
    for name, kind, cs, style in els:
        moved = []
        for c in cs:
            c = c.ToNurbsCurve() if stretch else c.DuplicateCurve()
            c.Transform(xf)
            moved.append(c)
        out.append((name, kind, moved, style))
    return out


def hatch_pattern(name):
    """-> (index in the document's hatch table, base line spacing of the pattern in its own units)."""
    want = PATTERNS.get(str(name or "solid").strip().lower(), str(name or "Solid"))
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc is None:
        return 0, 1.0
    for i in range(doc.HatchPatterns.Count):
        hp = doc.HatchPatterns[i]
        if hp.IsDeleted or hp.Name.lower() != want.lower():
            continue
        spacing = max([abs(hl.Offset.Y) for hl in hp.HatchLines] or [1.0]) or 1.0
        return hp.Index, spacing
    return 0, 1.0  # Solid is pattern 0 in every document


def drawing(els, unit_scale, tol, fill_default=(205, 205, 205)):
    """
    Placed elements -> curves (every element's curves: linework plus the outlines of fills and hatches), names (one
    per curve), fills (planar Breps of the fill elements), colours (one per fill), hatches (a solid hatch per fill
    element in its tone, a pattern hatch per hatch element), hatch_colours (one per hatch).
    unit_scale = model units per drawing metre, for hatch spacing.
    """
    curves, names, fills, colours, hatches, hatch_colours = [], [], [], [], [], []
    for name, kind, cs, style in els:
        curves += cs
        names += [name] * len(cs)
        if kind == "line":
            continue
        if kind == "fill":
            rgb = _rgb(style.get("colour", style.get("color", style.get("tone"))), fill_default)
            col = System.Drawing.Color.FromArgb(*rgb)
            breps = list(rg.Brep.CreatePlanarBreps(cs, tol) or [])
            fills += breps
            colours += [col] * len(breps)
            hs = list(rg.Hatch.Create(cs, 0, 0.0, 1.0, tol) or [])
            hatches += hs
            hatch_colours += [col] * len(hs)
        else:
            index, base = hatch_pattern(style.get("pattern", "lines"))
            spacing = float(style.get("spacing") or 0.05)  # metres between hatch lines, at drawing size
            angle = math.radians(float(style.get("angle") or 0.0))
            scale = 1.0 if index == 0 else max(1e-6, spacing * unit_scale / base)
            rgb = _rgb(style.get("colour", style.get("color", style.get("tone"))), (0, 0, 0))
            hs = list(rg.Hatch.Create(cs, index, angle, scale, tol) or [])
            hatches += hs
            hatch_colours += [System.Drawing.Color.FromArgb(*rgb)] * len(hs)
    return curves, names, fills, colours, hatches, hatch_colours


def thumbnail(els, size=256, paper=(246, 246, 243), ink=(25, 25, 25), fill_default=(205, 205, 205)):
    """
    A library thumbnail of a drawing, on paper, in drawing order: fills in their tone (holes kept), hatches as their
    pattern lines, every element's linework on top of its own fill. els: elements in any one unit (metres or mm).
    """
    import System
    import Eto.Drawing as ed
    import alien_assets
    box = bounds(els)
    if not box.IsValid:
        return None
    span = max(box.Max.X - box.Min.X, box.Max.Y - box.Min.Y) or 1.0
    k = size * 0.86 / span
    cx, cy = (box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0

    def pts(c):
        pl = c.ToPolyline(0, 0, 0.05, 0, 0, 0, span / 2000.0, 0, True)
        ok, poly = pl.TryGetPolyline() if pl is not None else (False, None)
        if not ok:
            return []
        return [ed.PointF(size / 2.0 + (p.X - cx) * k, size / 2.0 - (p.Y - cy) * k) for p in poly]

    bmp = ed.Bitmap(size, size, ed.PixelFormat.Format32bppRgba)
    g = ed.Graphics(bmp)
    g.AntiAlias = True
    g.Clear(ed.Color.FromArgb(*paper))
    pen = ed.Pen(ed.Color.FromArgb(*ink), max(1.0, size / 220.0))
    thin = ed.Pen(ed.Color.FromArgb(*ink), max(0.6, size / 400.0))
    for name, kind, cs, style in els:
        if kind == "fill":
            path = ed.GraphicsPath()
            path.FillMode = ed.FillMode.Alternate
            for c in cs:
                p = pts(c)
                if len(p) > 2:
                    path.AddLines(System.Array[ed.PointF](p))
                    path.CloseFigure()
            rgb = _rgb(style.get("colour", style.get("color", style.get("tone"))), fill_default)
            g.FillPath(ed.Color.FromArgb(*rgb), path)
        elif kind == "hatch":
            index, base = hatch_pattern(style.get("pattern", "lines"))
            spacing = max(float(style.get("spacing") or 0.05), span / 120.0)  # readable at thumbnail size
            hs = rg.Hatch.Create(cs, index, math.radians(float(style.get("angle") or 0.0)),
                                 1.0 if index == 0 else spacing / base, span / 10000.0) or []
            for h in hs:
                if index == 0:
                    rgb = _rgb(style.get("colour", style.get("color", style.get("tone"))), (0, 0, 0))
                    path = ed.GraphicsPath()
                    path.FillMode = ed.FillMode.Alternate
                    for c in h.Get3dCurves(True) or []:
                        p = pts(c)
                        if len(p) > 2:
                            path.AddLines(System.Array[ed.PointF](p))
                            path.CloseFigure()
                    for c in h.Get3dCurves(False) or []:
                        p = pts(c)
                        if len(p) > 2:
                            path.AddLines(System.Array[ed.PointF](p))
                            path.CloseFigure()
                    g.FillPath(ed.Color.FromArgb(*rgb), path)
                else:
                    for line in h.Explode() or []:
                        if isinstance(line, rg.Curve):
                            p = pts(line)
                            if len(p) > 1:
                                g.DrawLines(thin, System.Array[ed.PointF](p))
        for c in cs:
            p = pts(c)
            if len(p) > 1:
                g.DrawLines(pen, System.Array[ed.PointF](p))
    g.Dispose()
    return alien_assets._png(bmp)


def check_counts(els, max_curves=4000):
    n = sum(len(cs) for _, _, cs, _ in els)
    if n > max_curves:
        return "build() made %d curves; keep it under %d (draw what reads at drawing scale)" % (n, max_curves)
    return None


# ── text slots (symbol) ───────────────────────────────────────

class Slots(list):
    """The symbol's text values: text[i] beyond the end is "", so a missing value never raises."""
    def __getitem__(self, i):
        try:
            return list.__getitem__(self, i)
        except IndexError:
            return ""


def text_curves(text, plane, height, h_align="center", v_align="middle", bold=False):
    """Closed outline curves of text on a plane (the document's annotation font, bold on request), `height` in the
    plane's units. alien_bim.text_curves with a bold option."""
    text = str(text)
    if not text.strip() or height <= 0:
        return []
    ds = Rhino.DocObjects.DimensionStyle()
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc is not None:
        try:
            ds = doc.DimStyles.Current.Duplicate()
        except Exception:
            pass
    if bold:
        try:
            f = Rhino.DocObjects.Font.FromQuartetProperties(ds.Font.QuartetName, True, False)
            if f is not None:
                ds.Font = f
        except Exception:
            pass
    ds.TextHeight = 1.0
    te = rg.TextEntity.Create(text, rg.Plane.WorldXY, ds, False, 0, 0)
    if te is None:
        return []
    curves = [c for c in (te.CreateCurves(ds, False, 1.0, 0) or []) if c is not None]
    if not curves:
        return []
    bb = rg.BoundingBox.Empty
    for c in curves:
        bb.Union(c.GetBoundingBox(True))
    ax = {"left": bb.Min.X, "right": bb.Max.X}.get(h_align, (bb.Min.X + bb.Max.X) / 2)
    ay = {"bottom": bb.Min.Y, "top": bb.Max.Y}.get(v_align, (bb.Min.Y + bb.Max.Y) / 2)
    xf = (rg.Transform.PlaneToPlane(rg.Plane.WorldXY, plane) * rg.Transform.Scale(rg.Point3d.Origin, height)
          * rg.Transform.Translation(-ax, -ay, 0))
    for c in curves:
        c.Transform(xf)
    return curves


def label(s, x=0.0, y=0.0, height=2.5, align="center", valign="middle", bold=False):
    """For symbol code: closed outline curves of the string s at (x, y), text height `height`, in the build's own
    units (paper mm). align left|center|right and valign top|middle|bottom place the letters' extent on (x, y)."""
    s = "" if s is None else str(s)
    if not s.strip():
        return []
    a = {"centre": "center", "middle": "center", "mid": "center"}.get(str(align).lower(), str(align).lower())
    v = {"center": "middle", "centre": "middle", "baseline": "bottom"}.get(str(valign).lower(), str(valign).lower())
    plane = rg.Plane(rg.Point3d(float(x), float(y), 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis)
    return text_curves(s, plane, float(height), a if a in ("left", "center", "right") else "center",
                       v if v in ("top", "middle", "bottom") else "middle", bool(bold))


# ── files ─────────────────────────────────────────────────────

def write_3dm(path, geometry, unit_system, names=None, colours=None):
    """A .3dm in the given unit system (Rhino.UnitSystem), so a block baked from it comes in at the right size."""
    model = Rhino.FileIO.File3dm()
    model.Settings.ModelUnitSystem = unit_system
    for i, g in enumerate(geometry):
        attrs = Rhino.DocObjects.ObjectAttributes()
        if names and i < len(names) and names[i]:
            attrs.Name = names[i]
        if colours and i < len(colours) and colours[i] is not None:
            attrs.ObjectColor = colours[i]
            attrs.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
        if isinstance(g, rg.Brep):
            model.Objects.AddBrep(g, attrs)
        elif isinstance(g, rg.Curve):
            model.Objects.AddCurve(g, attrs)
        elif isinstance(g, rg.Hatch):
            model.Objects.AddHatch(g, attrs)
    model.Write(path, 8)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def nice_range(n):
    """Slider range for a built size n: half to twice it, on round numbers."""
    step = 0.05 if n >= 0.4 else 0.01
    return max(step, math.floor(0.5 * n / step) * step), math.ceil(2.0 * n / step) * step
