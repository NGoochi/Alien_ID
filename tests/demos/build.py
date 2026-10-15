"""
Build the demo definitions: each one in a fresh Grasshopper document, laid out, checked (every node solves, no
errors) and saved to <script folder>/demos/<name>.gh, with a viewport preview in demos/previews/<name>.png.
Rhino + Grasshopper open, Alien loaded.

  python3 tests/demos/build.py                 build all
  python3 tests/demos/build.py site facade     build some (names below)
  python3 tests/demos/build.py --check         reopen every saved demo from disk and check it (no building)

Generators reuse their caches: only a request never made before calls a model (a 3D object about US$0.02, a 2D
drawing about US$0.02, an image-traced figure or a texture about US$0.034). The demo nodes the definitions use
(demos/spots.py, attractor_facade.py, texture_mapping.py) live in the script folder next to the .gh files; copies
are in this repo's demos/ folder.
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from mcp import call  # noqa: E402

ROOT = None
OUT = None


def tool(name, args=None):
    r = call(name, args or {})
    if r["isError"]:
        raise RuntimeError("%s: %s" % (name, r["text"][:600]))
    return r


def jt(name, args=None):
    return json.loads(tool(name, args)["text"])


def py(code):
    """Run Python in Rhino; returns what it printed."""
    r = jt("run_code", {"language": "python", "code": code})
    if not r.get("ok", True):
        raise RuntimeError("run_code: %s" % r.get("error"))
    return r.get("print", "")


# ── a document ──────────────────────────────────────────────

def new_doc():
    py('''
import Grasshopper
from Grasshopper.Kernel import GH_Document
d = GH_Document()
Grasshopper.Instances.DocumentServer.AddDocument(d)
Grasshopper.Instances.ActiveCanvas.Document = d
''')


def place(nodes, links=()):
    """nodes: (key, component or 'suite/x.py' / 'demos/x.py', x, y, values) -> {key: id}."""
    spec = []
    for key, comp, x, y, values in nodes:
        n = {"key": key, "x": x, "y": y}
        if comp.endswith(".py"):
            n.update(component="alien", script=comp)
        else:
            n["component"] = comp
        if values:
            n["values"] = values
        spec.append(n)
    r = jt("add", {"nodes": spec, "links": list(links)})
    ids = {}
    for k, v in r["nodes"].items():
        if v.startswith("error"):
            raise RuntimeError("%s: %s" % (k, v))
        if "(values:" in v:
            print("   note:", k, v)
        ids[k] = v.split(" ")[0]
    bad = [l for l in r.get("links", []) if l != "ok"]
    if bad:
        raise RuntimeError("wires: %s" % bad)
    return ids


def wire(links):
    r = jt("wire", {"links": links})
    bad = [l for l in r["results"] if l != "ok"]
    if bad:
        raise RuntimeError("wires: %s" % bad)


def values(id_, vals):
    return jt("set_values", {"id": id_, "values": vals})


def hide(ids):
    tool("set_values", {"ids": list(ids), "preview": False})


def swatches(colours):
    """{id: (r, g, b)}: set Colour Swatches."""
    py('''
import Grasshopper, System.Drawing
doc = Grasshopper.Instances.ActiveCanvas.Document
want = %s
for o in doc.Objects:
    k = o.InstanceGuid.ToString("N")[:8]
    if k in want:
        r, g, b = want[k]
        o.SwatchColour = System.Drawing.Color.FromArgb(255, r, g, b)
        o.ExpireSolution(False)
doc.NewSolution(False)
''' % json.dumps(colours))


def note(title, body, x, y, width=60):
    """A title and a few lines of instructions as scribbles on the canvas."""
    py('''
import Grasshopper, System.Drawing
from Grasshopper.Kernel.Special import GH_Scribble
doc = Grasshopper.Instances.ActiveCanvas.Document
def scribble(text, x, y, size, bold):
    s = GH_Scribble()
    s.CreateAttributes()
    s.Text = text
    s.Font = System.Drawing.Font("Helvetica Neue", size, System.Drawing.FontStyle.Bold if bold else System.Drawing.FontStyle.Regular)
    s.Attributes.Pivot = System.Drawing.PointF(x, y)
    doc.AddObject(s, False)
scribble(%s, %d, %d, 40, True)
scribble(%s, %d, %d, 14, False)
''' % (json.dumps(title), x, y, json.dumps(body), x, y + 70))


def size_panel(id_, w, h):
    py('''
import Grasshopper, System.Drawing
doc = Grasshopper.Instances.ActiveCanvas.Document
for o in doc.Objects:
    if o.InstanceGuid.ToString("N")[:8] == %s:
        b = o.Attributes.Bounds
        o.Attributes.Bounds = System.Drawing.RectangleF(b.X, b.Y, %d, %d)
        o.Attributes.ExpireLayout()
''' % (json.dumps(id_), w, h))


def solve():
    return jt("canvas", {"solve": True})


def problems():
    """Every node that isn't fine: Alien nodes not ok (or with unset outputs), anything with an error or warning."""
    c = jt("canvas", {"solve": True})
    out = []
    for n in c["nodes"]:
        if n.get("type") == "alien" and n.get("state") not in ("ok",):
            out.append("%s %s: %s" % (n["id"], n.get("script"), n.get("state")))
        elif n.get("msg"):
            out.append("%s %s: %s" % (n["id"], n.get("script") or n.get("type"), n["msg"]))
    for n in c["nodes"]:
        if n.get("type") == "alien" and n.get("state") == "ok":
            r = jt("inspect", {"id": n["id"], "sample": 0})
            if r.get("unset_outputs"):
                out.append("%s %s: unset %s" % (n["id"], n.get("script"), r["unset_outputs"]))
    return out, c


