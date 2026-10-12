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
    meshes = []  # (mesh, (r, g, b))
    for i, g in enumerate(geometry):
        col = colours[i] if colours and i < len(colours) and colours[i] is not None else None
        rgb = (col.R, col.G, col.B) if col is not None else (200, 200, 200)
        if isinstance(g, rg.Brep):
            meshes += [(m, rgb) for m in (rg.Mesh.CreateFromBrep(g, mp) or []) if m]
        elif isinstance(g, rg.Mesh):
            meshes.append((g.DuplicateMesh(), rgb))
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

    for m, rgb in meshes:
        m.Faces.ConvertQuadsToTriangles()
        m.FaceNormals.ComputeFaceNormals()
        v = m.Vertices
        for i in range(m.Faces.Count):
            f = m.Faces[i]
            n = m.FaceNormals[i]
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
