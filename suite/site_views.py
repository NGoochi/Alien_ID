# NODE_INPUTS: site:list[Curve] | "site boundary (e.g. from vic_site_map)", context:float | "site plan reach beyond the site (m)" min=5 max=300 default=40
# NODE_INPUTS: geometry:list[Geometry] | "context to frame and cut: buildings, terrain, roads (optional)", align:bool | "align to the site's long axis instead of north" default=false
# NODE_INPUTS: sheet:str | "layout sheet to make with every view" default="A1" options="none|A3|A2|A1", run:bool | "create (or replace) the named views, section planes and sheet in Rhino" button
# NODE_OUTPUTS: views:list[str] | "view names", planes:list[Plane] | "picture plane of each view", frames:list[Curve] | "what each view shows", sections:list[Curve] | "cut lines through the geometry"
# NODE_OUTPUTS: info:str | "views, scales, what was created"
# CATEGORY: Views & drawings
# Site views: plan, site plan, X and Y centreline sections and four elevations around a site, as Rhino named views (sections clipped) and an optional layout sheet.
#
# Sections cut through the site's centre along its X and Y axes (true north, or the site's long axis with align).
# Elevations look at each side of the site with a clipping plane just outside it, so the street and neighbours in
# front don't hide it. Run replaces everything it made before (tagged "alien.site_views").
import math

import Rhino
import Rhino.Geometry as rg
import System.Drawing

TAG = "alien.site_views"
PREFIX = "Alien · "
SHEETS = {"A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0)}
SCALES = [50, 100, 200, 250, 500, 1000, 1250, 2000, 2500, 5000]

doc = Rhino.RhinoDoc.ActiveDoc
units = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
tol = doc.ModelAbsoluteTolerance if doc else 0.001

# ── site and frame ──

curves = [c for c in (site or []) if c is not None]
if not curves:  # sample site when nothing is wired: 18 x 36 m
    curves = [rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(-9 * units, 9 * units), rg.Interval(-18 * units, 18 * units)).ToNurbsCurve()]
pts = [p for c in curves for p in [c.PointAt(t) for t in c.DivideByCount(64, True)]]
closed = [c for c in curves if c.IsClosed]
amp = rg.AreaMassProperties.Compute(closed) if closed else None
centre = amp.Centroid if amp else rg.Point3d(sum(p.X for p in pts) / len(pts), sum(p.Y for p in pts) / len(pts), sum(p.Z for p in pts) / len(pts))

angle = 0.0
if align:  # long axis of the minimum-area rectangle around the site's hull
    best = None
    hull = [(p.X, p.Y) for p in pts]
    for i in range(len(hull)):
        (x1, y1), (x2, y2) = hull[i], hull[(i + 1) % len(hull)]
        a = math.atan2(y2 - y1, x2 - x1)
        cs, sn = math.cos(a), math.sin(a)
        us = [x * cs + y * sn for x, y in hull]
        vs = [-x * sn + y * cs for x, y in hull]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            best = (area, a if (max(us) - min(us)) >= (max(vs) - min(vs)) else a + math.pi / 2)
    angle = best[1]
ex = rg.Vector3d(math.cos(angle), math.sin(angle), 0)
ey = rg.Vector3d(-math.sin(angle), math.cos(angle), 0)
ez = rg.Vector3d.ZAxis


def uv(p):
    d = p - centre
    return d * ex, d * ey


us, vs = zip(*[uv(p) for p in pts])
u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
ctx = (context if context and context > 0 else 40.0) * units

geo = [g for g in (geometry or []) if g is not None]
ground = min(p.Z for p in pts)
top = ground + 12 * units
if geo:
    box = rg.BoundingBox.Empty
    reach = rg.BoundingBox(centre + ex * (u0 - ctx) + ey * (v0 - ctx) + ez * -1e6, centre + ex * (u1 + ctx) + ey * (v1 + ctx) + ez * 1e6)
    for g in geo:
        b = g.GetBoundingBox(True)
        if b.IsValid:
            box.Union(b)
    if box.IsValid:
        ground = min(ground, box.Min.Z)
        top = max(top, box.Max.Z)