def save(name):
    path = os.path.join(OUT, name + ".gh")
    py('''
import Grasshopper
from Grasshopper.Kernel import GH_DocumentIO
doc = Grasshopper.Instances.ActiveCanvas.Document
io = GH_DocumentIO(doc)
print(io.SaveQuiet(%s))
''' % json.dumps(path))
    return path


def zoom_canvas():
    py('''
import Grasshopper, System.Drawing
c = Grasshopper.Instances.ActiveCanvas
doc = c.Document
b = None
for o in doc.Objects:
    r = o.Attributes.Bounds
    b = r if b is None else System.Drawing.RectangleF.Union(b, r)
if b is not None:
    s = c.ClientSize
    z = min(1.0, 0.9 * min(s.Width / (b.Width + 100.0), s.Height / (b.Height + 100.0)))
    c.Viewport.Zoom = max(0.1, z)
    c.Viewport.MidPoint = System.Drawing.PointF(b.X + b.Width / 2, b.Y + b.Height / 2)
c.Refresh()
''')


def shot(name, args):
    os.makedirs(os.path.join(OUT, "previews"), exist_ok=True)
    a = {"width": 1200, "height": 760, "display": "Shaded"}
    a.update(args)
    r = tool("capture", a)
    if r["images"]:
        p = os.path.join(OUT, "previews", name + ".png")
        with open(p, "wb") as f:
            f.write(base64.b64decode(r["images"][0]))
        return p


def finish(name, view, keep_preview_ids=()):
    zoom_canvas()
    bad, c = problems()
    path = save(name)
    pic = shot(name, view)
    alien = sum(1 for n in c["nodes"] if n.get("type") == "alien")
    print("  %s: %d nodes (%d Alien), saved %s%s" % (name, len(c["nodes"]), alien, path, "" if not bad else "\n  PROBLEMS: " + "; ".join(bad)))
    return not bad


# ── the demos ───────────────────────────────────────────────

