"""
Run the project agent's building tool without a model: place a building on the open canvas, then check every node
solved and the parts line up (walls on the footprint, slabs and roof over it, the stair inside). Rhino + Grasshopper
open, Alien loaded. Free (no model calls).

  python3 tests/agent/building.py                         a default house
  python3 tests/agent/building.py '{"kind": "office"}'    any building tool arguments as JSON
  python3 tests/agent/building.py '{"edit": "<group id>", "roof": "gable", "storeys": 3}'   change one placed before
  python3 tests/agent/building.py --capture out.png ...   also capture the viewport (iso, zoomed to the building)
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from mcp import call  # noqa: E402

# The tool calls MCP tools itself, so it runs on a worker thread after this run_code call returns.
START = r'''
using System.Linq; using System.Reflection;
var asm = System.AppDomain.CurrentDomain.GetAssemblies().First(a => a.GetName().Name == "Alien");
var t = asm.GetType("Alien.Ai.BuildingTool");
var jobject = System.AppDomain.CurrentDomain.GetAssemblies().First(a => a.GetName().Name == "Newtonsoft.Json").GetType("Newtonsoft.Json.Linq.JObject");
var args = jobject.GetMethod("Parse", new[] { typeof(string) }).Invoke(null, new object[] { %s });
var path = %s;
System.Threading.Tasks.Task.Run(() =>
{
    string text;
    try { text = ((System.ValueTuple<string, string>)t.GetMethod("Run", BindingFlags.Public | BindingFlags.Static).Invoke(null, new object[] { args })).Item1; }
    catch (System.Exception ex) { text = "{\"exception\": " + System.Text.Json.JsonSerializer.Serialize((ex.InnerException ?? ex).ToString()) + "}"; }
    System.IO.File.WriteAllText(path, text);
});
result = "started";
'''

CHECK = r'''
import json
import Grasshopper
import Rhino.Geometry as rg
doc = Grasshopper.Instances.ActiveCanvas.Document
want = %s
keep = ("solids", "parts", "outline", "edges", "voids")
out = {}
for o in doc.Objects:
    sid = o.InstanceGuid.ToString("N")[:8]
    if sid not in want:
        continue
    d = {}
    for p in o.Params.Output:
        if p.NickName not in keep:
            continue
        items = [g.ScriptVariable() for g in p.VolatileData.AllData(True)]
        bb = rg.BoundingBox.Empty
        n = 0
        for v in items:
            if isinstance(v, rg.GeometryBase):
                bb.Union(v.GetBoundingBox(True)); n += 1
            elif isinstance(v, rg.Point3d):
                bb.Union(v); n += 1
        d[p.NickName] = {"n": len(items), "bbox": [[round(bb.Min.X, 2), round(bb.Min.Y, 2), round(bb.Min.Z, 2)], [round(bb.Max.X, 2), round(bb.Max.Y, 2), round(bb.Max.Z, 2)]] if bb.IsValid else None}
    out[want[sid]] = d
open(%s, "w").write(json.dumps(out))
'''


def main():
    args = sys.argv[1:]
    shot = None
    if args and args[0] == "--capture":
        shot, args = args[1], args[2:]
    spec = json.loads(args[0]) if args else {}
    out = os.path.join(os.environ.get("TMPDIR", "/tmp"), "alien_building_%d.json" % os.getpid())
    if os.path.exists(out):
        os.remove(out)
    t0 = time.time()
    print(call("run_code", {"language": "csharp", "code": START % (json.dumps(json.dumps(spec)), json.dumps(out))})["text"])
    while not os.path.exists(out) and time.time() - t0 < 300:
        time.sleep(1)
    time.sleep(0.3)
    r = json.load(open(out))
    if "exception" in r:
        print(r["exception"])
        sys.exit(1)
    print("placed in %.1f s: %d nodes, %d wires, group %s, footprint %s, origin %s" % (time.time() - t0, len(r["placed"]), r["wires"], r["group"], r["footprint_m"], r["origin"]))
    if r.get("edited"):
        print(r["edited"])
    bad = 0
    for n in r["placed"]:
        flag = "" if n["status"] == "ok" and not n.get("unset") else "  <-- " + n.get("error", "") + " " + str(n.get("unset", ""))
        bad += bool(flag)
        print("  %-16s %-8s %-6s %s%s" % (n["role"], n["id"], n["status"], (n.get("info") or "")[:110], flag))
    for k in ("problems", "notes"):
        if r.get(k):
            print(k + ":", "; ".join(r[k]))
    ids = {n["id"]: n["role"] for n in r["placed"]}
    geo_file = out + ".geo"
    call("run_code", {"language": "python", "code": CHECK % (json.dumps(ids), json.dumps(geo_file))})
    geo = json.load(open(geo_file))

    def box(role, out_):
        return (geo.get(role, {}).get(out_) or {}).get("bbox")
    fp = box("grids", "outline")
    print("footprint", fp)
    for role, o in (("walls", "solids"), ("slabs", "solids"), ("roofs", "solids"), ("frame", "solids"),
                    ("doors", "parts"), ("windows", "parts"), ("stairs", "solids"), ("stairs_1", "solids"), ("balustrades", "solids")):
        if role in geo:
            print("  %-12s %-7s n=%-4s %s" % (role, o, geo[role].get(o, {}).get("n"), box(role, o)))
    if shot:
        focus = [n["id"] for n in r["placed"] if n["role"] in ("walls", "slabs", "roofs", "frame", "windows", "doors", "balustrades") or n["role"].startswith("stairs")]
        res = call("capture", {"view": "iso", "focus": focus, "width": 1000, "height": 700, "display": "Shaded"})
        if res["images"]:
            with open(shot, "wb") as f:
                f.write(base64.b64decode(res["images"][0]))
            print("saved", shot)
        else:
            print("capture:", res["text"][:200])
    sys.exit(1 if bad or r.get("problems") else 0)


if __name__ == "__main__":
    main()
