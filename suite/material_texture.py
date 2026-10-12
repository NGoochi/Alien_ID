# NODE_INPUTS: finish:str | "the material finish" default="board-formed concrete", size:float | "real-world size one tile covers (m)" min=0.05 max=20 default=1.2
# NODE_INPUTS: variant:int | "another texture for the same finish" min=0 max=50 default=0, seamless:bool | "blend the edges so tiles repeat without seams" default=true
# NODE_INPUTS: add:bool | "add (or update) the material in the Rhino document" button, name:str | "material name (default: the finish)" default=""
# NODE_OUTPUTS: material | "display material: wire into Custom Preview", preview:list[Brep] | "3 x 3 tiles at real size, to see the repeat"
# NODE_OUTPUTS: texture:str | "texture image file", name:str | "material name", info:str | "model, cost, seam check"
# CATEGORY: Materials
# Material texture: an image model paints a tileable texture for a finish, made seamless, as a preview material and a Rhino render material at real-world scale.
import base64
import os

import Grasshopper
import Rhino
import Rhino.Geometry as rg
import System
import System.Drawing

import alien_ai
import alien_assets

WORK = 1024  # texture size (px)


def prompt_for(finish, size):
    return ("Seamless tileable texture of %s for architectural rendering, as one square material swatch. "
            "Photographed straight on, perfectly flat and evenly lit: no shadows, no perspective, no vignette, no "
            "highlights. The pattern fills the whole square edge to edge, at a scale where the square is about %.2g "
            "metres across. No objects, no text, no borders, no frame." % (finish, size))


def decode(data, side):
    """Image bytes -> (w, h, bytes per pixel, bytearray of pixels, stride == w * bpp), resized to side x side."""
    import Eto.Drawing as ed
    src = ed.Bitmap(System.IO.MemoryStream(System.Convert.FromBase64String(base64.b64encode(data).decode())))
    bmp = ed.Bitmap(src, side, side, ed.ImageInterpolation.High)
    bd = bmp.Lock()
    try:
        stride, bpp = bd.ScanWidth, bd.BytesPerPixel
        buf = System.Array.CreateInstance(System.Byte, stride * side)
        System.Runtime.InteropServices.Marshal.Copy(bd.Data, buf, 0, stride * side)
    finally:
        bd.Dispose()
    raw = bytearray(base64.b64decode(str(System.Convert.ToBase64String(buf))))
    rows = bytearray()
    for y in range(side):  # drop row padding
        rows += raw[y * stride:y * stride + side * bpp]
    return side, side, bpp, rows


def encode_png(w, h, bpp, pixels):
    """Pixels (same layout as decode) -> PNG bytes."""
    import Eto.Drawing as ed
    bmp = ed.Bitmap(w, h, ed.PixelFormat.Format32bppRgba if bpp == 4 else ed.PixelFormat.Format24bppRgb)
    bd = bmp.Lock()
    try:
        stride = bd.ScanWidth
        out = bytearray(stride * h)
        for y in range(h):
            out[y * stride:y * stride + w * bpp] = pixels[y * w * bpp:(y + 1) * w * bpp]
        arr = System.Convert.FromBase64String(base64.b64encode(bytes(out)).decode())
        System.Runtime.InteropServices.Marshal.Copy(arr, 0, bd.Data, len(out))
    finally:
        bd.Dispose()
    stream = System.IO.MemoryStream()
    bmp.Save(stream, ed.ImageFormat.Png)
    return base64.b64decode(str(System.Convert.ToBase64String(stream.ToArray())))