height = top - ground

# ── the views ──
# Each: name, camera direction, up, centre point, half width, half height, clip plane (or None), kind


def at(u, v, z):
    return centre + ex * u + ey * v + ez * (z - centre.Z)


margin = 0.15 * max(u1 - u0, v1 - v0) + 2 * units
vmargin = 0.15 * height + 1 * units
mid_z = (ground + top) / 2.0
views = []
views.append(("Plan", -ez, ey, at((u0 + u1) / 2, (v0 + v1) / 2, top), (u1 - u0) / 2 + margin, (v1 - v0) / 2 + margin, None, "plan"))
views.append(("Site plan", -ez, ey, at((u0 + u1) / 2, (v0 + v1) / 2, top), (u1 - u0) / 2 + ctx, (v1 - v0) / 2 + ctx, None, "plan"))
# Sections: cut through the centre; look across the cut, hiding what is between the camera and the cut.
views.append(("Section X", ey, ez, at((u0 + u1) / 2, 0, mid_z), (u1 - u0) / 2 + margin, height / 2 + vmargin,
              rg.Plane(centre, ex, ez), "section"))      # cut along X, looking north (+Y of the frame)
views.append(("Section Y", -ex, ez, at(0, (v0 + v1) / 2, mid_z), (v1 - v0) / 2 + margin, height / 2 + vmargin,
              rg.Plane(centre, ey, ez), "section"))      # cut along Y, looking west (-X of the frame)
# Elevations: camera outside each side, clip just outside the site so what's in front doesn't hide it.
gap = 0.5 * units
for name, d, u, v, half in (("Elevation north", -ey, (u0 + u1) / 2, v1 + gap, (u1 - u0) / 2),
                            ("Elevation south", ey, (u0 + u1) / 2, v0 - gap, (u1 - u0) / 2),
                            ("Elevation east", -ex, u1 + gap, (v0 + v1) / 2, (v1 - v0) / 2),
                            ("Elevation west", ex, u0 - gap, (v0 + v1) / 2, (v1 - v0) / 2)):
    p = at(u, v, mid_z)
    views.append((name, d, ez, p, half + margin, height / 2 + vmargin, rg.Plane(p, rg.Vector3d.CrossProduct(ez, d), ez), "elevation"))


def clip_plane(view):
    """
    The view's clipping plane through its cut. Rhino keeps what lies on the side its normal points to (checked in
    Rhino 8), so the normal points away from the camera: what's between the camera and the cut is hidden.
    """
    _, d, _, _, _, _, plane, _ = view
    return None if plane is None else rg.Plane(plane.Origin, d)


# ── outputs for the canvas ──

names = [PREFIX + v[0] for v in views]
planes, frames = [], []
for name, d, up, c, hw, hh, cut, kind in views:
    right = rg.Vector3d.CrossProduct(d, up)
    right.Unitize()
    pl = rg.Plane(c, right, up)
    planes.append(pl)
    frames.append(rg.Rectangle3d(pl, rg.Interval(-hw, hw), rg.Interval(-hh, hh)).ToNurbsCurve())

sections = []
for name, d, up, c, hw, hh, cut, kind in views:
    if kind != "section" or not geo:
        continue
    across = rg.Vector3d.CrossProduct(d, up)
    found = []
    for g in geo:
        try:
            if isinstance(g, rg.Extrusion):
                g = g.ToBrep()
            if isinstance(g, rg.Brep):
                ok, crv, _ = rg.Intersect.Intersection.BrepPlane(g, cut, tol)
                found += list(crv or []) if ok else []
            elif isinstance(g, rg.Mesh):
                found += [rg.PolylineCurve(pl) for pl in (rg.Intersect.Intersection.MeshPlane(g, cut) or [])]
        except Exception as ex:
            print("could not cut %s: %s" % (type(g).__name__, ex))
    # only what the section shows: cut lines within its frame
    sections += [k for k in found if abs((k.GetBoundingBox(True).Center - c) * across) <= hw]


