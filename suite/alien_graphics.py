"""
Alien context graphics: an image model draws a silhouette, this module traces it into vector outlines, a fill and a
hatch at true size. Used by the scale_figure and scale_tree nodes; keep it next to them in the script repository.

- The image model and key come from Alien's settings (settings.json: "imageModel", "openrouterKey").
- Every image is cached in <repository>/.alien/cache/graphics by request, so re-solving never pays twice; change
  `variant` on the node for another drawing of the same request.
- The drawing is checked after tracing (blank, cut off at the edge, flooded background); a failed drawing is redrawn
  once with a stricter prompt.
"""
import base64
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request

DEFAULT_IMAGE_MODEL = "google/gemini-3.1-flash-lite-image"
def _cache_dir():
    """<repository>/.alien/cache/graphics, shared by every copy of this helper (repository root, suite folder)."""
    try:
        import clr
        clr.AddReference("Alien")
        from Alien import ScriptRepository
        if ScriptRepository.Root:
            return os.path.join(str(ScriptRepository.Root), ".alien", "cache", "graphics")
    except Exception:
        pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".alien", "cache", "graphics")


CACHE = _cache_dir()
TRACE_PX = 480  # longest side of the image when traced
RULES = ("Draw it as a single solid pure-black silhouette{inside} on a plain pure-white background. Flat 2D, no grey, "
         "no gradients, no outline stroke, {extra}no shadow, no text, no border. The whole {thing} sits inside the image "
         "with a clear white margin on every side.")
STRICT = (" Important: nothing may touch or cross the edge of the image; leave a wide white margin all round, and draw "
          "only black on white.")

_traced = {}  # (image hash, detail) -> loops; survives between solves while this module stays loaded


# ── settings (through the Alien plugin, so the key never sits in a script) ──

def setting(key):
    try:
        import clr
        clr.AddReference("Alien")
        from Alien import ScriptRepository
        return ScriptRepository.Setting(key)
    except Exception:
        base = os.environ.get("APPDATA") or os.path.expanduser("~/Library/Application Support")
        try:
            with open(os.path.join(base, "Alien", "settings.json")) as f:
                return json.load(f).get(key)
        except (OSError, ValueError):
            return None


# ── prompts ──

def figure_prompt(subject, view):
    views = {"side": "seen exactly side-on, in profile", "front": "seen from the front", "back": "seen from behind",
             "three-quarter": "seen at three-quarters"}
    v = views.get((view or "side").strip().lower(), "seen " + view)
    return ("Architectural scale figure for a building elevation drawing: %s, %s, full body from head to feet. " % (subject, v)
            + RULES.format(inside=" with no interior detail", extra="no ground line, ", thing="figure"))


def tree_prompt(species, view):
    if (view or "").strip().lower() == "plan":
        return ("Architectural entourage for a site plan: one %s seen directly from above (top-down plan view), showing "
                "the outline of its canopy with a slightly irregular, leafy edge. " % species
                + RULES.format(inside="", extra="no trunk, ", thing="canopy"))
    return ("Architectural entourage for an elevation drawing: one %s, seen from the side at eye level, the whole tree "
            "from the base of the trunk at the bottom to the top of the crown. " % species
            + RULES.format(inside=" (trunk, branches and foliage) with natural gaps in the canopy where the white "
                                  "background shows through", extra="no ground, no grass, ", thing="tree"))


# ── image model ──

