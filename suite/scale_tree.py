# NODE_INPUTS: species:str | "kind of tree" default="mature deciduous street tree", height:float | "tree height in elevation (m)" min=0.5 max=60 default=8
# NODE_INPUTS: view:str | "elevation or plan" default="elevation" options="elevation|plan", spread:float | "canopy width in plan (m); 0 = 0.75 x height" min=0 max=60 default=0
# NODE_INPUTS: variant:int | "another drawing of the same request" min=0 max=50 default=0
# NODE_INPUTS: generate:bool | "draw it with the image model (paid); a drawing made before comes back free" button
# NODE_INPUTS: detail:float | "outline detail, 0-1" min=0 max=1 default=0.5
# NODE_INPUTS: smooth:bool | "smooth curves (off: straight segments)" default=true, plane:Plane | "where it stands (default: XZ elevation plane, or XY for plan)"
# NODE_OUTPUTS: outline:list[Curve] | "silhouette outlines, canopy gaps included", fill:list[Brep] | "filled silhouette", hatch | "solid hatch, for baking"
# NODE_OUTPUTS: info:str | "prompt, model, cost, scale", image:str | "the generated image file"
# CATEGORY: Entourage
# Scale tree: an image model draws a tree in elevation or plan as a silhouette, traced into outlines, a fill and a hatch at true size.
#
# Only Generate calls the image model: a new species, view or variant waits for it; drawings made before come back
# free. Height, spread, detail, smooth and plane only re-trace or re-place the drawing.
import Rhino.Geometry as rg

import alien_graphics as ag

species = (species or "").strip() or "mature deciduous street tree"
plan = (view or "").strip().lower() == "plan"
if plane is None:
    plane = rg.Plane.WorldXY if plan else rg.Plane(rg.Point3d.Origin, rg.Vector3d.XAxis, rg.Vector3d.ZAxis)
size = (spread if spread and spread > 0 else 0.75 * height) if plan else height

got = ag.silhouette(ag.tree_prompt(species, "plan" if plan else "elevation"), "1:1" if plan else "3:4",
                    int(variant or 0), detail, allow_new=bool(generate))
outline, fill, hatch, image = [], [], [], ""
if got is None:
    info = ("Press Generate to draw '%s' (%s) with the image model (about US$0.034; nothing is sent until you do). "
            "Drawings made before come back free." % (species, "plan" if plan else "elevation"))
    print("Waiting for Generate")
else:
    loops, w, h, meta = got
    outline, fill, hatch = ag.place(loops, size, plane, "width" if plan else "height", smooth)
    image = meta["path"]
    home = rg.Plane.WorldXY if plan else rg.Plane(rg.Point3d.Origin, rg.Vector3d.XAxis, rg.Vector3d.ZAxis)
    ag.save_asset("%s (%s)" % (species, "plan" if plan else "elevation"), "scale_tree", meta, variant,
                  {"species": species, "height": height, "view": "plan" if plan else "elevation", "spread": spread or 0,
                   "variant": int(variant or 0), "detail": detail, "smooth": bool(smooth)}, outline, hatch, plane, home)
    holes = sum(1 for _, hole in loops if hole)
    info = "\n".join([
        "%s, %s, %s %.1f m: %d outline%s (%d gap%s)" % (species, "plan" if plan else "elevation", "canopy" if plan else "height",
                                                        size, len(outline) - holes, "" if len(outline) - holes == 1 else "s",
                                                        holes, "" if holes == 1 else "s"),
        "Drawn by %s, %s%s" % (meta["model"], "from cache (free)" if meta["cached"] else "%.1f s" % meta["seconds"],
                               "" if meta["cached"] else ", US$%.3f" % (meta.get("spent") or 0)),
        "Redrawn because %s" % "; ".join(meta["notes"]) if meta["notes"] else "Drawing passed its checks first time",
        "Prompt: " + meta["prompt"],
    ])
