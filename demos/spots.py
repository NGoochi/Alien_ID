# NODE_INPUTS: x:float | "first spot along X (m)" min=-100 max=100 default=0, y:float | "first spot along Y (m)" min=-100 max=100 default=0
# NODE_INPUTS: step:float | "spacing between spots (m)" min=0 max=50 default=3
# NODE_INPUTS: along:str | "which way the spots run" default="X" options="X|Y"
# NODE_INPUTS: facing:str | "plane orientation: XY lies flat (plan, objects standing up), XZ stands up (elevations)" default="XY" options="XY|XZ"
# NODE_OUTPUTS: a:Plane | "first spot", b:Plane | "second", c:Plane | "third", d:Plane | "fourth", e:Plane | "fifth", planes:list[Plane] | "all five"
# CATEGORY: Utilities
# Spots: five placement planes in a row, in metres whatever the model's units, for laying demo objects out side by side.
#
# Grasshopper's own points and planes are in model units, so a definition laid out in millimetres lands a thousand
# times too far apart in a metre model; these spots scale with the document.
import Rhino
import Rhino.Geometry as rg

doc = Rhino.RhinoDoc.ActiveDoc
u = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
run = rg.Vector3d(1, 0, 0) if along != "Y" else rg.Vector3d(0, 1, 0)
o = rg.Point3d((x or 0) * u, (y or 0) * u, 0)
planes = []
for i in range(5):
    p = o + run * ((step or 0) * u * i)
    planes.append(rg.Plane(p, rg.Vector3d.XAxis, rg.Vector3d.ZAxis) if facing == "XZ" else rg.Plane(p, rg.Vector3d.XAxis, rg.Vector3d.YAxis))
a, b, c, d, e = planes