def site():
    """Victorian site map in greyscale: each map layer its own grey, buildings white, trees see-through."""
    new_doc()
    layers = [  # output, grey, transparency (None: a plain swatch)
        ("ground", (214, 214, 214), None), ("contours", (150, 150, 150), None), ("buildings", (252, 252, 252), None),
        ("carriageway", (88, 88, 88), None), ("footpath", (190, 190, 190), None), ("nature_strip", (164, 164, 164), None),
        ("kerb", (130, 130, 130), None), ("canopy", (110, 110, 110), 0.25), ("trunk", (70, 70, 70), None),
    ]
    lines = [("lots", (110, 110, 110), 1), ("site", (0, 0, 0), 2)]  # output, grey, weight (draw order)
    nodes = [("map", "suite/vic_site_map.py", 0, 0, {})]
    links = []
    for i, (out, grey, t) in enumerate(layers):
        y = -560 + i * 110
        nodes.append(("sw_" + out, "Colour Swatch", 700, y, None))
        nodes.append(("pv_" + out, "Custom Preview", 980, y, None))
        links.append("map.%s>pv_%s.Geometry" % (out, out))
        if t is None:
            links.append("sw_%s>pv_%s.Material" % (out, out))
        else:
            nodes.append(("mat_" + out, "Create Material", 840, y + 40, {"T": t}))
            links += ["sw_%s>mat_%s.Kd" % (out, out), "mat_%s.M>pv_%s.Material" % (out, out)]
    for i, (out, grey, _) in enumerate(lines):
        # lot lines are flat at z 0, under the terrain: drape them onto it
        y = -560 + (len(layers) + i) * 130
        nodes.append(("drape_" + out, "demos/drape_curves.py", 420, 520 + i * 420, {"lift": 0.08 if out == "site" else 0.05}))
        nodes.append(("sw_" + out, "Colour Swatch", 700, y, None))
        nodes.append(("pv_" + out, "Custom Preview", 980, y, None))
        links += ["map.%s>drape_%s.curves" % (out, out), "map.ground>drape_%s.ground" % out,
                  "drape_%s.draped>pv_%s.Geometry" % (out, out), "sw_%s>pv_%s.Material" % (out, out)]
    nodes += [("info", "Panel", 0, 1400, None), ("zone", "Panel", 0, 1700, None)]
    links += ["map.info>info", "map.zone>zone"]
    ids = place(nodes, links)
    swatches({ids["sw_" + out]: grey for out, grey, _ in layers + lines})
    size_panel(ids["info"], 400, 260)
    size_panel(ids["zone"], 400, 60)
    hide([ids["map"], ids["drape_lots"], ids["drape_site"]])
    note("Site map", "An address becomes the lot, neighbours, roads, terrain, streets, trees and massing (Vicmap, Overture).\n"
         "Each layer has its own preview in a grey from its swatch (the site boundary heaviest): change a swatch to\n"
         "restyle the map. Type another Victorian address into the map node (it suggests as you type).", -60, -820)
    ok = finish("01_site_map", {"view": "iso", "focus": [ids["pv_ground"]]})
    shot("01_site_map_plan", {"view": "top", "focus": [ids["pv_ground"]]})
    return ok


def object3d():
    """3D objects written by a model: a new lounge chair, plus a table and chair from the cache."""
    new_doc()
    ids = place([
        ("spots", "demos/spots.py", 0, 0, {"step": 2.6}),
        ("lounge", "suite/object_3d.py", 420, -420, {"thing": "a mid-century lounge chair", "style": "walnut shell, tan leather cushions, splayed legs"}),
        ("table", "suite/object_3d.py", 420, 120, {"thing": "a dining table for six"}),
        ("chair", "suite/object_3d.py", 420, 600, {"thing": "a dining chair"}),
        ("preview", "Custom Preview", 900, 120, None),
        ("info", "Panel", 900, -420, None),
        ("code", "Panel", 900, 330, None),
    ], ["spots.a>lounge.plane", "spots.b>table.plane", "spots.c>chair.plane",
        "lounge.parts>preview.Geometry", "lounge.colours>preview.Material",
        "table.parts>preview.Geometry", "table.colours>preview.Material",
        "chair.parts>preview.Geometry", "chair.colours>preview.Material",
        "lounge.info>info", "lounge.code>code"])
    r = values(ids["lounge"], {"generate": True})  # new: the model writes it (a design made before is free)
    print("   lounge:", (r.get("out", {}).get("info") or r.get("error") or "")[:160])
    size_panel(ids["info"], 380, 200)
    size_panel(ids["code"], 560, 520)
    hide([ids["lounge"], ids["table"], ids["chair"]])
    note("3D objects", "Describe a thing (or drop in a picture) and press generate: a model writes RhinoCommon code that\n"
         "builds it from solids at real size. The code panel shows what it wrote. Width, depth and height resize\n"
         "the built design without asking the model again; a design made before comes back free.", -60, -820)
    return finish("02_object_3d", {"view": "iso", "focus": [ids["preview"]]})