def nice_scale(width_model, width_paper_mm):
    """Smallest standard scale (1:n) at which width_model fits width_paper_mm."""
    need = width_model / units * 1000.0 / width_paper_mm
    return next((s for s in SCALES if s >= need), SCALES[-1])


info_lines = ["%d views around the site (%s), %.0f x %.0f m, heights %.1f to %.1f m" % (
    len(views), "aligned to its long axis" if align else "true north", (u1 - u0) / units, (v1 - v0) / units, ground / units, top / units)]
if sections:
    info_lines.append("%d cut lines through the geometry" % len(sections))


# ── create in Rhino ──

def clear_previous():
    for obj in list(doc.Objects.FindByUserString(TAG, "1", True) or []):
        doc.Objects.Delete(obj, True)
    for i in reversed(range(doc.NamedViews.Count)):
        if doc.NamedViews[i].Name.startswith(PREFIX):
            doc.NamedViews.Delete(i)
    for page in list(doc.Views.GetPageViews()):
        if page.PageName.startswith(PREFIX):
            page.Close()


def tagged():
    attrs = Rhino.DocObjects.ObjectAttributes()
    attrs.SetUserString(TAG, "1")
    layer = doc.Layers.FindName("Alien views")
    if layer is None:  # clipping planes and sheet labels live here (a hidden clipping plane stops clipping)
        idx = doc.Layers.Add("Alien views", System.Drawing.Color.FromArgb(150, 150, 150))
    else:
        idx = layer.Index
    attrs.LayerIndex = idx
    return attrs


def frame_box(c, d, up, hw, hh):
    right = rg.Vector3d.CrossProduct(d, up)
    right.Unitize()
    corners = [c + right * a * hw + up * b * hh for a in (-1, 1) for b in (-1, 1)]
    return rg.BoundingBox(corners)


def set_camera(vp, c, d, up, hw, hh):
    vp.ChangeToParallelProjection(True)
    dist = 2 * max(hw, hh, height) + 50 * units
    vp.SetCameraLocations(c, c - d * dist)
    vp.CameraUp = up
    vp.ZoomBoundingBox(frame_box(c, d, up, hw, hh))


