"""
Alien asset library: what suite nodes generate (drawings, 3D objects, textures) saved for reuse, in
<repository>/.alien/assets/<kind>/<id>/ with an asset.json, a thumbnail and the files (geometry as .3dm, images as
PNG). The Alien panel lists them. An asset's id comes from what made it, so regenerating the same thing updates it.
"""
import base64
import json
import math
import os
import time

import alien_ai


def asset_id(kind, prompt, params):
    return alien_ai.key_of(kind, prompt, params)[:12]


def asset_dir(kind, asset_id, create=True):
    path = os.path.join(alien_ai.repository(), ".alien", "assets", kind, asset_id)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def exists(kind, prompt, params):
    return os.path.exists(os.path.join(asset_dir(kind, asset_id(kind, prompt, params), False), "asset.json"))


def save(kind, name, prompt, params, node, files=None, geometry=None, names=None, colours=None, thumbnail=None,
         extra=None):
    """
    Write (or update) an asset. files: {filename: bytes}; geometry: Rhino geometry for geometry.3dm (with optional
    per-object names and System.Drawing colours); thumbnail: PNG bytes. Returns the asset folder.
    """
    aid = asset_id(kind, prompt, params)
    folder = asset_dir(kind, aid)
    listed = {}
    for filename, data in (files or {}).items():
        with open(os.path.join(folder, filename), "wb") as f:
            f.write(data)
        listed[os.path.splitext(filename)[0]] = filename
    if geometry:
        write_3dm(os.path.join(folder, "geometry.3dm"), geometry, names, colours)
        listed["geometry"] = "geometry.3dm"
    if thumbnail:
        with open(os.path.join(folder, "thumb.png"), "wb") as f:
            f.write(thumbnail)
        listed["thumbnail"] = "thumb.png"
    meta = {"id": aid, "kind": kind, "name": name, "prompt": prompt, "params": params, "node": node,
            "files": listed, "created": time.strftime("%Y-%m-%d %H:%M")}
    meta.update(extra or {})
    with open(os.path.join(folder, "asset.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return folder


# Library kinds besides what the generators make (object, texture, graphic, drawing, symbol, display_mode): the
# building types the BIM nodes define. The panel's Library groups them under "Building types".
TYPE_KINDS = ("wall_type", "slab_type", "roof_type", "stair_type", "balustrade_type", "door", "window", "profile")


def save_type(kind, name, record, node, inputs, geometry=None, colours=None, names=None, summary=None):
    """
    Keep a building type in the library: record.json (the type record exactly as the nodes pass it), a thumbnail drawn
    from `geometry` (a swatch, section or elevation; model units are fine), and the node's inputs, so the panel's
    "Place node" rebuilds the same type. `node` is the suite node's name ("wall_types"). The id comes from the record:
    saving the same type again updates it. Returns the asset id.
    """
    import Rhino
    rec = record if isinstance(record, str) else json.dumps(record, sort_keys=True)
    params = {"record": json.loads(rec)}
    geometry = [g for g in (geometry or []) if g is not None]
    if geometry:  # assets keep geometry in metres
        doc = Rhino.RhinoDoc.ActiveDoc
        u = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
        scaled = []
        for g in geometry:
            g = g.Duplicate()
            g.Transform(Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, 1.0 / u))
            scaled.append(g)
        geometry = scaled
    solids = [g for g in geometry if isinstance(g, (Rhino.Geometry.Brep, Rhino.Geometry.Mesh))]
    thumb = None
    if solids:
        cols = [colours[i] for i, g in enumerate(geometry) if isinstance(g, (Rhino.Geometry.Brep, Rhino.Geometry.Mesh))] if colours else None
        thumb = thumbnail_from_geometry(solids, colours=cols)
    elif geometry:
        thumb = thumbnail_from_curves(geometry, colours=colours)
    folder = save(kind, name, name, params, node, files={"record.json": rec.encode("utf-8")}, geometry=geometry or None,
                  names=names, colours=colours, thumbnail=thumb, extra={"inputs": inputs or {}, "size": summary})
    return os.path.basename(folder)


def write_3dm(path, geometry, names=None, colours=None):
    import Rhino
    model = Rhino.FileIO.File3dm()
    model.Settings.ModelUnitSystem = Rhino.UnitSystem.Meters
    for i, g in enumerate(geometry):
        attrs = Rhino.DocObjects.ObjectAttributes()
        if names and i < len(names) and names[i]:
            attrs.Name = names[i]
        if colours and i < len(colours) and colours[i] is not None:
            attrs.ObjectColor = colours[i]
            attrs.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
        if isinstance(g, Rhino.Geometry.Brep):
            model.Objects.AddBrep(g, attrs)
        elif isinstance(g, Rhino.Geometry.Mesh):
            model.Objects.AddMesh(g, attrs)
        elif isinstance(g, Rhino.Geometry.Curve):
            model.Objects.AddCurve(g, attrs)
        elif isinstance(g, Rhino.Geometry.Hatch):
            model.Objects.AddHatch(g, attrs)
    model.Write(path, 8)


# ── thumbnails (Eto, so they work on Windows and macOS) ──

def _png(bmp):
    import System
    import Eto.Drawing as ed
    stream = System.IO.MemoryStream()
    bmp.Save(stream, ed.ImageFormat.Png)
    return base64.b64decode(str(System.Convert.ToBase64String(stream.ToArray())))


def thumbnail_from_image(data, size=192):
    """A square-fitted PNG thumbnail of an image."""
    import System
    import Eto.Drawing as ed
    src = ed.Bitmap(System.IO.MemoryStream(System.Convert.FromBase64String(base64.b64encode(data).decode())))
    k = float(size) / max(src.Width, src.Height)
    return _png(ed.Bitmap(src, max(1, int(src.Width * k)), max(1, int(src.Height * k)), ed.ImageInterpolation.High))


def thumbnail_from_geometry(geometry, size=256, colours=None, background=(44, 44, 46)):
    """
    A shaded isometric PNG of Breps/Meshes (optionally one System.Drawing colour each): meshed, faces drawn
    far-to-near and lit from one side (no renderer needed, so it works inside a solve).
    """
    import System
    import Eto.Drawing as ed
    import Rhino.Geometry as rg

    box = rg.BoundingBox.Empty
    for g in geometry:
        box.Union(g.GetBoundingBox(True))
    mp = rg.MeshingParameters.FastRenderMesh
    mp.MaximumEdgeLength = max(box.Diagonal.Length / 30.0, 1e-6)  # small triangles keep the far-to-near order right
    mp.SimplePlanes = False
    meshes = []  # (mesh, (r, g, b), cull): faces of closed, outward Breps that face away are skipped
    for i, g in enumerate(geometry):
        col = colours[i] if colours and i < len(colours) and colours[i] is not None else None
        rgb = (col.R, col.G, col.B) if col is not None else (200, 200, 200)
        if isinstance(g, rg.Brep):
            cull = g.IsSolid and g.SolidOrientation == rg.BrepSolidOrientation.Outward
            meshes += [(m, rgb, cull) for m in (rg.Mesh.CreateFromBrep(g, mp) or []) if m]
        elif isinstance(g, rg.Mesh):
            meshes.append((g.DuplicateMesh(), rgb, False))
    tris = []
    # isometric camera: from (1, -1, 1) looking at the origin
    c, s = math.cos(math.radians(45)), math.sin(math.radians(45))
    tilt = math.radians(35.264)
    light = (0.45, -0.25, 0.86)

    def project(p):
        x, y = p.X * c + p.Y * s, -p.X * s + p.Y * c          # turn 45 degrees about Z
        depth = y * math.cos(tilt) - p.Z * math.sin(tilt)      # away from the viewer
        up = y * math.sin(tilt) + p.Z * math.cos(tilt)
        return x, up, depth

    for m, rgb, cull in meshes:
        m.Faces.ConvertQuadsToTriangles()
        m.FaceNormals.ComputeFaceNormals()
        v = m.Vertices
        for i in range(m.Faces.Count):
            f = m.Faces[i]
            n = m.FaceNormals[i]
            if cull and n.X - n.Y + n.Z < 0:  # faces away from the camera: drawn under the near faces they leak specks
                continue
            pts = [project(v[f.A]), project(v[f.B]), project(v[f.C])]
            shade = max(0.0, n.X * light[0] + n.Y * light[1] + n.Z * light[2])
            tris.append((sum(p[2] for p in pts) / 3.0, pts, 0.45 + 0.65 * shade, rgb))
    bmp = ed.Bitmap(size, size, ed.PixelFormat.Format32bppRgba)
    g = ed.Graphics(bmp)
    g.AntiAlias = True
    g.Clear(ed.Color.FromArgb(background[0], background[1], background[2]))
    if tris:
        xs = [p[0] for _, pts, _, _ in tris for p in pts]
        ys = [p[1] for _, pts, _, _ in tris for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
        k = size * 0.84 / span
        ox, oy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        for _, pts, shade, rgb in sorted(tris, key=lambda t: -t[0]):
            poly = System.Array[ed.PointF]([ed.PointF(size / 2.0 + (x - ox) * k, size / 2.0 - (y - oy) * k) for x, y, _ in pts])
            colour = ed.Color.FromArgb(*[min(255, int(c * shade)) for c in rgb])
            g.FillPolygon(colour, poly)
            g.DrawPolygon(colour, poly)  # same-colour outline closes the anti-aliased hairlines between triangles
    g.Dispose()
    return _png(bmp)


def thumbnail_from_curves(geometry, size=256, colours=None, background=(44, 44, 46), ink=(235, 235, 235)):
    """
    A top-view PNG of 2D work: curves drawn as lines, closed planar Breps and hatches filled (lighter), each in its own
    System.Drawing colour when given. For symbols, plan drawings and anything flat.
    """
    import System
    import Eto.Drawing as ed
    import Rhino.Geometry as rg

    items = []  # (kind, [[(x, y)...]], rgb)
    box = rg.BoundingBox.Empty
    for i, g in enumerate(geometry):
        col = colours[i] if colours and i < len(colours) and colours[i] is not None else None
        rgb = (col.R, col.G, col.B) if col is not None else ink
        loops, kind = [], "line"
        if isinstance(g, rg.Curve):
            loops = [g]
        elif isinstance(g, rg.Hatch):
            loops, kind = list(g.Get3dCurves(True) or []) + list(g.Get3dCurves(False) or []), "fill"  # outer edges and holes
        elif isinstance(g, rg.Brep):
            loops, kind = [e.DuplicateCurve() for e in g.Edges], ("fill" if g.Faces.Count == 1 and g.IsSurface else "line")
            if kind == "fill":
                loops = list(rg.Curve.JoinCurves(loops) or loops)
        polys = []
        for c in loops:
            pl = c.ToPolyline(0, 0, 0.05, 0, 0, 0, 0, 0, True)
            if pl is None:
                continue
            ok, poly = pl.TryGetPolyline()
            if ok:
                pts = [(p.X, p.Y) for p in poly]
                polys.append(pts)
                for p in poly:
                    box.Union(p)
        if polys:
            items.append((kind, polys, rgb))
    bmp = ed.Bitmap(size, size, ed.PixelFormat.Format32bppRgba)
    g = ed.Graphics(bmp)
    g.AntiAlias = True
    g.Clear(ed.Color.FromArgb(background[0], background[1], background[2]))
    if items and box.IsValid:
        span = max(box.Max.X - box.Min.X, box.Max.Y - box.Min.Y) or 1.0
        k = size * 0.84 / span
        cx, cy = (box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0

        def pts(poly):
            return System.Array[ed.PointF]([ed.PointF(size / 2.0 + (x - cx) * k, size / 2.0 - (y - cy) * k) for x, y in poly])
        for kind, polys, rgb in sorted(items, key=lambda it: it[0] != "fill"):  # fills first, lines on top
            if kind == "fill":
                fill = ed.Color.FromArgb(int(rgb[0] * 0.55 + background[0] * 0.45), int(rgb[1] * 0.55 + background[1] * 0.45),
                                         int(rgb[2] * 0.55 + background[2] * 0.45))
                path = ed.GraphicsPath()  # even-odd, so holes (a hollow section, a void) stay open
                path.FillMode = ed.FillMode.Alternate
                for poly in polys:
                    path.AddLines(pts(poly))
                    path.CloseFigure()
                g.FillPath(fill, path)
            pen = ed.Pen(ed.Color.FromArgb(*rgb), max(1.0, size / 160.0))
            for poly in polys:
                g.DrawLines(pen, pts(poly))
    g.Dispose()
    return _png(bmp)
