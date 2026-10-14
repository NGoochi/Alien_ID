"""
Alien view helpers: shared by the view and drawing nodes (views, layout, exploded_axo). Keep next to them.

What they make in the Rhino document is tagged, so a press replaces exactly what the last press of the same set made
and nothing else:
  named views    registered in the document's string table, section "alien.views": entry = view name, value = JSON
                 {"node", "set", "kind", "w", "h"} (w/h: the view's frame in metres; kind: plan, rcp, top, elevation,
                 section, iso, axo, persp)
  clip planes    user string "alien.views" = "<node>|<set>", on the hidden "Alien views" layer (hidden planes still clip)
  layout pages   registered in section "alien.layout": entry = the page's viewport id, value = JSON {"set", "name"};
                 the page's details, text and linework carry the user string "alien.layout" = <set>
Rhino records a named view's clipping planes from the viewport it is saved from, so views are saved through the model
Perspective viewport (camera, display mode, name and its own clipping planes are put back straight after), and the
active view is always left on that Perspective viewport so other tools' captures keep working.
"""
import json
import math

import Rhino
import Rhino.Geometry as rg
import System
import System.Drawing

import alien_bim as ab

REG = "alien.views"
TAG = "alien.views"
LAYOUT = "alien.layout"
PLANES_LAYER = "Alien views"


def doc():
    return Rhino.RhinoDoc.ActiveDoc


# ── names and the registry ───────────────────────────────────

def with_separator(prefix):
    """'Alien views' -> 'Alien views · '; a prefix already ending in a space or separator is kept as typed."""
    p = str(prefix or "")
    if p and p[-1] not in " ·-_:/|.":
        p += " · "
    return p


def title_of(name):
    """A view's title without its set prefix: 'Alien views · North elevation' -> 'North elevation'."""
    s = str(name)
    return s.split(" · ")[-1].strip() if " · " in s else s.strip()


def registry(d=None, section=REG):
    d = d or doc()
    out = {}
    for entry in d.Strings.GetEntryNames(section) or []:
        try:
            out[entry] = json.loads(d.Strings.GetValue(section, entry) or "{}")
        except ValueError:
            out[entry] = {}
    return out


def register(d, name, section=REG, **info):
    d.Strings.SetString(section, name, json.dumps(info))


def forget(d, name, section=REG):
    d.Strings.Delete(section, name)


def kind_of(name, d=None):
    """A named view's kind: from the registry, else guessed from its name."""
    info = registry(d).get(name)
    if info and info.get("kind"):
        return info["kind"]
    n = str(name).lower()
    for k, words in (("rcp", ("rcp", "ceiling")), ("section", ("section",)), ("elevation", ("elevation",)),
                     ("plan", ("plan", "top")), ("persp", ("persp",)), ("iso", ("iso", "axo"))):
        if any(w in n for w in words):
            return k
    return "other"


# ── Rhino views ──────────────────────────────────────────────

def perspective_view(d=None):
    """The model Perspective view (by name, else the first perspective model view)."""
    d = d or doc()
    views = list(d.Views.GetViewList(True, False) or [])
    for v in views:
        if v.MainViewport.Name == "Perspective":
            return v
    for v in views:
        if v.MainViewport.IsPerspectiveProjection:
            return v
    return views[0] if views else None


def back_to_model(d=None):
    """Make the model Perspective viewport the active view again (pages and named views can take it over)."""
    d = d or doc()
    v = perspective_view(d)
    if v is not None:
        if d.Views.ActiveView is None or d.Views.ActiveView.MainViewport.Id != v.MainViewport.Id:
            d.Views.ActiveView = v
        v.Redraw()
    return v


def find_mode(name):
    """A display mode by English or local name (case-insensitive), or None."""
    s = str(name or "").strip()
    if not s:
        return None
    m = Rhino.Display.DisplayModeDescription.FindByName(s)
    if m is not None:
        return m
    for m in Rhino.Display.DisplayModeDescription.GetDisplayModes():
        if s.lower() in (m.EnglishName.lower(), m.LocalName.lower()):
            return m
    return None


def mode_names():
    return [m.EnglishName for m in Rhino.Display.DisplayModeDescription.GetDisplayModes()]


def planes_layer(d):
    """Index of the layer for clipping planes, created hidden when missing (an existing one is left as it is)."""
    layer = d.Layers.FindName(PLANES_LAYER)
    if layer is not None:
        return layer.Index
    idx = d.Layers.Add(PLANES_LAYER, System.Drawing.Color.FromArgb(150, 150, 150))
    lay = d.Layers[idx]
    lay.IsVisible = False
    lay.CommitChanges()
    return idx


def clip_objects(d):
    """Every clipping plane, hidden ones included (they still clip)."""
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.ObjectTypeFilter = Rhino.DocObjects.ObjectType.ClipPlane
    es.HiddenObjects = True
    es.LockedObjects = True
    es.NormalObjects = True
    return list(d.Objects.GetObjectList(es) or [])