def entourage():
    """2D entourage for an elevation: image-traced figures and a tree, and a car drawn by code."""
    new_doc()
    ids = place([
        ("spots", "demos/spots.py", 0, 0, {"step": 4.5, "facing": "XZ"}),
        ("couple", "suite/scale_figure.py", 420, -560, {"who": "2 adults walking and a dog", "view": "side"}),
        ("tree", "suite/scale_tree.py", 420, -120, {}),
        ("car", "suite/object_2d.py", 420, 300, {"thing": "a hatchback car, side elevation", "style": "outline with grey fills for body and windows, dark tyres"}),
        ("cyclist", "suite/scale_figure.py", 420, 760, {"who": "a person riding a bicycle", "view": "side"}),
        ("walker", "suite/scale_figure.py", 420, 1180, {"who": "an adult walking 2 dogs", "view": "side"}),
        ("ground", "4c4e56eb", 420, 1600, None),  # Line (Curve/Primitive)
        ("sw_dark", "Colour Swatch", 900, -420, None), ("sw_tree", "Colour Swatch", 900, -120, None),
        ("pv_figures", "Custom Preview", 1150, -420, None), ("pv_tree", "Custom Preview", 1150, -120, None),
        ("pv_car", "Custom Preview", 1150, 300, None), ("pv_lines", "Custom Preview", 1150, 600, None),
        ("info", "Panel", 900, 900, None),
    ], ["spots.a>couple.plane", "spots.b>tree.plane", "spots.c>car.plane", "spots.d>cyclist.plane", "spots.e>walker.plane",
        "spots.a>ground.A", "spots.e>ground.B",
        "couple.fill>pv_figures.Geometry", "cyclist.fill>pv_figures.Geometry", "walker.fill>pv_figures.Geometry",
        "sw_dark>pv_figures.Material", "tree.fill>pv_tree.Geometry", "sw_tree>pv_tree.Material",
        "car.fills>pv_car.Geometry", "car.colours>pv_car.Material",
        "car.curves>pv_lines.Geometry", "ground.L>pv_lines.Geometry", "sw_dark>pv_lines.Material",
        "car.info>info"])
    swatches({ids["sw_dark"]: (34, 34, 34), ids["sw_tree"]: (128, 128, 128)})
    for k in ("car", "cyclist"):  # new requests: drawn now (made before: free)
        r = values(ids[k], {"generate": True})
        print("   %s:" % k, (r.get("out", {}).get("info") or r.get("error") or "")[:160])
    size_panel(ids["info"], 380, 160)
    hide([ids[k] for k in ("couple", "tree", "car", "cyclist", "walker", "spots")])
    note("2D entourage", "People and trees are drawn by an image model and traced into outlines and fills at true height;\n"
         "the car is drawn by code a model writes. All stand on XZ planes for elevations (spots, 4.5 m apart).\n"
         "Change who / species / thing and press generate; drawings made before come back free.", -60, -860)
    return finish("03_entourage_2d", {"view": "front", "focus": [ids["pv_figures"], ids["pv_tree"], ids["pv_car"]]})


