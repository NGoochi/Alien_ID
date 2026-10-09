# NODE_INPUTS: pts:list[Point3d], radius:float default=2 min=0
# NODE_OUTPUTS: circles
# Draws a circle at each point, radius growing with the point's index.
import Rhino.Geometry as rg

# sample geometry when nothing is wired
centers = pts or [rg.Point3d(0, 0, 0), rg.Point3d(10, 0, 0), rg.Point3d(20, 0, 0)]

circles = [rg.Circle(p, radius * (i + 1)) for i, p in enumerate(centers)]