def clear_views(d, node, key, stale=None):
    """Delete the named views and clipping planes a node made for one set (prefix or name). Returns the view names;
    `stale` (a list) collects the layout details that were clipped by the deleted planes."""
    gone = []
    persp = perspective_view(d)
    pid = persp.MainViewport.Id if persp else None
    for name, info in registry(d).items():
        if info.get("node") == node and info.get("set") == key:
            if d.NamedViews.FindByName(name) >= 0:
                d.NamedViews.Delete(name)
            forget(d, name)
            gone.append(name)
    for o in list(d.Objects.FindByUserString(TAG, "%s|%s" % (node, key), True) or []):
        if stale is not None and isinstance(o, Rhino.DocObjects.ClippingPlaneObject):
            stale += [i for i in o.ClippingPlaneGeometry.ViewportIds() or [] if i != pid and i not in stale]
        d.Objects.Delete(o, True)
    return gone


def clash(d, name):
    """True when a named view of this name exists and is not one an Alien view node made (so it must not be touched)."""
    return d.NamedViews.FindByName(name) >= 0 and name not in registry(d)


def save_views(d, specs, node, key, mode=None):
    """
    Create named views from specs (dicts): name, kind, d (camera direction), up, centre (target), hw, hh (half frame,
    model units), clip (Plane whose normal points away from the camera, or None), extra (more clipping planes, e.g.
    the far side of a limited depth), persp, lens, box (BoundingBox to zoom a perspective to). Each view is saved
    from the Perspective viewport so its clipping planes are recorded with it; the viewport is put back afterwards.
    Returns (made, skipped).
    """
    view = perspective_view(d)
    if view is None:
        return [], [(s["name"], "no model view to save from") for s in specs]
    vp = view.MainViewport
    saved = Rhino.DocObjects.ViewportInfo(vp)
    saved_mode, saved_name = vp.DisplayMode, vp.Name
    u = ab.units()
    # Clipping planes already active in this viewport (the user's) would be recorded in every view: lift them off.
    lifted = [o for o in clip_objects(d) if vp.Id in list(o.ClippingPlaneGeometry.ViewportIds() or [])]
    for o in lifted:
        o.ClippingPlaneGeometry.RemoveClipViewportId(vp.Id)
        o.CommitChanges()
    made, skipped = [], []
    layer = None
    try:
        for s in specs:
            name = s["name"]
            if clash(d, name):
                skipped.append((name, "a named view of that name exists and is not Alien's"))
                continue
            cids = []
            # The cut, and for a limited view depth a second plane facing back (a clipping plane's own depth setting
            # works in model views but not in layout details).
            planes = ([s["clip"]] if s.get("clip") is not None else []) + list(s.get("extra") or [])
            for pl in planes:
                if layer is None:
                    layer = planes_layer(d)
                attrs = Rhino.DocObjects.ObjectAttributes()
                attrs.LayerIndex = layer
                attrs.Name = name
                attrs.SetUserString(TAG, "%s|%s" % (node, key))
                cids.append(d.Objects.AddClippingPlane(pl, s["hw"], s["hh"], [vp.Id], attrs))
            c, dv, up = s["centre"], s["d"], s["up"]
            dist = 2 * max(s["hw"], s["hh"], s.get("deep", 0)) + 100 * u
            if s.get("persp"):
                vp.ChangeToPerspectiveProjection(True, s.get("lens", 35.0))
                vp.SetCameraLocations(c, c - dv * dist)
                vp.CameraUp = up
                vp.ZoomBoundingBox(s["box"])
            else:
                vp.ChangeToParallelProjection(True)
                vp.SetCameraLocations(c, c - dv * dist)
                vp.CameraUp = up
                # the exact frame, not ZoomBoundingBox's padded one in the window's shape (a named view keeps the
                # frustum the viewport has when it is saved; changing a saved view's frustum doesn't stick)
                vpi = Rhino.DocObjects.ViewportInfo(vp)
                ok, l, r, b, t, n, f = vpi.GetFrustum()
                if ok and vpi.SetFrustum(-s["hw"], s["hw"], -s["hh"], s["hh"], n, f):
                    vp.SetViewProjection(vpi, False)
            idx = d.NamedViews.Add(name, vp.Id)
            if idx >= 0:
                if mode is not None:  # the display mode is set on a copy and the view replaced by it
                    vi = d.NamedViews[idx]
                    vi.DisplayModeId = mode.Id
                    d.NamedViews.Delete(idx)
                    d.NamedViews.Add(vi)
                register(d, name, node=node, set=key, kind=s["kind"], w=round(2 * s["hw"] / u, 4), h=round(2 * s["hh"] / u, 4))
                made.append(name)
            else:
                skipped.append((name, "Rhino refused the view"))
            for cid in cids:  # the planes clip only through their named view (and details that show it)
                o = d.Objects.FindId(cid)
                if o is not None:
                    o.ClippingPlaneGeometry.RemoveClipViewportId(vp.Id)
                    o.CommitChanges()
    finally:
        vp.SetViewProjection(saved, True)
        if saved_mode is not None:
            vp.DisplayMode = saved_mode
        vp.Name = saved_name
        for o in lifted:
            o.ClippingPlaneGeometry.AddClipViewportId(vp.Id)
            o.CommitChanges()
        back_to_model(d)
    return made, skipped


