"""
Alien types: what the doors and windows nodes need to keep an opening TYPE in the Alien library (alien_assets.save_type)
as kinds "door" and "window". Keep this file next to alien_bim.py and alien_walls.py.

Records (metres), the library's record.json:
  door_type    name, family, width, height, hand, swing, frame, design ({parts, key} of a wired design, or null)
  window_type  the same plus sill
The thumbnail and geometry.3dm hold one opening (frame, leaves or sashes, glass) moved home: bottom centre at the
origin, along world X, up world Z, its exterior facing -Y (towards the thumbnail's iso camera). Hand is not mirrored.
"""
import hashlib
import json

import Rhino.Geometry as rg

import alien_bim as bim


def design_key(geoms):
    """A short key for a wired design (part kinds and sizes), so types with different designs get different ids."""
    rows = []
    for g in geoms or []:
        bb = g.GetBoundingBox(True)
        rows.append([type(g).__name__] + [round(v, 4) for v in (bb.Max - bb.Min)] +
                    ([g.Faces.Count, g.Edges.Count] if isinstance(g, rg.Brep) else []))
    return hashlib.md5(json.dumps(rows).encode()).hexdigest()[:8]


def opening_type(kind, name, family, width, height, hand, swing, frame, geoms=None, sill=None):
    """A door_type / window_type record (dict). Blank name: one made from the family, size and hand."""
    mm = lambda v: int(round(float(v) * 1000))
    bits = []
    if kind == "door" or family in ("casement", "sliding"):
        bits.append(hand)
    if kind == "door" or family in ("casement", "awning", "hopper"):
        bits.append(swing)
    if geoms:
        bits.append("design")
    auto = "%s %s %dx%d%s" % (family[:1].upper() + family[1:], kind, mm(width), mm(height), " (%s)" % ", ".join(bits) if bits else "")
    rec = {"alien": kind + "_type", "name": str(name or "").strip() or auto, "family": family, "width": round(float(width), 6),
           "height": round(float(height), 6), "hand": hand, "swing": swing, "frame": round(float(frame), 6),
           "design": {"parts": len(geoms), "key": design_key(geoms)} if geoms else None}
    if sill is not None:
        rec["sill"] = round(float(sill), 6)
    return rec


def home(fr):
    """Transform from an opening's place in its wall (an alien_walls.Frame) to the type's home position."""
    left = fr.tan.X * fr.ext.Y - fr.tan.Y * fr.ext.X > 0  # exterior on the left of the wall's path
    src = rg.Plane(fr.origin, fr.tan, rg.Vector3d.ZAxis)
    dst = rg.Plane(rg.Point3d.Origin, -rg.Vector3d.XAxis if left else rg.Vector3d.XAxis, rg.Vector3d.ZAxis)
    return rg.Transform.PlaneToPlane(src, dst)  # a rotation: ext lands on -Y either way


def save_opening(kind, rec, fr, parts, colours, inputs, node):
    """Save an opening type with one opening's parts as its geometry and thumbnail. Returns the asset id."""
    import alien_assets
    xf = home(fr)
    geo, cols = [], []
    for g, c in zip(parts, colours):
        if g is None:
            continue
        g = g.DuplicateBrep() if isinstance(g, rg.Brep) else g.Duplicate()
        if g.Transform(xf):
            geo.append(g)
            cols.append(c)
    size = "%d x %d mm%s" % (round(rec["width"] * 1000), round(rec["height"] * 1000),
                             ", sill %d" % round(rec["sill"] * 1000) if rec.get("sill") is not None else "")
    return alien_assets.save_type(kind, rec["name"], bim.dump(rec), node, inputs, geometry=geo, colours=cols,
                                  summary=size)
