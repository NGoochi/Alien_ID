# NODE_INPUTS: mesh:Mesh, dir:Vector3d | "contour direction" default=[0,0,1], interval:float | "spacing" min=0.01 default=1.0, start:float | "offset along direction" default=0.0
# NODE_OUTPUTS: contours, count:int
# Slices a mesh with parallel planes spaced at a fixed interval along a direction.
import math
import Rhino
import Rhino.Geometry as rg

# sample geometry when nothing is wired
if mesh is None:
    mesh = rg.Mesh.CreateFromSphere(rg.Sphere(rg.Point3d(0, 0, 0), 5.0), 32, 32)

d = dir if isinstance(dir, rg.Vector3d) else rg.Vector3d(0, 0, 1)
if not d.IsValid or d.Length < 1e-12:
    d = rg.Vector3d(0, 0, 1)
d.Unitize()

contours = []
count = 0

if mesh is not None and mesh.IsValid and interval > 0:
    bb = mesh.GetBoundingBox(True)
    if bb.IsValid:
        c = bb.Center
        tc = c.X * d.X + c.Y * d.Y + c.Z * d.Z
        tmin = sum(min(bb.Min[i] * d[i], bb.Max[i] * d[i]) for i in range(3))
        tmax = sum(max(bb.Min[i] * d[i], bb.Max[i] * d[i]) for i in range(3))

        doc = Rhino.RhinoDoc.ActiveDoc
        tol = doc.ModelAbsoluteTolerance if doc else 0.001
        ctol = max(tol, (tmax - tmin) * 1e-6)

        verts = [rg.Point3d(v.X, v.Y, v.Z) for v in mesh.Vertices]
        tris = []
        for f in mesh.Faces:
            tris.append((f.A, f.B, f.C))
            if f.IsQuad:
                tris.append((f.A, f.C, f.D))

        ka = int(math.ceil((tmin - start) / interval))
        kb = int(math.floor((tmax - start) / interval))
        if kb - ka > 500:
            kb = ka + 500

        for k in range(ka, kb + 1):
            t = start + k * interval
            o = rg.Point3d(c.X + d.X * (t - tc), c.Y + d.Y * (t - tc), c.Z + d.Z * (t - tc))
            segs = []
            for ia, ib, ic in tris:
                vs = (verts[ia], verts[ib], verts[ic])
                ds = [(v.X - o.X) * d.X + (v.Y - o.Y) * d.Y + (v.Z - o.Z) * d.Z for v in vs]
                pts = []
                for a, b in ((0, 1), (1, 2), (2, 0)):
                    da, db = ds[a], ds[b]
                    if (da < 0) != (db < 0):
                        tt = da / (da - db)
                        va, vb = vs[a], vs[b]
                        pts.append(rg.Point3d(va.X + (vb.X - va.X) * tt,
                                              va.Y + (vb.Y - va.Y) * tt,
                                              va.Z + (vb.Z - va.Z) * tt))
                uniq = []
                for p in pts:
                    if not any(p.DistanceTo(q) < ctol for q in uniq):
                        uniq.append(p)
                if len(uniq) >= 2:
                    segs.append((uniq[0], uniq[1]))

            key = lambda p: (round(p.X / ctol), round(p.Y / ctol), round(p.Z / ctol))
            adj = {}
            for i, (a, b) in enumerate(segs):
                adj.setdefault(key(a), []).append(i)
                adj.setdefault(key(b), []).append(i)
            used = [False] * len(segs)
            for i in range(len(segs)):
                if used[i]:
                    continue
                used[i] = True
                a, b = segs[i]
                pts = [a, b]
                for forward in (True, False):
                    while True:
                        cur = pts[-1] if forward else pts[0]
                        kk = key(cur)
                        nxt = None
                        for j in adj.get(kk, ()):
                            if not used[j]:
                                nxt = j
                                break
                        if nxt is None:
                            break
                        used[nxt] = True
                        p, q = segs[nxt]
                        other = q if key(p) == kk else p
                        if other.DistanceTo(cur) > ctol:
                            if forward:
                                pts.append(other)
                            else:
                                pts.insert(0, other)
                if len(pts) >= 2:
                    contours.append(rg.PolylineCurve(rg.Polyline(pts)))

count = len(contours)