def frame():
    """Beam-to-column frame from catalogue sections, with an edit and the connections it needs."""
    new_doc()
    ids = place([
        ("spots", "demos/spots.py", 0, -700, {"x": -4, "y": 0, "step": 2, "along": "Y"}),
        ("column", "suite/section_profile.py", 0, -300, {"family": "UC", "size": "250UC89.5"}),
        ("beam", "suite/section_profile.py", 0, 300, {"family": "UB", "size": "410UB53.7"}),
        ("grids", "suite/grids.py", 420, -700, {"x_spacing": "2x7.2", "y_spacing": "6"}),
        ("levels", "suite/levels.py", 420, -300, {"count": 2, "height": 3.6}),
        ("members", "suite/beams_columns.py", 840, -500, {"material": "steel"}),
        ("edit", "suite/structure_edit.py", 1260, -500, {"select": "level=RF role=beam", "action": "resize", "section": "down"}),
        ("joints", "suite/connectors.py", 1680, -500, {"welds": True}),
        ("pv_frame", "Custom Preview", 2100, -560, None), ("pv_joints", "Custom Preview", 2100, -380, None),
        ("pv_sections", "Custom Preview", 2100, -200, None), ("sw_steel", "Colour Swatch", 1900, -150, None),
        ("schedule", "Panel", 1680, 200, None),
    ], ["spots.a>column.plane", "spots.b>beam.plane",
        "grids.grids>members.grids", "levels.levels>members.levels",
        "column.profile>members.column", "beam.profile>members.beam",
        "members.members>edit.members", "edit.members>joints.members",
        "edit.solids>pv_frame.Geometry", "edit.colours>pv_frame.Material",
        "joints.solids>pv_joints.Geometry", "joints.colours>pv_joints.Material",
        "column.fill>pv_sections.Geometry", "beam.fill>pv_sections.Geometry", "column.sample>pv_sections.Geometry",
        "beam.sample>pv_sections.Geometry", "sw_steel>pv_sections.Material",
        "joints.schedule>schedule"])
    swatches({ids["sw_steel"]: (70, 92, 120)})
    size_panel(ids["schedule"], 520, 420)
    hide([ids[k] for k in ("members", "edit", "joints", "spots", "column", "beam")])
    note("Frame and connections", "Catalogue sections (section_profile) set the columns and beams; structure_edit takes the roof beams down a\n"
         "size; connectors adds base plates, end and fin plates, bolts and welds at every joint, with a schedule.\n"
         "Try another size on either profile, or edit lines like 'B-L01-*: section=460UB67.1'.", -60, -1060)
    ok = finish("04_frame_connections", {"view": "iso", "focus": [ids["pv_frame"], ids["pv_joints"]]})
    u = float(py("import Rhino\nprint(Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, Rhino.RhinoDoc.ActiveDoc.ModelUnitSystem))"))
    # a close look at one beam-to-column joint: grid 2/A at Level 1 (3.6 m)
    shot("04_frame_connections_joint", {"view": "perspective", "camera": [5.9 * u, -1.4 * u, 4.5 * u], "look_at": [7.2 * u, 0, 3.4 * u]})
    return ok


def facade():
    """A perforated facade whose openings swell towards an attractor point."""
    new_doc()
    ids = place([
        ("facade", "demos/attractor_facade.py", 0, 0, {"u": 0.32, "v": 0.6, "shape": "circle", "reach": 11}),
        ("pv_panels", "Custom Preview", 520, -100, None),
        ("glass_colour", "Colour Swatch", 360, 140, None),
        ("glass_mat", "Create Material", 520, 140, {"T": 0.55}),
        ("pv_glass", "Custom Preview", 720, 140, None),
        ("point_colour", "Colour Swatch", 360, 260, None), ("pv_point", "Custom Preview", 720, 260, None),
        ("info", "Panel", 520, 400, None),
    ], ["facade.panels>pv_panels.Geometry", "facade.colours>pv_panels.Material",
        "facade.point>pv_point.Geometry", "point_colour>pv_point.Material",
        "glass_colour>glass_mat.Kd", "glass_mat.M>pv_glass.Material", "facade.glass>pv_glass.Geometry",
        "facade.info>info"])
    swatches({ids["glass_colour"]: (150, 185, 205), ids["point_colour"]: (200, 40, 40)})
    size_panel(ids["info"], 420, 120)
    hide([ids["facade"]])
    note("Attractor facade", "A perforated rainscreen on a curved facade: openings swell towards the attractor and panels darken with\n"
         "them. Drag u and v to move the attractor, or click 'attractor' and pick a point in Rhino; wire your own\n"
         "surface into facade. Shape, reach, sizes, panel count, depth and joints are all on the node.", -60, -420)
    return finish("05_attractor_facade", {"view": "perspective", "focus": [ids["pv_panels"]]})