def image(prompt, aspect, variant=0, model=None, strict=False):
    """-> (image bytes, info dict). Cached by (model, prompt, aspect, variant)."""
    model = model or setting("imageModel") or DEFAULT_IMAGE_MODEL
    text = prompt + (STRICT if strict else "") + ("" if not variant else " (Variation %d.)" % variant)
    key = hashlib.sha1(json.dumps([model, text, aspect]).encode()).hexdigest()[:20]
    path = os.path.join(CACHE, key)
    if os.path.exists(path + ".img"):
        with open(path + ".img", "rb") as f:
            data = f.read()
        with open(path + ".json") as f:
            meta = json.load(f)
        meta["cached"], meta["path"] = True, path + ".img"
        return data, meta

    api_key = (setting("openrouterKey") or "").strip()
    if not api_key:
        raise ValueError("No OpenRouter key: add \"openrouterKey\" to Alien's settings.json (right-click a node > Settings).")
    body = {"model": model, "modalities": ["image", "text"], "messages": [{"role": "user", "content": text}],
            "usage": {"include": True}, "provider": {"data_collection": "deny"}}
    if model.startswith("google/"):
        body["image_config"] = {"aspect_ratio": aspect}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                                          "X-Title": "Alien"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as ex:
        detail = ex.read()[:300].decode("utf-8", "replace")
        raise RuntimeError("%s: HTTP %d %s" % (model, ex.code, detail))
    if "error" in res:
        raise RuntimeError("%s: %s" % (model, res["error"].get("message")))
    msg = res["choices"][0]["message"]
    images = msg.get("images") or []
    if not images:
        raise RuntimeError("%s drew nothing (said: %s)" % (model, (msg.get("content") or "")[:150]))
    data = base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1])
    meta = {"model": model, "prompt": text, "aspect": aspect, "cost": res.get("usage", {}).get("cost"),
            "seconds": round(time.time() - t, 1), "created": time.strftime("%Y-%m-%d %H:%M")}
    os.makedirs(CACHE, exist_ok=True)
    with open(path + ".img", "wb") as f:
        f.write(data)
    with open(path + ".json", "w") as f:
        json.dump(meta, f)
    meta["cached"], meta["path"] = False, path + ".img"
    return data, meta


def gray(data, side=TRACE_PX):
    """Image bytes (PNG/JPEG) -> (width, height, rows of 0-255 darkness-ready grey), downscaled with Eto (in Rhino)."""
    import System
    import Eto.Drawing as ed
    src = ed.Bitmap(System.IO.MemoryStream(System.Convert.FromBase64String(base64.b64encode(data).decode())))
    k = min(1.0, float(side) / max(src.Width, src.Height))
    w, h = max(2, int(src.Width * k)), max(2, int(src.Height * k))
    bmp = ed.Bitmap(src, w, h, ed.ImageInterpolation.High) if k < 1 else src
    bd = bmp.Lock()
    try:
        stride, bpp = bd.ScanWidth, bd.BytesPerPixel
        buf = System.Array.CreateInstance(System.Byte, stride * h)
        System.Runtime.InteropServices.Marshal.Copy(bd.Data, buf, 0, stride * h)
    finally:
        bd.Dispose()
    raw = base64.b64decode(str(System.Convert.ToBase64String(buf)))  # fast byte[] -> bytes
    rows = []
    for y in range(h):
        line = raw[y * stride:y * stride + w * bpp]
        # darkest channel: black stays 0 whichever order the channels (and alpha) come in
        rows.append([min(line[i:i + bpp]) for i in range(0, w * bpp, bpp)] if bpp > 1 else list(line))
    return w, h, rows


# ── tracing ──