# ── drawing helpers ──────────────────────────────────────────

_memo = {}


def letters(text, plane, height, h="center", v="middle"):
    """ab.text_curves, outlined once per string and alignment and copied (outlining is the slow part)."""
    key = (str(text), h, v)
    if key not in _memo:
        _memo[key] = ab.text_curves(text, rg.Plane.WorldXY, 1.0, h, v)
    xf = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, plane) * rg.Transform.Scale(rg.Point3d.Origin, height)
    out = []
    for c in _memo[key]:
        c = c.DuplicateCurve()
        c.Transform(xf)
        out.append(c)
    return out


def dashed(a, b, dash, gap):
    """A dashed line from a to b as short LineCurves (previews have no linetypes)."""
    v = b - a
    length = v.Length
    if length < 1e-9:
        return []
    if dash <= 0 or dash + gap <= 0 or length / (dash + gap) > 400:
        return [rg.LineCurve(a, b)]
    v.Unitize()
    out, t = [], 0.0
    while t < length - 1e-9:
        out.append(rg.LineCurve(a + v * t, a + v * min(t + dash, length)))
        t += dash + gap
    return out


def as_geometry(g):
    """Wire values -> GeometryBase (lines, polylines, rectangles, circles, arcs, boxes and points included)."""
    if g is None:
        return None
    if isinstance(g, rg.GeometryBase):
        return g
    if isinstance(g, rg.Line):
        return rg.LineCurve(g)
    if isinstance(g, rg.Polyline):
        return rg.PolylineCurve(g)
    if isinstance(g, (rg.Rectangle3d, rg.Circle, rg.Arc)):
        return g.ToNurbsCurve()
    if isinstance(g, rg.Box):
        return g.ToBrep()
    if isinstance(g, rg.Point3d):
        return rg.Point(g)
    return None


def as_cuttable(g):
    """Brep or Mesh to intersect with a plane (None for curves, points and the rest)."""
    if isinstance(g, (rg.Brep, rg.Mesh)):
        return g
    if isinstance(g, rg.Extrusion):
        return g.ToBrep()
    if isinstance(g, rg.SubD):
        try:
            return g.ToBrep(rg.SubDToBrepOptions.Default)
        except Exception:
            return None
    if isinstance(g, rg.Surface):
        return g.ToBrep()
    return None


def cut(geo, plane, tol):
    """Section curves of breps and meshes through a plane (geometry whose box misses the plane is skipped)."""
    out = []
    for g in geo:
        bb = g.GetBoundingBox(False)
        if not bb.IsValid:
            continue
        sides = [plane.DistanceTo(p) for p in bb.GetCorners()]
        if min(sides) > tol or max(sides) < -tol:
            continue
        c = as_cuttable(g)
        try:
            if isinstance(c, rg.Brep):
                ok, crv, _ = rg.Intersect.Intersection.BrepPlane(c, plane, tol)
                if ok and crv:
                    out += list(rg.Curve.JoinCurves(crv, tol) or crv)
            elif isinstance(c, rg.Mesh):
                out += [rg.PolylineCurve(pl) for pl in (rg.Intersect.Intersection.MeshPlane(c, plane) or [])]
        except Exception:
            pass
    return out


def iso_direction(angle):
    """Camera direction of a true isometric view from SW, SE, NE or NW (looking across and 35.26 degrees down)."""
    a = str(angle or "SW").strip().upper()
    sx = 1 if "W" in a else -1      # camera on the west side looks east (+X)
    sy = 1 if "S" in a else -1      # camera on the south side looks north (+Y)
    v = rg.Vector3d(sx, sy, -math.sqrt(2.0))
    v.Unitize()
    return v


def up_for(dv):
    """World Z made perpendicular to a camera direction (world Y for straight up/down views)."""
    if abs(dv.Z) > 0.999:
        return rg.Vector3d(0, 1, 0)
    up = rg.Vector3d.ZAxis - dv * (dv * rg.Vector3d.ZAxis)
    up.Unitize()
    return up


def extents(points, origin, right, up, dv):
    """(min/max along right, up and the view direction) of points relative to origin."""
    xs = [(p - origin) * right for p in points]
    ys = [(p - origin) * up for p in points]
    zs = [(p - origin) * dv for p in points]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)