if run and doc:
    clear_previous()
    # Named views are saved from a real viewport so Rhino records each one's clipping plane with it (the API has no
    # setter for that); the viewport's own camera is put back afterwards.
    view = doc.Views.ActiveView
    if view is None or isinstance(view, Rhino.Display.RhinoPageView):  # e.g. the old sheet was active and just closed
        standard = list(doc.Views.GetViewList(True, False))
        view = next((v for v in standard if v.MainViewport.IsPerspectiveProjection), standard[0])
    vp = view.MainViewport
    saved = Rhino.DocObjects.ViewportInfo(vp)
    clip_ids = {}
    for v in views:
        name, d, up, c, hw, hh, cut, kind = v
        cid = None
        if cut is not None:
            cid = doc.Objects.AddClippingPlane(clip_plane(v), hw * 1.05, hh * 1.05, [vp.Id], tagged())
            clip_ids[name] = cid
        set_camera(vp, c, d, up, hw, hh)
        doc.NamedViews.Add(PREFIX + name, vp.Id)
        if cid is not None:  # the plane clips only through its named view (and its detail on the sheet)
            cpo = doc.Objects.FindId(cid)
            cpo.ClippingPlaneGeometry.RemoveClipViewportId(vp.Id)
            cpo.CommitChanges()
    vp.SetViewProjection(saved, True)
    view.Redraw()
    info_lines.append("Named views: " + ", ".join(v[0] for v in views) + " (restore one from the Named Views panel)")

    if sheet in SHEETS:
        pw, ph = SHEETS[sheet]
        page = doc.Views.AddPageView(PREFIX + "Site views", pw, ph)
        pad, lab = pw / 70.0, pw / 50.0                  # margins, label band
        text_h = pw / 160.0
        # Top band: plan and site plan side by side. Below: three rows of wide strips for the sections and elevations.
        col_w = (pw - 3 * pad) / 2
        plan_h = (ph - 2 * pad) * 0.52 - lab
        strip_h = ((ph - 2 * pad) * 0.48 - 3 * lab - 3 * pad) / 3
        boxes = {"Plan": (pad, ph - pad - plan_h), "Site plan": (2 * pad + col_w, ph - pad - plan_h)}
        order = [("Section X", "Section Y"), ("Elevation north", "Elevation south"), ("Elevation east", "Elevation west")]
        for r, pair in enumerate(order):
            y = ph - pad - plan_h - lab - pad - (r + 1) * strip_h - r * (lab + pad)
            for k, nm in enumerate(pair):
                boxes[nm] = (pad + k * (col_w + pad), y)
        size = lambda nm: (col_w, plan_h) if nm in ("Plan", "Site plan") else (col_w, strip_h)
        fit = lambda v: max(nice_scale(2 * v[4], size(v[0])[0]), nice_scale(2 * v[5], size(v[0])[1]))
        shared = max(fit(v) for v in views if v[7] != "plan")       # sections and elevations: one scale
        scales = {v[0]: (fit(v) if v[0] == "Site plan" else max(fit(v), shared) if v[0] == "Plan" else shared) for v in views}
        technical = Rhino.Display.DisplayModeDescription.FindByName("Pen") or Rhino.Display.DisplayModeDescription.FindByName("Technical")
        label_layer = doc.Layers.FindName("Alien sheet labels")
        label_index = label_layer.Index if label_layer else doc.Layers.Add("Alien sheet labels", System.Drawing.Color.Black)
        for name, d, up, c, hw, hh, cut, kind in views:
            x0, y0 = boxes[name]
            w, h = size(name)
            detail = page.AddDetailView(name, rg.Point2d(x0, y0), rg.Point2d(x0 + w, y0 + h), Rhino.Display.DefinedViewportProjection.Top)
            if detail is None:
                continue
            page.SetActiveDetail(detail.Id)   # camera changes only stick on the active detail
            vp = detail.Viewport
            set_camera(vp, c, d, up, hw, hh)
            if technical is not None:          # hidden lines, silhouettes and section fills: drawing-like
                vp.DisplayMode = technical
            detail.CommitViewportChanges()
            detail.DetailGeometry.SetScale(scales[name], Rhino.UnitSystem.Millimeters, 1, Rhino.UnitSystem.Millimeters)
            detail.DetailGeometry.IsProjectionLocked = True
            detail.CommitChanges()
            if name in clip_ids:
                cpo = doc.Objects.FindId(clip_ids[name])
                if cpo is not None:
                    cpo.ClippingPlaneGeometry.AddClipViewportId(vp.Id)
                    cpo.CommitChanges()
            label = tagged()
            label.LayerIndex = label_index
            label.Space = Rhino.DocObjects.ActiveSpace.PageSpace
            label.ViewportId = page.MainViewport.Id
            doc.Objects.AddText("%s   1:%d" % (name, scales[name]), rg.Plane(rg.Point3d(x0, y0 - lab * 0.7, 0), rg.Vector3d.ZAxis),
                                text_h, "Arial", False, False, label)
        page.SetPageAsActive()
        page.Redraw()
        info_lines.append("Layout '%sSite views' (%s): plan 1:%d, site plan 1:%d, sections and elevations 1:%d" % (
            PREFIX, sheet, scales["Plan"], scales["Site plan"], shared))
    planes_layer = doc.Layers.FindName("Alien views")
    if planes_layer is not None and planes_layer.IsVisible:  # hidden clipping planes still clip, and stay out of the drawings
        planes_layer.IsVisible = False
        planes_layer.CommitChanges()
    doc.Views.Redraw()
else:
    info_lines.append("Set run to create them in Rhino (named views%s)." % ("" if sheet == "none" else " and an %s layout" % sheet))

views = names
info = "\n".join(info_lines)
