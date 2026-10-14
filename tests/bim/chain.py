"""
Building-suite chain test. Places every building node in a clear area of the open Grasshopper canvas, wires them the
way a project would, and checks the lot:

  bim_site -> levels, grids -> wall_types -> walls -> doors -> windows
           -> beams_columns -> structure_edit -> connectors (section_profile -> beam)
           -> curtain_wall
           -> slab_types -> slabs (stairs.voids -> openings), roof_types -> roofs
           -> stair_types -> stairs -> balustrades (stairs.edges -> path), balustrade_types
  roofs -> views, slabs -> exploded_axo

Wires join an output to a downstream input of the same name (site, levels, grids, wall_type, walls, members), plus
the few handoffs with different names (LINKS). Then
every node must have solved without an error or unset outputs, every Brep it outputs must be valid (and closed, except
outputs named like surfaces), and each node's run time is reported. Rhino + Grasshopper open, Alien loaded.

  python3 tests/bim/chain.py               place, check, leave the rig on the canvas
  python3 tests/bim/chain.py --clean       ...and delete it afterwards
  python3 tests/bim/chain.py --values '{"levels": {"count": 6}}'   set inputs first (by node name)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from mcp import call  # noqa: E402

NODES = ["bim_site", "levels", "grids", "wall_types", "walls", "doors", "windows",
         "beams_columns", "structure_edit", "connectors", "curtain_wall",
         "slab_types", "slabs", "roof_types", "roofs", "stair_types", "stairs", "balustrade_types", "balustrades",
         "section_profile", "views", "exploded_axo"]
EDGES = [("bim_site", "levels"), ("bim_site", "grids"), ("bim_site", "walls"), ("bim_site", "beams_columns"),
         ("bim_site", "curtain_wall"), ("levels", "walls"), ("levels", "beams_columns"), ("levels", "curtain_wall"),
         ("levels", "doors"), ("levels", "windows"), ("grids", "beams_columns"), ("wall_types", "walls"),
         ("walls", "doors"), ("doors", "windows"), ("beams_columns", "structure_edit"), ("structure_edit", "connectors"),
         ("bim_site", "slabs"), ("bim_site", "roofs"), ("bim_site", "stairs"), ("bim_site", "balustrades"),
         ("levels", "slabs"), ("levels", "roofs"), ("levels", "stairs"), ("levels", "balustrades"),
         ("slab_types", "slabs"), ("roof_types", "roofs"), ("stair_types", "stairs"), ("balustrade_types", "balustrades")]
# Pins with different names: (from node, output, to node, input)
LINKS = [("stairs", "voids", "slabs", "openings"), ("stairs", "edges", "balustrades", "path"),
         ("section_profile", "profile", "beams_columns", "beam"), ("roofs", "solids", "views", "geometry"),
         ("slabs", "solids", "exploded_axo", "geometry")]
OPEN_OK = ("surface", "panel", "face", "swatch_faces", "fill")  # outputs allowed to hold open Breps (flat previews)
X0, Y0 = 3200, 1200  # canvas area for the rig

CHECK = r'''
import json
import Grasshopper
import Rhino.Geometry as rg
doc = Grasshopper.Instances.ActiveCanvas.Document
ids = set(%s)
out = {}
for o in doc.Objects:
    sid = o.InstanceGuid.ToString("N")[:8]
    if sid not in ids:
        continue
    counts, bad, breps = {}, [], 0
    for p in o.Params.Output:
        counts[p.NickName] = p.VolatileData.DataCount
        for goo in p.VolatileData.AllData(True):
            v = goo.ScriptVariable()
            if isinstance(v, rg.Brep):
                breps += 1
                if not v.IsValid:
                    bad.append(p.NickName + ": invalid")
                elif not v.IsSolid and not any(k in p.NickName.lower() for k in %s):
                    bad.append(p.NickName + ": open")
            elif isinstance(v, rg.Curve) and not v.IsValid:
                bad.append(p.NickName + ": invalid curve")
    out[sid] = {"counts": counts, "breps": breps, "bad": sorted(set(bad))[:8], "nbad": len(bad)}
print(json.dumps(out))
'''


def tool(name, args):
    r = call(name, args)
    if r["isError"]:
        raise RuntimeError("%s: %s" % (name, r["text"]))
    return r["text"]


def main():
    clean = "--clean" in sys.argv
    values = json.loads(sys.argv[sys.argv.index("--values") + 1]) if "--values" in sys.argv else {}
    repo = json.loads(tool("repo", {}))
    root = repo.get("root") or repo.get("repository")
    present = [n for n in NODES if root is None or os.path.exists(os.path.join(root, "suite", n + ".py"))]
    missing = [n for n in NODES if n not in present]

    spec = [{"key": n, "component": "alien", "script": "suite/%s.py" % n,
             "x": X0 + (i % 4) * 480, "y": Y0 + (i // 4) * 950} for i, n in enumerate(present)]
    placed = json.loads(tool("add", {"nodes": spec}))["nodes"]
    ids = {n: placed[n] for n in present}
    pins = {}
    for n, i in ids.items():
        info = json.loads(tool("inspect", {"id": i, "sample": 0}))
        # inspect lists inputs as "name:type (...)" and outputs as {name: summary}
        pins[n] = ({str(x).split(":")[0].strip() for x in info.get("in", [])}, set(info.get("out", {})))

    links = []
    for a, b in EDGES:
        if a in ids and b in ids:
            for name in sorted(pins[a][1] & pins[b][0]):
                links.append("%s.%s>%s.%s" % (ids[a], name, ids[b], name))
    for a, out, b, inp in LINKS:
        if a in ids and b in ids and out in pins[a][1] and inp in pins[b][0]:
            links.append("%s.%s>%s.%s" % (ids[a], out, ids[b], inp))
    if links:
        tool("wire", {"links": links})
    for n, vals in values.items():
        if n in ids:
            tool("set_values", {"id": ids[n], "values": vals})

    t = time.time()
    solved = json.loads(tool("canvas", {"filter": "alien", "solve": True}))
    wall = time.time() - t
    checks = json.loads(json.loads(tool("run_code", {"language": "python", "code": CHECK % (json.dumps(list(ids.values())), json.dumps(OPEN_OK))}))["print"])

    failures = 0
    print("%-15s %-6s %8s %6s  %s" % ("node", "state", "ms", "breps", "problems"))
    by_id = {x["id"]: x for x in solved.get("nodes", [])}
    for n in present:
        i = ids[n]
        rep = json.loads(tool("inspect", {"id": i, "sample": 0}))
        state = by_id.get(i, {}).get("state", rep.get("status"))
        problems = []
        if rep.get("status") not in ("ok", None):
            problems.append("status %s: %s" % (rep.get("status"), rep.get("error", "")))
        if rep.get("unset_outputs"):
            problems.append("unset: " + ", ".join(rep["unset_outputs"]))
        if by_id.get(i, {}).get("msg"):
            problems.append(by_id[i]["msg"])
        c = checks.get(i, {})
        problems += c.get("bad", [])
        failures += bool(problems)
        print("%-15s %-6s %8s %6s  %s" % (n, state, rep.get("ms", "?"), c.get("breps", 0), "; ".join(problems) or "-"))
    print("wires: %d   whole solve: %.1f s   missing nodes: %s" % (len(links), wall, ", ".join(missing) or "none"))
    if clean:
        tool("delete", {"ids": list(ids.values())})
    sys.exit(1 if failures or missing else 0)


if __name__ == "__main__":
    main()