def seam_error(w, h, bpp, px):
    """Mean colour jump across the wrap-around edges, relative to the image's own neighbour differences (1.0 = no seam)."""
    def diff(a, b):
        return sum(abs(px[a + c] - px[b + c]) for c in range(3))
    edge = inner = 0
    for i in range(0, h, 4):
        edge += diff(i * w * bpp + (w - 1) * bpp, i * w * bpp)                  # right edge -> left edge
        inner += diff(i * w * bpp + (w // 2 - 1) * bpp, i * w * bpp + (w // 2) * bpp)
    for i in range(0, w, 4):
        edge += diff((h - 1) * w * bpp + i * bpp, i * bpp)                      # bottom edge -> top edge
        inner += diff((h // 2 - 1) * w * bpp + i * bpp, (h // 2) * w * bpp + i * bpp)
    return edge / max(inner, 1)


SEAM_LIMIT = 1.35  # edges this much rougher than the texture's own detail get blended; below it they already tile


def make_seamless(w, h, bpp, px, band=0.16):
    """
    Roll the image half a tile so its own continuous middle becomes the edges, then cross-fade the original back in
    over the seam cross that the roll created. The result repeats without seams.
    """
    row = w * bpp
    hw, hh = w // 2, h // 2
    rolled = bytearray()
    for y in range(h):
        src = px[((y + hh) % h) * row:((y + hh) % h + 1) * row]
        rolled += src[hw * bpp:] + src[:hw * bpp]
    b = max(4, int(band * w))
    weight = [0.0] * w
    for x in range(w):
        t = max(0.0, 1.0 - abs(x + 0.5 - hw) / b)
        weight[x] = t * t * (3 - 2 * t)  # smoothstep
    out = bytearray(rolled)
    for y in range(h):
        ty = max(0.0, 1.0 - abs(y + 0.5 - hh) / b)
        wy = ty * ty * (3 - 2 * ty)
        xs = range(w) if wy > 0 else range(max(0, hw - b), min(w, hw + b))
        base = y * row
        for x in xs:
            m = max(weight[x], wy)
            if m <= 0:
                continue
            i = base + x * bpp
            for c in range(3):
                out[i + c] = int(rolled[i + c] * (1 - m) + px[i + c] * m + 0.5)
    return out


def add_material(doc, mat_name, texture_path, size_m):
    """
    Create or update a PBR render material with the texture as base colour and bump, mapped in world coordinates
    (WCS box) at real-world size: one tile per size_m, on any object, with no UV mapping needed.
    """
    tex = Rhino.DocObjects.Texture()
    tex.FileReference = Rhino.FileIO.FileReference.CreateFromFullPath(texture_path)
    mat = Rhino.DocObjects.Material()
    mat.Name = mat_name
    mat.ToPhysicallyBased()
    mat.PhysicallyBased.SetTexture(tex, Rhino.DocObjects.TextureType.PBR_BaseColor)
    mat.PhysicallyBased.SetTexture(tex, Rhino.DocObjects.TextureType.Bump)
    rm = Rhino.Render.RenderMaterial.FromMaterial(mat, doc)
    rm.Name = mat_name
    for old in [m for m in doc.RenderMaterials if m.Name == mat_name]:
        doc.RenderMaterials.Remove(old)
    doc.RenderMaterials.Add(rm)
    tile = size_m * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem)   # model units
    child = rm.FirstChild
    while child is not None:
        if isinstance(child, Rhino.Render.RenderTexture):
            ctx = Rhino.Render.RenderContent.ChangeContexts.Program
            child.BeginChange(ctx)
            child.SetProjectionMode(Rhino.Render.TextureProjectionMode.WcsBox, ctx)
            child.SetRepeat(rg.Vector3d(1.0 / tile, 1.0 / tile, 1.0 / tile), ctx)
            child.EndChange()
        child = child.NextSibling
    return rm


# ── run ──

finish = (finish or "").strip() or "board-formed concrete"
size = size if size and size > 0 else 1.2
name = (name or "").strip() or finish.capitalize()
prompt = prompt_for(finish, size) + ("" if not variant else " (Variation %d.)" % int(variant))
data, meta = alien_ai.image(prompt, "1:1")

out_dir = alien_ai.cache_dir("textures")
key = alien_ai.key_of(meta["path"], WORK, bool(seamless))
texture = os.path.join(out_dir, key + ".png")
asset_params = {"size": size, "variant": int(variant or 0), "seamless": bool(seamless)}
before = after = None
if not os.path.exists(texture):
    w, h, bpp, px = decode(data, WORK)
    before = seam_error(w, h, bpp, px)
    if seamless and before > SEAM_LIMIT:   # blending already-tileable patterns (boards, bricks) only adds ghosting
        px = make_seamless(w, h, bpp, px)
        after = seam_error(w, h, bpp, px)
    with open(texture, "wb") as f:
        f.write(encode_png(w, h, bpp, px))
if not alien_assets.exists("texture", prompt, asset_params):
    try:
        with open(texture, "rb") as f:
            png = f.read()
        alien_assets.save("texture", finish, prompt, asset_params,
                          "material_texture", files={"texture.png": png}, thumbnail=alien_assets.thumbnail_from_image(png),
                          extra={"size_m": size, "inputs": {"finish": finish, "size": size, "variant": int(variant or 0),
                                                            "seamless": bool(seamless)}})
    except Exception as ex:
        print("could not save to the asset library: %s" % ex)

doc = Rhino.RhinoDoc.ActiveDoc
units = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
base = Rhino.DocObjects.Material()
base.DiffuseColor = System.Drawing.Color.White
base.SetBitmapTexture(texture)
display = Rhino.Display.DisplayMaterial(base)
material = Grasshopper.Kernel.Types.GH_Material(display)  # Custom Preview's material input only takes GH's own type

tile = size * units
preview = []
for i in range(3):
    for j in range(3):
        srf = rg.PlaneSurface(rg.Plane(rg.Point3d(i * tile, j * tile, 0), rg.Vector3d.ZAxis), rg.Interval(0, tile), rg.Interval(0, tile))
        preview.append(srf.ToBrep())

added = ""
if add and doc:
    add_material(doc, name, texture, size)
    added = "Added to the document as '%s' (base colour + bump, one tile per %.2f m)." % (name, size)

info = "\n".join(l for l in [
    "%s: one tile = %.2f m, %d px" % (finish, size, WORK),
    "Painted by %s, %s" % (meta["model"], "from cache" if meta["cached"] else "%.1f s, US$%.3f" % (meta["seconds"], meta["cost"] or 0)),
    ("Seam check: edges %.1fx the texture's own detail before, %.1fx after blending" % (before, after)) if after is not None else
    ("Seam check: edges %.1fx the texture's own detail: %s" % (before, "already tiles, left as drawn" if seamless else "seamless off"))
    if before is not None else "",
    added,
] if l)