def contours(w, h, rows, level=128):
    """Marching squares on the grey image (dark = inside) -> closed loops in pixel units, y up, sub-pixel accurate."""
    g = [[255] * (w + 2)] + [[255] + r + [255] for r in rows] + [[255] * (w + 2)]  # white border: every loop closes
    W, H = w + 2, h + 2
    ins = [[v < level for v in r] for r in g]
    links = {}

    def link(a, b):
        links.setdefault(a, []).append(b)
        links.setdefault(b, []).append(a)

    for y in range(H - 1):
        r0, r1, i0, i1 = g[y], g[y + 1], ins[y], ins[y + 1]
        for x in range(W - 1):
            c = (i0[x] << 3) | (i0[x + 1] << 2) | (i1[x + 1] << 1) | i1[x]
            if c == 0 or c == 15:
                continue
            top, right, bottom, left = (0, x, y), (1, x + 1, y), (0, x, y + 1), (1, x, y)
            if c in (1, 14):
                link(left, bottom)
            elif c in (2, 13):
                link(bottom, right)
            elif c in (3, 12):
                link(left, right)
            elif c in (4, 11):
                link(top, right)
            elif c in (6, 9):
                link(top, bottom)
            elif c in (7, 8):
                link(left, top)
            else:  # saddle: decide by the cell centre
                centre_in = (r0[x] + r0[x + 1] + r1[x] + r1[x + 1]) / 4.0 < level
                if (c == 5) == centre_in:
                    link(left, top)
                    link(bottom, right)
                else:
                    link(left, bottom)
                    link(top, right)

    def point(e):
        horiz, x, y = e
        a, b = (g[y][x], g[y][x + 1]) if horiz == 0 else (g[y][x], g[y + 1][x])
        t = 0.5 if a == b else min(1.0, max(0.0, (level - a) / float(b - a)))
        px, py = (x + t, y) if horiz == 0 else (x, y + t)
        return (px - 1, (H - 2) - py)  # undo the border, y up

    loops, seen = [], set()
    for start in links:
        if start in seen:
            continue
        loop, prev, cur = [], None, start
        while cur not in seen:
            seen.add(cur)
            loop.append(point(cur))
            nxt = [n for n in links[cur] if n != prev]
            prev, cur = cur, (nxt[0] if nxt else links[cur][0])
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def area(loop):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(loop, loop[1:] + loop[:1])) / 2.0