def texture():
    """A generated texture made a material, mapped at real size onto a wall, plinth and bench."""
    new_doc()
    ids = place([
        ("tile", "Number Slider", 0, 0, None),
        ("texture", "suite/material_texture.py", 300, -200, {"finish": "rough sawn cedar cladding boards"}),
        ("mapping", "demos/texture_mapping.py", 300, 320, {}),
        ("preview", "Custom Preview", 780, 120, None),
        ("info", "Panel", 780, -200, None),
    ], ["tile>texture.size", "tile>mapping.size", "mapping.meshes>preview.Geometry", "texture.material>preview.Material",
        "texture.info>info"])
    values(ids["tile"], {"value": 1.2, "min": 0.2, "max": 4, "decimals": 2})
    py('''
import Grasshopper
doc = Grasshopper.Instances.ActiveCanvas.Document
for o in doc.Objects:
    if o.InstanceGuid.ToString("N")[:8] == %s:
        o.NickName = "tile size (m)"
''' % json.dumps(ids["tile"]))
    r = values(ids["texture"], {"generate": True})
    print("   texture:", (r.get("out", {}).get("info") or r.get("error") or "")[:200])
    values(ids["texture"], {"add": True})  # and into Rhino's material list, at the same scale
    size_panel(ids["info"], 420, 140)
    hide([ids["texture"], ids["mapping"]])
    note("Texture to material", "An image model paints a tileable texture for a finish; the node makes it seamless, a display material for\n"
         "previews, and (press add) a PBR render material in Rhino at the same real size. texture_mapping gives any\n"
         "geometry real-size coordinates, so one slider sets the tile size for both.", -60, -520)
    return finish("06_texture_material", {"view": "perspective", "focus": [ids["preview"]]})


def axo():
    """A house from the building suite, pulled apart storey by storey beside the original."""
    new_doc()
    out = os.path.join(os.environ.get("TMPDIR", "/tmp"), "alien_demo_building.json")
    if os.path.exists(out):
        os.remove(out)
    tool("run_code", {"language": "csharp", "code": BUILD % (json.dumps(json.dumps({"kind": "house", "roof": "hip"})), json.dumps(out))})
    t0 = time.time()
    while not os.path.exists(out) and time.time() - t0 < 300:
        time.sleep(1)
    time.sleep(0.5)
    house = json.load(open(out))
    roles = {n["role"]: n["id"] for n in house["placed"]}
    preview = house.get("preview")
    # what to explode, as geometry/colour pairs in one order
    parts = [("side_windows", "solids", "colours"), ("doors", "parts", "part_colours"), ("windows", "parts", "part_colours"),
             ("side_windows", "parts", "part_colours"), ("slabs", "solids", "colours"), ("roofs", "solids", "colours"),
             ("stairs", "solids", "colours"), ("balustrades", "solids", "colours")]
    parts = [p for p in parts if p[0] in roles]
    ids = place([
        ("axo", "suite/exploded_axo.py", 2900, 200, {"mode": "height bands", "band": 3.0, "spacing": 3.5, "beside": True,
                                                    "names": ["Ground floor", "Level 1", "Roof"]}),
        ("pv_axo", "Custom Preview", 3320, 200, None),
        ("sw_lines", "Colour Swatch", 3120, 520, None), ("pv_lines", "Custom Preview", 3320, 520, None),
    ], ["sw_lines>pv_lines.Material", "axo.exploded>pv_axo.Geometry", "axo.lines>pv_lines.Geometry", "axo.labels>pv_lines.Geometry"])
    wire(["%s.%s>%s.geometry" % (roles[k], g, ids["axo"]) for k, g, _ in parts])
    wire(["%s.%s>%s.Material" % (roles[k], c, ids["pv_axo"]) for k, _, c in parts])
    swatches({ids["sw_lines"]: (40, 40, 40)})
    hide([ids["axo"]])
    note("Exploded axo", "The building tool's house (the project agent places it from one request), with its walls, openings,\n"
         "slabs, stair, balustrades and roof pulled apart by storey (height bands) beside the original, with dashed\n"
         "explosion lines and labels. Press view on the axo node for a parallel isometric named view.", 0, -900)
    return finish("07_exploded_axo", {"view": "iso", "focus": [ids["pv_axo"], preview] if preview else [ids["pv_axo"]]})


