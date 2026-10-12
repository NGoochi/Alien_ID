# NODE_INPUTS: species:str | "kind of tree" default="mature deciduous street tree", height:float | "tree height in elevation (m)" min=0.5 max=60 default=8
# NODE_INPUTS: view:str | "elevation or plan" default="elevation" options="elevation|plan", spread:float | "canopy width in plan (m); 0 = 0.75 x height" min=0 max=60 default=0
# NODE_INPUTS: variant:int | "another drawing of the same request" min=0 max=50 default=0, detail:float | "outline detail, 0-1" min=0 max=1 default=0.5
# NODE_INPUTS: smooth:bool | "smooth curves (off: straight segments)" default=true, plane:Plane | "where it stands (default: XZ elevation plane, or XY for plan)"
# NODE_OUTPUTS: outline:list[Curve] | "silhouette outlines, canopy gaps included", fill:list[Brep] | "filled silhouette", hatch | "solid hatch, for baking"
# NODE_OUTPUTS: info:str | "prompt, model, cost, scale", image:str | "the generated image file"
# CATEGORY: Entourage
# Scale tree: an image model draws a tree in elevation or plan as a silhouette, traced into outlines, a fill and a hatch at true size.
import Rhino.Geometry as rg

import alien_graphics as ag

species = (species or "").strip() or "mature deciduous street tree"
plan = (view or "").strip().lower() == "plan"
if plane is None:
    plane = rg.Plane.WorldXY if plan else rg.Plane(rg.Point3d.Origin, rg.Vector3d.XAxis, rg.Vector3d.ZAxis)
size = (spread if spread and spread > 0 else 0.75 * height) if plan else height

loops, w, h, meta = ag.silhouette(ag.tree_prompt(species, "plan" if plan else "elevation"), "1:1" if plan else "3:4",
                                  int(variant or 0), detail)
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
    "Drawn by %s, %s%s" % (meta["model"], "from cache" if meta["cached"] else "%.1f s" % meta["seconds"],
                           "" if meta["cached"] else ", US$%.3f" % (meta.get("spent") or 0)),
    "Redrawn because %s" % "; ".join(meta["notes"]) if meta["notes"] else "Drawing passed its checks first time",
    "Prompt: " + meta["prompt"],
])
