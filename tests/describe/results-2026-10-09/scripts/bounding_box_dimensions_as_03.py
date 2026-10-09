# NODE_INPUTS: geo:list[Geometry] | "geometry to measure"
# NODE_OUTPUTS: dimensions:str | "bounding box X, Y, Z sizes as text"
# Reports the bounding-box X, Y and Z size of each input geometry as text, one line per item.
import Rhino.Geometry as rg

# Sample geometry when nothing is wired
if not geo:
    geo = [rg.PolylineCurve([rg.Point3d(0, 0, 0), rg.Point3d(10, 0, 0),
                             rg.Point3d(10, 4, 0), rg.Point3d(3, 4, 6)])]

lines = []
for i, g in enumerate(geo):
    if g is None:
        lines.append(f"{i}: no geometry")
        continue
    bb = g.GetBoundingBox(True)
    if not bb.IsValid:
        lines.append(f"{i}: invalid bounding box")
        continue
    d = bb.Max - bb.Min
    lines.append(f"{i}: X {d.X:.3f}  Y {d.Y:.3f}  Z {d.Z:.3f}")

dimensions = "\n".join(lines)