def inside(pt, loop):
    x, y, c = pt[0], pt[1], False
    for (x1, y1), (x2, y2) in zip(loop, loop[1:] + loop[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def simplify(loop, tol):
    """Douglas-Peucker on a closed loop."""
    if len(loop) < 8:
        return loop
    far = max(range(len(loop)), key=lambda i: (loop[i][0] - loop[0][0]) ** 2 + (loop[i][1] - loop[0][1]) ** 2)
    keep = [False] * len(loop)
    keep[0] = keep[far] = True
    stack = [(0, far), (far, len(loop))]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        (ax, ay), (bx, by) = loop[a], loop[b % len(loop)]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        best, at = -1.0, -1
        for i in range(a + 1, b):
            d = abs(dy * (loop[i][0] - ax) - dx * (loop[i][1] - ay)) / norm
            if d > best:
                best, at = d, i
        if best > tol:
            keep[at] = True
            stack += [(a, at), (at, b)]
    out = [p for p, k in zip(loop, keep) if k]
    return out if len(out) >= 3 else loop


def trace(data, detail=0.5):
    """Image bytes -> (loops [(points y-up, is_hole)], width, height). Specks below the detail threshold are dropped."""
    key = (hashlib.sha1(data).hexdigest(), round(detail, 3))
    if key in _traced:
        return _traced[key]
    w, h, rows = gray(data)
    raw = contours(w, h, rows)
    big = max((abs(area(l)) for l in raw), default=0)
    min_area = max(4.0, big * 10 ** (-2.0 - 2.0 * detail))  # smallest island or gap kept: 1% of the main shape at
    #                                                         detail 0, 0.1% at 0.5, 0.01% at 1
    tol = 0.35 + (1.0 - detail) * 1.6
    kept = [simplify(l, tol) for l in raw if abs(area(l)) >= min_area]
    loops = []
    for i, l in enumerate(kept):  # nesting depth decides outline or hole
        depth = sum(1 for j, o in enumerate(kept) if j != i and abs(area(o)) > abs(area(l)) and inside(l[0], o))
        loops.append((l, depth % 2 == 1))
    _traced[key] = (loops, w, h)
    return _traced[key]


def check(loops, w, h):
    """None when the drawing is usable, else what is wrong with it."""
    outer = [l for l, hole in loops if not hole]
    if not outer:
        return "the image is blank"
    pts = [p for l in outer for p in l]
    if min(p[0] for p in pts) < 1.5 or min(p[1] for p in pts) < 1.5 or max(p[0] for p in pts) > w - 1.5 or max(p[1] for p in pts) > h - 1.5:
        return "the drawing is cut off at the edge of the image"
    filled = sum(abs(area(l)) * (-1 if hole else 1) for l, hole in loops)
    if filled > 0.75 * w * h:
        return "the background is not white"
    return None


def silhouette(prompt, aspect, variant=0, detail=0.5, model=None):
    """Prompt -> (loops, width, height, info). Redraws once with a stricter prompt if the drawing fails its check."""
    notes, cost = [], 0.0
    for strict in (False, True):
        data, meta = image(prompt, aspect, variant, model, strict)
        cost += 0 if meta.get("cached") else (meta.get("cost") or 0)
        loops, w, h = trace(data, detail)
        problem = check(loops, w, h)
        if problem is None:
            meta["spent"] = cost
            meta["notes"] = notes
            return loops, w, h, meta
        notes.append(problem)
    raise RuntimeError("The image model's drawing was unusable (%s). Try another variant." % "; ".join(notes))


# ── geometry ──

def place(loops, size, plane, fit="height", smooth=True):
    """
    Loops (pixels, y up) -> Rhino outline curves, planar fills and solid hatches. Scaled so the drawing's height
    (fit="height") or longest side (fit="width") is `size` metres, converted to model units; the bottom centre of
    the drawing lands on the plane's origin (fit="width": the centre).
    """
    import Rhino
    import Rhino.Geometry as rg
    doc = Rhino.RhinoDoc.ActiveDoc
    units = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
    tol = doc.ModelAbsoluteTolerance if doc else 0.001
    pts = [p for l, hole in loops if not hole for p in l]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    if fit == "height":
        s, ox, oy = size / (y1 - y0), (x0 + x1) / 2.0, y0
    else:
        s, ox, oy = size / max(x1 - x0, y1 - y0), (x0 + x1) / 2.0, (y0 + y1) / 2.0
    s *= units

    curves = []
    for loop, hole in loops:
        p3 = [plane.PointAt((x - ox) * s, (y - oy) * s) for x, y in loop]
        c = None
        if smooth and len(p3) >= 5:
            c = rg.Curve.CreateInterpolatedCurve(p3 + [p3[0]], 3, rg.CurveKnotStyle.ChordPeriodic)  # repeat the start to close
            crossings = rg.Intersect.Intersection.CurveSelf(c, tol) if c is not None else None
            if c is None or not c.IsClosed or (crossings is not None and crossings.Count):
                c = None  # the smooth fit crossed itself: keep the polyline
        curves.append(c or rg.PolylineCurve(p3 + [p3[0]]))
    fill = list(rg.Brep.CreatePlanarBreps(curves, tol) or [])
    hatch = list(rg.Hatch.Create(curves, 0, 0.0, 1.0, tol) or [])
    return curves, fill, hatch


def save_asset(name, node, meta, variant, inputs, curves, hatches, plane, home):
    """
    Keep a drawing in the asset library (kind "graphic"): the image, plus the traced outlines and hatches moved back
    to metres on the node's home plane at the origin, so a baked block comes in at true size.
    """
    try:
        import Rhino
        import Rhino.Geometry as rg
        import alien_assets
        params = {"variant": int(variant or 0)}
        if alien_assets.exists("graphic", meta["prompt"], params):
            return
        doc = Rhino.RhinoDoc.ActiveDoc
        units = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
        to_home = rg.Transform.Scale(rg.Point3d.Origin, 1.0 / units) * rg.Transform.PlaneToPlane(plane, home)
        geometry = []
        for g in list(curves) + list(hatches):
            g = g.Duplicate()
            g.Transform(to_home)
            geometry.append(g)
        with open(meta["path"], "rb") as f:
            data = f.read()
        ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else ".png"
        alien_assets.save("graphic", name, meta["prompt"], params, node, files={"drawing" + ext: data},
                          geometry=geometry, thumbnail=alien_assets.thumbnail_from_image(data),
                          extra={"inputs": inputs, "model": meta.get("model")})
    except Exception as ex:
        print("could not save to the asset library: %s" % ex)
