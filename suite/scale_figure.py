# NODE_INPUTS: who:str | "who, doing what" default="an adult walking", height:float | "figure height (m)" min=0.3 max=2.5 default=1.75
# NODE_INPUTS: view:str | "which way the figure faces" default="side" options="side|front|back|three-quarter", variant:int | "another drawing of the same request" min=0 max=50 default=0
# NODE_INPUTS: detail:float | "outline detail, 0-1" min=0 max=1 default=0.5, smooth:bool | "smooth curves (off: straight segments)" default=true
# NODE_INPUTS: plane:Plane | "where the figure stands (default: the XZ elevation plane at the origin)"
# NODE_OUTPUTS: outline:list[Curve] | "silhouette outlines, holes included", fill:list[Brep] | "filled silhouette", hatch | "solid hatch, for baking"
# NODE_OUTPUTS: info:str | "prompt, model, cost, scale", image:str | "the generated image file"
# CATEGORY: Entourage
# Scale figure: an image model draws a person as a silhouette, traced into outlines, a fill and a hatch at true height for elevations and sections.
import re

import Rhino.Geometry as rg

import alien_graphics as ag

who = (who or "").strip() or "an adult walking"
if plane is None:
    plane = rg.Plane(rg.Point3d.Origin, rg.Vector3d.XAxis, rg.Vector3d.ZAxis)  # elevation: drawn in XZ, seen from -Y
# wide subjects get a wider canvas, so the figure isn't squeezed
aspect = "4:3" if re.search(r"bike|bicycle|cycl|pram|stroller|dog|bench|wheelchair|group|couple|people", who, re.I) else "2:3"

loops, w, h, meta = ag.silhouette(ag.figure_prompt(who, view), aspect, int(variant or 0), detail)
outline, fill, hatch = ag.place(loops, height, plane, "height", smooth)
image = meta["path"]
ag.save_asset("%s (%s)" % (who, view), "scale_figure", meta, variant,
              {"who": who, "height": height, "view": view, "variant": int(variant or 0), "detail": detail,
               "smooth": bool(smooth)}, outline, hatch, plane, rg.Plane(rg.Point3d.Origin, rg.Vector3d.XAxis, rg.Vector3d.ZAxis))

holes = sum(1 for _, hole in loops if hole)
info = "\n".join([
    "%s, %s view, %.2f m tall: %d outline%s (%d hole%s)" % (who, view, height, len(outline) - holes,
                                                           "" if len(outline) - holes == 1 else "s", holes, "" if holes == 1 else "s"),
    "Drawn by %s, %s%s" % (meta["model"], "from cache" if meta["cached"] else "%.1f s" % meta["seconds"],
                           "" if meta["cached"] else ", US$%.3f" % (meta.get("spent") or 0)),
    "Redrawn because %s" % "; ".join(meta["notes"]) if meta["notes"] else "Drawing passed its checks first time",
    "Prompt: " + meta["prompt"],
])