def tower():
    """A framed tower: steel frame on the grids, post-tensioned slabs and a curtain wall all round."""
    new_doc()
    ids = place([
        ("site", "suite/bim_site.py", 0, 0, {"name": "Tower"}),
        ("levels", "suite/levels.py", 420, -300, {"count": 10, "height": 3.6, "roof_level": True}),
        ("grids", "suite/grids.py", 420, 300, {"x_spacing": "4x8.4", "y_spacing": "3x8.4"}),
        ("frame", "suite/beams_columns.py", 840, -500, {"material": "steel", "top_offset": 0.25}),
        ("slab_type", "suite/slab_types.py", 840, 200, {"preset": "post-tensioned 250"}),
        ("slabs", "suite/slabs.py", 1260, 100, {"which": "all", "offset": 0.45}),
        ("curtain", "suite/curtain_wall.py", 1260, -600, {"offset": 0.7, "panel": 1.4, "profile": "box", "width": 60, "depth": 180}),
        ("preview", "Custom Preview", 1700, -100, None),
        ("info", "Panel", 1700, 200, None),
    ], ["site.site>levels.site", "site.site>grids.site", "site.site>frame.site", "site.site>slabs.site",
        "grids.outline>levels.outline", "grids.grids>frame.grids", "levels.levels>frame.levels",
        "grids.outline>slabs.outline", "levels.levels>slabs.levels", "slab_type.slab_type>slabs.slab_type",
        "grids.outline>curtain.path", "levels.levels>curtain.levels",
        "frame.solids>preview.Geometry", "frame.colours>preview.Material",
        "slabs.solids>preview.Geometry", "slabs.colours>preview.Material",
        "curtain.solids>preview.Geometry", "curtain.colours>preview.Material",
        "curtain.info>info"])
    size_panel(ids["info"], 420, 160)
    hide([ids[k] for k in ("frame", "slabs", "curtain", "slab_type")])
    note("Framed tower", "Ten storeys on a 4 x 3 bay grid (8.4 m): a steel frame at every grid intersection, post-tensioned slabs\n"
         "0.45 m past the grid, and a curtain wall 0.7 m out with transoms at each slab edge. Change the storeys on\n"
         "levels or the bays on grids and everything follows.", -60, -900)
    return finish("08_framed_tower", {"view": "iso", "focus": [ids["preview"]]})


# The agent's building tool, off the UI thread (it calls MCP tools itself); the result goes to a file.
BUILD = r'''
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

DEMOS = {"site": site, "object3d": object3d, "entourage": entourage, "frame": frame, "facade": facade,
         "texture": texture, "axo": axo, "tower": tower}


def check_saved():
    """Open every saved demo from disk, solve it and report."""
    ok = True
    for f in sorted(os.listdir(OUT)):
        if not f.endswith(".gh"):
            continue
        tool("doc", {"action": "open_gh", "path": os.path.join(OUT, f)})
        time.sleep(2)
        bad, c = problems()
        print("  %-26s %3d nodes  %s" % (f, len(c["nodes"]), "ok" if not bad else "PROBLEMS: " + "; ".join(bad)))
        ok = ok and not bad
    return ok


def main():
    global ROOT, OUT
    r = jt("repo")
    ROOT = r.get("root") or r.get("repository")
    OUT = os.path.join(ROOT, "demos")
    os.makedirs(OUT, exist_ok=True)
    args = sys.argv[1:]
    if args == ["--check"]:
        sys.exit(0 if check_saved() else 1)
    names = args or list(DEMOS)
    results = {}
    for n in names:
        print(n)
        t0 = time.time()
        try:
            results[n] = DEMOS[n]()
        except Exception as ex:
            print("  FAILED:", ex)
            results[n] = False
        print("  (%.0f s)" % (time.time() - t0))
    print("\n" + ", ".join("%s %s" % (k, "ok" if v else "FAILED") for k, v in results.items()))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
