"""Write docs/docs.json: Alien's full reference as one JSON file (for people, agents and the coming website).

  python3 tools/docs_json.py            # Rhino open: MCP tools are read live from the server
  python3 tools/docs_json.py --check    # also fail if a suite node, setting or MCP tool is undocumented

Node pins come from the suite scripts' headers, categories from src/Library.cs, MCP tools from the running server
(else the previous docs.json). Everything else is written below: keep it in step with the code.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "docs.json")
MCP = "http://127.0.0.1:9876/mcp"


# ── header parsing (a port of src/Scripting/HeaderParser.cs, enough for documentation) ──

def split_tokens(s):
    out, cur, quote, depth = [], "", False, 0
    for ch in s:
        if ch == '"':
            quote = not quote
        elif not quote and ch in "[<":
            depth += 1
        elif not quote and ch in "]>":
            depth = max(0, depth - 1)
        if ch == "," and not quote and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [t.strip() for t in out if t.strip()]


def parse_token(tok):
    head, _, meta = tok.partition("|")
    m = re.match(r"\s*([A-Za-z_]\w*)\s*(?::\s*([^\s]+))?\s*(.*)$", head)
    name, typ, rest = m.group(1), (m.group(2) or "object"), m.group(3)
    meta = (rest + " " + meta).strip()
    access = "item"
    w = re.match(r"(list|tree)\[(.+)\]$", typ, re.I)
    if w:
        access, typ = w.group(1).lower(), w.group(2)
    elif typ.endswith("[]"):
        access, typ = "list", typ[:-2]
    desc = ""
    dm = re.match(r'\s*"([^"]*)"(.*)$', meta)
    if dm:
        desc, meta = dm.group(1), dm.group(2)
    p = {"name": name, "type": typ, "access": access}
    if desc:
        p["description"] = desc
    for k, v in re.findall(r'\b(min|max|step|decimals|default|options|suggest)\s*=\s*("[^"]*"|\[[^\]]*\]|[^\s,]+)', meta):
        v = v.strip('"')
        if k in ("min", "max", "step"):
            p[k] = float(v) if "." in v or "e" in v.lower() else int(v)
        elif k == "decimals":
            p[k] = int(v)
        elif k == "options":
            p[k] = v.split("|")
        elif k == "suggest":
            p["suggest"] = v
        elif typ == "bool":
            p[k] = {"true": True, "false": False}.get(v.lower(), v)
        elif typ in ("float", "int") and re.fullmatch(r"-?\d+(\.\d+)?", v):
            p[k] = float(v) if "." in v else int(v)
        else:
            p[k] = v
    bare = re.sub(r'"[^"]*"', "", meta)
    if typ == "bool" and re.search(r"(^|\s)button(\s|$)", bare):
        p["control"] = "button"
    if typ == "str" and re.search(r"(^|\s)image(\s|$)", bare):
        p["control"] = "image"
    return p


def parse_header(path):
    inputs, outputs, category, description = [], [], None, None
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")[:60]
    for line in lines:
        m = re.match(r"\s*#\s*NODE_(INPUTS|OUTPUTS)\s*:\s*(.*)$", line)
        if m:
            (inputs if m.group(1) == "INPUTS" else outputs).extend(parse_token(t) for t in split_tokens(m.group(2)))
            continue
        c = re.match(r"\s*#\s*CATEGORY\s*:\s*(.+)$", line)
        if c:
            category = c.group(1).strip()
            continue
        if line.startswith("#") and description is None and (inputs or outputs):
            body = line.lstrip("#").strip()
            if body:
                description = body
        elif not line.startswith("#") and line.strip():
            break
    return {"inputs": inputs, "outputs": outputs, "category": category, "description": description}


def categories():
    src = open(os.path.join(ROOT, "src", "Library.cs"), encoding="utf-8").read()
    return [{"key": k, "label": label, "hint": hint}
            for k, label, hint in re.findall(r'new Category\("([^"]+)", "([^"]+)", "([^"]+)"', src)]


def mcp(method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    req = urllib.request.Request(MCP, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["result"]


def mcp_section(previous):
    try:
        init = mcp("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "docs_json", "version": "1"}})
        tools = mcp("tools/list")["tools"]
    except Exception as ex:
        if previous.get("mcp", {}).get("tools"):
            print("MCP server not reachable (%s): keeping the previous tool list" % ex, file=sys.stderr)
            return previous["mcp"]["tools"], previous["mcp"].get("server_instructions"), previous["mcp"].get("read_from")
        raise SystemExit("MCP server not reachable and no previous docs.json: open Rhino + Grasshopper with Alien loaded")
    out = []
    for t in tools:
        schema = t.get("inputSchema") or {}
        required = set(schema.get("required") or [])
        params = []
        for name, p in (schema.get("properties") or {}).items():
            q = {"name": name, "type": p.get("type") or ("one of: " + ", ".join(x.get("type", "?") for x in p.get("anyOf", [])) if p.get("anyOf") else "any")}
            if p.get("description"):
                q["description"] = p["description"]
            if name in required:
                q["required"] = True
            params.append(q)
        out.append({"name": t["name"], "description": t["description"], "parameters": params})
    return out, init.get("instructions"), "live server, %s" % dt.date.today().isoformat()


# ── written content ─────────────────────────────────────────────────

SUITE = {
    "vic_site_map.py": {
        "summary": "A Victorian address (or lat,lon) becomes a site model: the lot, neighbouring lots, road centrelines, "
                   "terrain with contours, 3D streets, trees, building massing standing on the ground, and the planning "
                   "zone, overlays and bushfire status.",
        "behaviour": [
            "Geocodes with Vicmap's address index (Nominatim as fallback; a match on only the street or suburb is refused).",
            "Lot from Vicmap Property; neighbouring lots from Vicmap Parcel; roads from Vicmap Transport.",
            "Buildings from the Overture Maps buildings archive (PMTiles, read by HTTP range), OpenStreetMap Overpass as fallback. "
            "Height: Overture height, else storeys x 3 m + 1 m, else the tallest building part, else assumed_height.",
            "Terrain: Geoscience Australia 5 m LiDAR DEM where it covers the site, gaps filled from the statewide Vicmap Elevation "
            "10 m DEM; surface models (Terrarium, Open-Meteo) only if both fail. A 2 m grid, at most 201 x 201 points.",
            "Streets: each centreline gets a cross-section from its road class, fitted to the road reserve measured from Vicmap "
            "road casement polygons (property line to property line). Carriageway, median, nature strip, footpath; lanes "
            "(reserve <= 7.5 m) wall to wall with no kerb; verges paved in central/commercial zones (CCZ, CDZ, ACZ, C1Z, MUZ, DZ, PDZ). "
            "Bands and reserves are overlaid into planar regions, classified, meshed and draped on the ground; verges sit a "
            "150 mm kerb above the road, with kerb faces between.",
            "Trees: Vicmap Vegetation Tree Urban points with LiDAR height and canopy half-widths, modelled by shape: broad "
            "(clumps of lumpy low-poly blobs on a split trunk with limbs), columnar, eucalypt (open high crown), conifer "
            "(stacked cones) or palm (trunk and fronds), from the species where known (City of Melbourne) else from "
            "proportions, seeded per tree. Trees under 1.5 m get a height from their canopy; points inside a building "
            "footprint reading roof height are left out.",
            "Geometry is in metres scaled to the model's units, centred on the site, Y = true north. z = 0 is the ground at "
            "the site centre (datum gives its AHD height). Curves stay flat at z = 0 for modelling; ground, buildings, streets "
            "and trees follow the terrain.",
            "Fetched data is cached for 30 days in the temp folder (alien-cache/vic_site_map), each v2 layer on its own; "
            "refresh (a button) fetches again once.",
            "Data is fetched for a context bucket (60, 120, 200 or 300 m) and trimmed to the radius, so sliding the radius "
            "within a bucket refetches nothing.",
            "The address field suggests Victorian addresses while typing (Vicmap address index, about 0.1 s per lookup).",
            "If Vicmap's ArcGIS property/parcel layers fail (as after a republish on 12 Oct 2026), the same layers are read "
            "from the open-data WFS and info says so.",
        ],
        "data_sources": ["Vicmap Address, Property, Parcel, Transport, Planning, Elevation, Vegetation (State of Victoria, CC BY 4.0)",
                         "DEM of Australia from LiDAR 5 m (Geoscience Australia, CC BY 4.0)",
                         "Overture Maps buildings and OpenStreetMap (ODbL)", "City of Melbourne urban forest (CC BY)"],
        "cost": "Free (open data, no key).",
        "speed": "First fetch for an address 2-10 s (about 35 s seen in the CBD); about 1 s from the cache; a 240 m radius with "
                 "streets about 6 s from the cache.",
        "checked": ["201 Napier St Fitzroy: 4,710 m2 lot, NRZ1, HO180 + DCPO1; terrain 28.7-39.1 m AHD; 65 street centrelines (58 "
                    "fitted to measured reserves); 300+ trees", "Monbulk: about 35 m fall, 79 contours, streets draped on the slope",
                    "120 Collins St: CCZ1, paved verges, a grassed median, 51 trees with species for 27"],
        "limits": ["Not a Planning Property Report: confirm with VicPlan.",
                   "Microsoft ML building heights ran about 2.3 m low in inner Melbourne.",
                   "Tree data covers metro Melbourne, Ballarat, Shepparton, Wangaratta and Sale only."],
    },
    "site_views.py": {
        "summary": "Sets up the standard drawing views around a site: plan, site plan, X and Y centreline sections and four "
                   "elevations, as Rhino named views (sections clipped) and an optional layout sheet.",
        "behaviour": [
            "Frame: true north, or the long axis of the site's minimum rectangle (align).",
            "Views: 'Alien · Plan', 'Alien · Site plan', 'Alien · Section X' (looking north), 'Alien · Section Y' (looking west), "
            "'Alien · Elevation north/south/east/west' clipped 0.5 m outside the site so the site's own buildings show.",
            "Clipping planes live on a hidden 'Alien views' layer (hidden planes still clip).",
            "Sheet (A3/A2/A1): plans on top, sections and elevations in rows, one standard scale for all (the site plan gets "
            "its own), Pen display mode, labels on 'Alien sheet labels'.",
            "run (a button) replaces everything the last run made: tagged objects, 'Alien · ' named views and pages.",
            "Without run, the outputs still give the view names, camera planes, frames and section lines.",
        ],
        "cost": "Free.",
        "checked": ["Fitzroy site on A1: plan 1:500, site plan 1:1000, sections and elevations 1:500; each named view restores "
                    "with its clipping plane."],
    },
    "object_3d.py": {
        "summary": "Describe a thing (or give a picture), press Generate, and a model writes RhinoCommon code that builds it from "
                   "solids at real size, with part names and materials. Width, depth and height resize the built design.",
        "behaviour": [
            "Only the generate button calls the model. Changing thing, style, image, detail or variant waits for it ('Press "
            "Generate to build ...'); nothing is sent until then.",
            "A design built before comes back free from the cache (<repo>/.alien/cache/objects), with or without the button. "
            "The default request ('a dining chair') ships with its design.",
            "The model (setting model3d, default anthropic/claude-sonnet-5.5) writes def build(rg, math) returning "
            "(name, material, brep) tuples in metres, Z up, standing on z = 0, front facing -Y, from robust primitives "
            "(no booleans or fillets). Alien runs it with only rg and math, checks the parts and that the size is plausible "
            "(3 cm to 8 m), and sends any error back: up to 3 attempts.",
            "Width, depth and height never call the model: each axis scales the built design on its own; 0 or unset means as "
            "built. After a build the sliders show the design's size and span half to twice it (NODE_CONTROLS); sizes set for "
            "one design reset when the design changes.",
            "Each design is saved once to the asset library at its built size (geometry.3dm, build.py, thumbnail, the reference "
            "image); resizing never makes a new asset.",
        ],
        "cost": "US$0.01-0.02 per design (Sonnet 5.5); free from the cache.",
        "checked": ["Dining chair 18 parts 0.44 x 0.42 x 0.90 m US$0.017; dining table for six 15 parts US$0.011; toilet with "
                    "cistern 14 parts US$0.015; lounge armchair from a product photo 17 parts US$0.019; bar stool US$0.013; "
                    "wall basin US$0.020. All first attempt.",
                    "Resizing, re-pressing Generate on a built design and switching between built designs made no model calls "
                    "and no assets."],
        "security": "The generated code runs with a restricted builtins dict and only rg and math: not a sandbox (Python "
                    "introspection can escape one). It comes from the user's own model call and is shown in the code output.",
    },
    "material_texture.py": {
        "summary": "A material finish becomes a seamless tileable texture: a Custom Preview material, a 3 x 3 tile preview at "
                   "real size, and (add) a Rhino PBR material mapped at real-world size.",
        "behaviour": [
            "Only the generate button calls the image model: a new finish or variant waits for it; a texture painted before "
            "comes back free. The texture is keyed by finish and variant, never by size.",
            "size is the real-world size of one tile: moving it re-tiles the same texture for free; Generate again repaints "
            "at the new scale.",
            "The image model paints a flat, evenly lit swatch about size metres across.",
            "Seam check: if the wrap-around edges jump more than 1.35 x the texture's own detail, the image is rolled half a "
            "tile and cross-faded over the seam cross; already-tiling patterns are left as drawn (blending them ghosts).",
            "add (a button) creates or replaces a PBR render material: base colour + bump, WCS box mapping, one tile per size "
            "metres, so it needs no UV mapping.",
            "Textures are cached in <repo>/.alien/cache/textures; each painted texture is saved once to the asset library "
            "(its seamless version).",
        ],
        "cost": "About US$0.034 per texture; free from the cache.",
        "checked": ["Board-formed concrete, 1.2 m tile: both textures in WCS box mapping at the right repeat."],
        "limits": ["Display textures don't show in captures of a backgrounded Rhino: check them by eye."],
    },
    "bim_site.py": {
        "summary": "The building suite's base: a site record (name, project base point, grid rotation, true north, AHD datum, "
                   "drawing scale, boundary) that levels, grids and every building node read through their site input.",
        "behaviour": ["Takes vic_site_map's site (several lots are joined) and datum.",
                      "Origin: a wired point, else the boundary's lower-left corner in project axes (anchor corner), its "
                      "centroid (centre) or the world origin.",
                      "align takes the grid direction from the lot's long axis or longest edge; rotation turns it further.",
                      "Annotation across the suite is sized in paper mm at the site's drawing scale (1:50 ... 1:1000).",
                      "Outputs the project plane, a north point (with a PN tick when the grid is turned) and a base-point marker."],
        "cost": "Free.",
        "checked": ["2026-10-13: every anchor x align combination on an irregular lot (longest edge 94.1 deg checked by hand); "
                    "a real vic_site_map lot, touching lots (joined), disjoint lots, open polylines and circles."],
    },
    "levels.py": {
        "summary": "Named building levels (basements, storeys, roof) from a ground level above the site origin, with level "
                   "records, level lines, datum-symbol heads and plan labels for drawings.",
        "behaviour": ["count, height and per-storey heights ('4.5, 3, , 3.6'; a blank keeps the default); basements below "
                      "the ground level; an optional Roof level.",
                      "Naming: Level 1 | L1 | L01 | Level 01 | LVL 1 | custom ('Floor #', '##F'), a ground name and a start number.",
                      "RL = site datum + elevation ('RL 23.460' with an AHD datum, else 'RL +3.200').",
                      "Level lines run along project X and/or Y past the plates by margin; heads at either or both ends.",
                      "The old outputs (planes, elevations, floor_heights, names, plates, roof, total) stay one per storey; "
                      "levels, lines, heads, annotations, labels and rl include the roof."],
        "cost": "Free.",
        "checked": ["2026-10-13: every naming and ground option, counts 1-100, junk heights, basements, the chain bim_site -> levels "
                    "(origin z, rotation, datum, scale)."],
    },
    "grids.py": {
        "summary": "Revit/ArchiCAD-style grids on the site's origin and rotation: grid records, lines, bubble heads, "
                   "intersections named 'A/1' and dimension strings.",
        "behaviour": ["Bays as '7.2, 7.2, 8.4' or '3x7.2' (values over 100 read as mm); numbers along X and letters along Y "
                      "by default (swap with labels), start labels such as '4', 'C', 'X1'; I and O skipped; AA after Z.",
                      "Heads (bubbles) at both, start, end or no ends, sized in paper mm at the drawing scale.",
                      "Custom angled or curved grids with names; their crossings are found on the curve.",
                      "Bay and overall dimensions in mm on the start sides."],
        "cost": "Free.",
        "checked": ["2026-10-13: spacing formats, label options, extensions 0-20, custom arcs, rotated sites (30 and 94 deg); "
                    "intersections match alien_bim.intersections."],
    },
    "wall_types.py": {
        "summary": "A wall build-up: an Australian preset (brick veneer, double brick, timber stud, steel stud partition, "
                   "concrete 200, precast 150, blockwork 190, CLT, rendered AAC) or your own layers, as a wall_type record.",
        "behaviour": ["Layers run exterior -> interior in mm ('brick 110; cavity 40; timber stud 90*; plasterboard 10'; m and cm "
                      "work; * marks the core); a layers text replaces the preset's.",
                      "Junk tokens are reported one by one; an empty custom falls back to a generic 200 mm wall.",
                      "Outputs layer names, materials, thicknesses, the total and a stepped swatch with colours."],
        "cost": "Free.",
        "checked": ["2026-10-13: every preset; custom layers with junk, zero, negative and huge thicknesses."],
    },
    "walls.py": {
        "summary": "Layered walls along curves (lines, polylines, arcs, splines, closed outlines) from a base level to the next "
                   "(or a named level, or a height), with wall records, one solid per layer, plan linework and centrelines.",
        "behaviour": ["The exterior is on the left of an open path; closed outlines put it outside; flip swaps.",
                      "Location line: centre, exterior, interior or core centre.",
                      "Corners within a wall are mitred exactly; with join on, walls meeting end to end are mitred (L) and walls "
                      "stopping inside another are trimmed to its face (T). Crossings (X) are left overlapping.",
                      "Layers that fold over on bends too tight for the wall are dropped with a note.",
                      "Plan linework is cut at a height above the base (default 1 m)."],
        "cost": "Free.",
        "checked": ["2026-10-13: lines, arcs, splines, L and T pairs, circles, filleted and concave rooms, odd and too-tight paths, "
                    "all 4 locations x flip (valid outward-facing solids; layer positions checked numerically), wired levels."],
    },
    "doors.py": {
        "summary": "Doors hosted in wall records: single or double swing, sliding, pocket, bifold, at distances along a wall or at "
                   "points, cut through every layer, with frames, leaves and drawing symbols.",
        "behaviour": ["Positions '1.2, -1, 50%' (negative = back from the end), or points (nearest wall); wall by id, index, list or 'all'.",
                      "Openings stay clear of corners and wall ends and never overlap (skipped or moved with a note).",
                      "walls in, walls out: each record gains its openings, so walls -> doors -> windows chains, and solids are the "
                      "walls with every opening so far cut (straight walls rebuilt face by face, curved ones by Booleans).",
                      "hand is seen from the side the door opens into; swing in or out.",
                      "Drawings: plan (leaf drawn open with its swing arc), reflected (seen from below), elevation (dashed V to the hinges).",
                      "design: generated geometry (e.g. object_3d's parts) is fitted into each leaf, mirrored for right hands."],
        "cost": "Free.",
        "checked": ["2026-10-13: every family x hand x swing on straight, arc and spline walls; hand checked on 6 wall "
                    "orientations (24/24); junk, negative, % and overlapping positions; a cached object_3d design."],
    },
    "windows.py": {
        "summary": "Windows hosted in wall records: fixed, casement, awning, hopper, sliding, double hung, at a sill height, cut "
                   "through every layer, with frames, sashes, glass and drawing symbols.",
        "behaviour": ["Hosted, placed and chained like doors; a window may sit above a door.",
                      "Plan symbols are dashed when the window is above the cut; reflected view; elevation marks show the hinge "
                      "side (side-hung casement, top-hung awning, bottom-hung hopper) or arrows (sliding, double hung).",
                      "design: generated geometry is fitted into each sash."],
        "cost": "Free.",
        "checked": ["2026-10-13: every family x hand x swing on straight and curved walls, high and full-height sills, the chain "
                    "wall_types -> walls -> doors -> doors -> windows (6 windows 61 ms)."],
    },
    "beams_columns.py": {
        "summary": "A structural frame on the grids: columns at intersections (one member per storey) and beams from "
                   "intersection to intersection at each level, in Australian steel, timber or concrete sections.",
        "behaviour": ["Sections read forgivingly from alien_sections' catalogue: 193 steel sizes (UB, UC, PFC, SHS, RHS, CHS, EA, "
                      "UA), glulam, LVL, sawn and concrete ('310 UB 40.4', 'SHS 150x9', '600 dia'); steel snaps to the nearest "
                      "catalogue size with a note; junk falls back to the material's default.",
                      "Columns at all intersections, the perimeter or none, less a skip list ('B/2, C/3'); they turn with their grid.",
                      "Beams on both, lettered or numbered grids, top_offset below each level, stopping 12 mm off the face they "
                      "meet (flange, web or flange tips); where no column stands, the continuous beams are primary.",
                      "Member records carry the exact centreline, section spec and name, level, grid and kg (info sums steel tonnes)."],
        "cost": "Free.",
        "checked": ["2026-10-13: every material, columns_at, beams_on and level option; grids rotated 15 and 30 deg (identical beam "
                    "lengths); a 6x4-bay 6-storey frame (558 members) in 0.15 s."],
    },
    "structure_edit.py": {
        "summary": "Edits a frame after creation: resize, move, rotate, remove or extend members picked by id wildcards or "
                   "filters, with several edits at once; ids are kept.",
        "behaviour": ["select: 'C-A/*', 'B-L02-*', or filters level=L02 grid=A role=beam section=310UC* material=steel; * = all.",
                      "edits: one 'pattern: key=value ...' line per edit, applied in order (section=, remove, move=dx,dy,dz, "
                      "rotate=, extend=).",
                      "Resize takes a section name or up/down a size; beams keep their tops.",
                      "Beams framing into a changed member are re-trimmed to its new face; edited members preview green and "
                      "are listed in changed."],
        "cost": "Free.",
        "checked": ["2026-10-13: every action through the dropdown and edit lines, every filter, up/down sizing, junk lines; "
                    "connectors follow the edits (a column turned 90 deg swaps end plates and fin plates)."],
    },
    "connectors.py": {
        "summary": "Steel connections found from the members' geometry: base plates with anchors, cap plates, column splices, "
                   "end plates, fin plates and cleats, with bolts, optional welds and a schedule.",
        "behaviour": ["auto picks per joint: end plates on column flanges, fin plates on webs and between beams; or one type for all.",
                      "Hollow, channel and angle sections, CHS columns, timber knife plates and T-brackets on timber/concrete supports.",
                      "Bolt rows/columns fit the plate (or as given); edge distance and base-plate margin grow when bolts wouldn't fit.",
                      "joints records and a schedule line per joint ('BP A/1 GF: PL 505x508x25, 4 M24 anchors')."],
        "cost": "Free.",
        "checked": ["2026-10-13: 28 section-family x type combinations, every option, rotated grids, the chain grids/levels -> "
                    "beams_columns -> structure_edit -> connectors (196 joints)."],
    },
    "curtain_wall.py": {
        "summary": "A curtain wall or glazing system along a straight, faceted, curved or closed path, between levels (or a "
                   "height): mullions, transoms, frame, glass and optional trusses, with a plan cut and records.",
        "behaviour": ["The exterior is on the right of an open path's direction; closed outlines face out; flip swaps.",
                      "Mullions at a panel width (equal divide, fixed from start or centred); arcs and splines are faceted into "
                      "straight chords; every corner gets a mullion turned to the bisector.",
                      "Transoms at every level and/or typed heights ('1.2, 2.4', '@1.5', '-0.9' down from the top), per storey or "
                      "from the wall base.",
                      "Mullion and transom profiles box, I, T, round, fin (frameless glass with 12 mm joints) or flat bar, in mm.",
                      "Optional warren, pratt, howe or vierendeel trusses behind the mullions (depth, chord and web sections).",
                      "Member counts are capped (300 mullions, 1,500 cells, 1,500 truss members) with a note."],
        "cost": "Free.",
        "checked": ["2026-10-13: every profile pair, truss type and fit mode; line, arc, spline, circle, CW/CCW rectangles, "
                    "L-shape, sloped and doubled-back paths; offsets and flip; a 48 m 12-storey wall with trusses (956 valid solids)."],
    },
    "branch_picker.py": {
        "summary": "Passes chosen branches of a data tree through, in the order given; the rest go out of rest.",
        "behaviour": ["Pick forms: '0, 2, 4-6', '-1' (last), '*' (all), 'even', 'odd'.",
                      "Out-of-range or junk picks are ignored and reported; flatten merges the picked branches.",
                      "Works by branch index: original path labels such as {0;2} are not kept."],
        "cost": "Free.",
    },
    "scale_figure.py": {
        "summary": "An image model draws a person as a black silhouette, traced into true-height outlines, a planar fill and a "
                   "solid hatch for elevations and sections.",
        "behaviour": ["Only the generate button calls the image model: a new who, view or variant waits for it; drawings made "
                      "before come back free. Height, detail, smooth and plane only re-trace or re-place the drawing.",
                      "Tracing: marching squares with sub-pixel edges, small islands and gaps dropped by detail, Douglas-Peucker "
                      "simplification, smooth closed curves with a polyline fallback.",
                      "A blank, cut-off or non-white drawing is redrawn once with a stricter prompt, only while Generate runs; "
                      "the check uses a fixed detail so the detail slider never causes a redraw.",
                      "Stands in the XZ plane at the origin (Front view) unless a plane is wired.",
                      "Drawings are cached in <repo>/.alien/cache/graphics and saved to the asset library."],
        "cost": "About US$0.034 per drawing; free from the cache.",
        "checked": ["Walking adult exactly 1,750 mm tall; woman with pram 1.68 m with wheel holes kept."],
    },
    "scale_tree.py": {
        "summary": "An image model draws a tree in elevation or plan, traced into true-size outlines (canopy gaps kept as "
                   "holes), a planar fill and a solid hatch.",
        "behaviour": ["Only the generate button calls the image model: a new species, view or variant waits for it.",
                      "Elevation: sized by height, standing in XZ. Plan: sized by spread (0 = 0.75 x height), lying in XY.",
                      "Same tracing, checks, cache and asset saving as scale_figure; a detailed tree (183 loops) solves in 0.6 s."],
        "cost": "About US$0.034 per drawing; free from the cache.",
        "checked": ["8 m street tree in elevation with 22 canopy gaps; 7 m canopy in plan."],
    },
}

HELPERS = [
    {"file": "alien_ai.py", "what": "Alien's settings, OpenRouter chat (with images for vision) and image generation, with "
                                    "caches in <repo>/.alien/cache. Requests deny data collection and report their cost."},
    {"file": "alien_assets.py", "what": "Saves what nodes generate to <repo>/.alien/assets/<kind>/<id>/ with asset.json, "
                                        "geometry.3dm (metres, per-object names and colours) and a thumbnail (images "
                                        "fitted, or an isometric shaded render of geometry drawn with Eto)."},
    {"file": "alien_graphics.py", "what": "Prompting, image model, cache, silhouette tracing and true-size placement for "
                                          "scale_figure and scale_tree; saves drawings as 'graphic' assets."},
    {"file": "alien_vicdata.py", "what": "Victorian open data for vic_site_map: elevation_grid (GA LiDAR, Vicmap 10 m LERC "
                                         "tiles with a pure-Python decoder, fallbacks), trees (Tree Urban + City of "
                                         "Melbourne species), streets (centreline cross-sections fitted to measured reserves)."},
]

DOC = {
    "summary": "Alien is a Grasshopper (Rhino 8) plugin: a node whose logic lives in a plain Python 3 or C# script file, "
               "with pins and on-node controls from a header comment. Describe a node in plain English and a model writes "
               "the script, runs it and fixes its own errors. Nodes are shareable files; AI agents drive everything through "
               "a built-in MCP server. It ships with a suite of architectural nodes and keeps what they generate in a library.",
    "status": "Working prototype for a uni mini-MBA pitch (mock business, no real sales). Phase 1 of BRIEF.md is done, plus "
              "the panel, library categories, the generated-asset library and the suite below. The online library, "
              "accounts and payments (Phases 2-3) are not built.",
    "platforms": "Rhino 8 on macOS (tested) and Windows (builds; the sidecar window and controls are untested there).",
    "repository": {
        "url": "https://github.com/NGoochi/Alien_ID (private)",
        "layout": [
            {"path": "src/", "what": "The plugin (C#, net7.0): AlienComponent (the node), AlienAttributes (its drawing and "
                                     "mouse handling), ScriptRepository (settings, script folder), Suite, Library, Assets, AlienUi"},
            {"path": "src/Scripting/", "what": "HeaderParser, ScriptRunner (RhinoCode), TypeMap, Values, RunResult"},
            {"path": "src/Ai/", "what": "Describe-a-node: ModelClient, Generator (retry loop), Prompts, Transcript"},
            {"path": "src/Ui/", "what": "AlienPanel (pane.html in an Eto WebView), AlienSidecar, NodeControls, InlineEditor"},
            {"path": "src/Mcp/", "what": "McpServer (Streamable HTTP, JSON), Tools*.cs, Describe (run reports), UiThread"},
            {"path": "suite/", "what": "The preloaded nodes and helpers, embedded in the plugin at build time"},
            {"path": "docs/AGENT_GUIDE.md", "what": "Agent reference: served by the docs MCP tool and part of describe-a-node's prompt"},
            {"path": "docs/DEV_NOTES.md", "what": "Developer facts: build, threading, pin rebuilds, RhinoCode, UI, Rhino API facts, data"},
            {"path": "docs/docs.json", "what": "This file (tools/docs_json.py writes it)"},
            {"path": "tests/describe/", "what": "Describe-a-node test: 10 prompts, results per model"},
            {"path": "tools/", "what": "Drive a running Rhino from a shell: MCP calls, captures, clicks, the panel, these docs, "
                                       "package.sh (the release zip)"},
            {"path": "installer/", "what": "The release zip's contents besides Alien.gha: INSTALL.md, README.txt, installers, settings example"},
            {"path": "BRIEF.md", "what": "Product brief: tiers, stack, phases"},
            {"path": "AGENTS.md / CLAUDE.md", "what": "Project context for coding agents"},
        ],
    },
    "install": {
        "release": "From https://github.com/NGoochi/Alien_ID/releases, with Rhino closed: Windows, run "
                   "Alien-<version>-Setup.exe (per user, no admin; SmartScreen: More info > Run anyway). It installs Alien.gha "
                   "into %APPDATA%\\Grasshopper\\Libraries, writes settings (script folder Documents\\Alien scripts, the "
                   "OpenRouter key if pasted on its optional page), adds an uninstaller to Settings > Apps, and can start Rhino "
                   "with Grasshopper. macOS, open Alien-<version>.pkg (Gatekeeper: System Settings > Privacy & Security > Open "
                   "Anyway); it installs for the current user only and refuses while Rhino runs. Then the panel's Help (?) "
                   "links OpenRouter. A by-hand zip has Alien.gha and the old scripts. Windows needs Rhino on .NET Core "
                   "(SetDotNetRuntime). Full steps: installer/INSTALL.md.",
        "requirements": ["Rhino 8 (macOS or Windows)", ".NET SDK 7 or later (building from source only)",
                         "An OpenRouter API key for describe-a-node and the AI nodes (or Ollama for local describe models)"],
        "steps_from_source": [
            "Close Rhino completely (macOS: Cmd+Q).",
            "Run `dotnet build -c Release` in src/: it builds Alien.gha and copies it into Grasshopper's Libraries folder "
            "(Windows %APPDATA%\\Grasshopper\\Libraries; macOS ~/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/"
            "Grasshopper (b45a29b1-...)/Libraries). Rhino paths are in src/Alien.csproj (override with -p:RhinoSystemDir=... "
            "-p:RhinoGhDir=...).",
            "Start Rhino and open Grasshopper: the MCP server starts with it.",
            "Place an Alien node (tab Alien > Script). The script repository is Documents/Alien scripts unless one was "
            "chosen (right-click a node > Script repository).",
            "Open the Alien panel, click ? (Help) and follow Connect OpenRouter; or add \"openrouterKey\" to settings.json "
            "(re-read live).",
        ],
    },
    "settings": {
        "file": {"windows": "%APPDATA%\\Alien\\settings.json", "macos": "~/Library/Application Support/Alien/settings.json"},
        "notes": "Re-read when it changes (no restart) and saved owner-only. Keys never go into .gh files, scripts or the repo.",
        "keys": [
            {"key": "repository", "meaning": "The script folder", "default": None},
            {"key": "port", "meaning": "MCP server port", "default": 9876},
            {"key": "editor", "meaning": "Command for Edit script, e.g. \"code\"", "default": None},
            {"key": "openrouterKey", "meaning": "OpenRouter API key (describe-a-node, AI suite nodes)", "default": None},
            {"key": "model", "meaning": "Describe-a-node model; \"ollama:<name>\" for local", "default": "anthropic/claude-haiku-5.5"},
            {"key": "imageModel", "meaning": "Image model for drawings and textures", "default": "google/gemini-3.1-flash-lite-image"},
            {"key": "model3d", "meaning": "object_3d's model", "default": "anthropic/claude-sonnet-5.5"},
            {"key": "classifyModel", "meaning": "Model that sorts uncategorised library nodes", "default": "the describe model"},
            {"key": "classify", "meaning": "\"off\" sorts the library by keywords only", "default": None},
            {"key": "ollamaUrl", "meaning": "Local model server", "default": "http://localhost:11434"},
            {"key": "reasoning", "meaning": "Reasoning effort for describe-a-node", "default": "low (none for local models)"},
            {"key": "panel", "meaning": "\"rhino\" docks the panel in Rhino instead of beside Grasshopper", "default": None},
            {"key": "theme", "meaning": "Colours of the nodes, canvas text box and panel: {preset, background, text, accent} "
             "(\"#rrggbb\"); written by the panel's settings", "default": "the Alien preset"},
        ],
    },
    "script_repository": {
        "what": "A folder of the user's scripts, never this repo. .gh files store scripts as repo:relative/path, so nodes "
                "stay shareable as single files.",
        "layout": [
            {"path": "*.py, *.cs (any depth)", "what": "Scripts; those with a header are nodes and appear in the library"},
            {"path": "suite/", "what": "The preloaded suite, installed and kept up to date by the plugin"},
            {"path": ".alien/history/", "what": "Every earlier version of each script (name.yyyyMMdd-HHmmss-fff.ext); the panel restores them"},
            {"path": ".alien/cache/graphics, images, textures, objects", "what": "Caches of paid results, by request"},
            {"path": ".alien/assets/<kind>/<id>/", "what": "The generated-asset library"},
            {"path": ".alien/library.json", "what": "The model's category picks, keyed by a hash of each header"},
            {"path": ".alien/suite.json", "what": "Hashes of the suite files as installed (to update only unedited ones)"},
            {"path": ".alien/trash/assets/", "what": "Assets removed from the library (recoverable)"},
        ],
    },
    "node": {
        "summary": "One Alien node runs one script file. Saving the file (or a model or agent editing it) rebuilds the pins, "
                   "keeping wires by name, and re-solves.",
        "header": {
            "example": ["# NODE_INPUTS: base:Curve, count:int | \"divisions\" min=2 max=200 default=20",
                        "# NODE_OUTPUTS: points, log",
                        "# CATEGORY: Geometry",
                        "# Divides a curve into N points."],
            "rules": [
                "First 200 lines; Python uses #, C# uses //. Several NODE_INPUTS / NODE_OUTPUTS lines are allowed.",
                "Token: name:type [| \"description\"] [min= max= step= decimals= default= options=\"a|b|c\" suggest=\"p\"] "
                "[button] [image]. Commas inside quotes are fine.",
                "Access: list[T], T[], T:list -> list; tree[T] -> tree (a list of branch lists); otherwise item.",
                "default= accepts numbers, true/false, \"text\", [x,y,z].",
                "Pin names are identities: renaming one drops its wires.",
                "Outputs are generic; a type after : documents them.",
                "After the pins: an optional # CATEGORY: line (a library category label), then one comment line saying what "
                "the node does (its library listing).",
                "Python packages: # r: numpy (first 32 lines). C# references: // r \"full/path.dll\" or // r \"nuget: Name, 1.2.3\".",
            ],
            "types": "float int bool str Color Interval Point3d Vector3d Plane Line Circle Arc Box Rectangle3d Curve Surface "
                     "Brep Mesh SubD Geometry Transform object (aliases: double/number, string/text, point, vector, domain, colour)",
        },
        "values": [
            "An unwired input gets, in order: its manual value (from the node's control or MCP set_values), the header default, "
            "else None / [] (C#: default(T)).",
            "An input set to its header default holds no manual value.",
            "Outputs are top-level variables with the output names; nested lists become data trees; tuples, sets, generators "
            "and numpy arrays become lists; Polylines become PolylineCurves.",
            "Item-access inputs fed with lists run the script once per item.",
        ],
        "controls": [
            {"kind": "slider", "for": "float / int", "behaviour": "Range from the header's min/max, else recommended from the "
             "default; a fast script (< 80 ms) follows the drag live, a slow one solves on release; double-click to type."},
            {"kind": "switch", "for": "bool", "behaviour": "Toggles true/false."},
            {"kind": "button", "for": "bool with the word button", "behaviour": "Momentary: true for one solve when clicked, "
             "shows 'working…' until it has run; never stored, saved or undone. For model calls and other slow, paid or "
             "side-effecting steps. set_values {name: true} presses it."},
            {"kind": "dropdown", "for": "str with options=", "behaviour": "Picks one of the options."},
            {"kind": "image field", "for": "str with the word image", "behaviour": "Takes an image file or link dropped on the "
             "node (its path or URL) and shows a thumbnail; typing a path or URL works too."},
            {"kind": "suggestions", "for": "str with suggest=\"vic-address\"", "behaviour": "A list drawn under the text box "
             "follows the typing (Vicmap addresses, about 0.1 s a lookup, from the street number on); arrow keys + Enter or "
             "a click pick one."},
            {"kind": "text field", "for": "str, and lists of str/float/int typed as 'a, b, c'", "behaviour": "Edited in place; "
             "the box grows with the text (up to 460 px). Enter, clicking anywhere else (another field too) or scrolling "
             "the canvas commits; Escape cancels."},
        ],
        "runtime_controls": {
            "what": "A Python script can set NODE_CONTROLS to retune its controls after a run.",
            "example": "NODE_CONTROLS = {\"width\": {\"min\": 0.35, \"max\": 1.45, \"value\": 0.72, \"decimals\": 2, \"key\": \"<design id>\"}}",
            "fields": {"min/max/step/decimals": "the slider's range", "value": "what the control shows while the input has "
                       "no manual value (e.g. the built size for '0 = as built')", "key": "what the values belong to: when a run "
                       "reports a different key, values set under the old one are cleared and the node solves again"},
        },
        "appearance": ["Colours from the theme (panel settings): background, text and accent, the rest mixed from them.",
                       "Header with the Alien mark (in the accent), the script name, a PY / C# badge and a state light; a status row (ok / error, run time) "
                       "with a PANEL button; the error itself in a strip at the bottom; a glow when selected.",
                       "Input dots: green = wired, blue = a value set on the node or by an agent, grey = the header default."],
        "menu": [
            {"section": "Script", "items": ["Edit script", "New Python script…", "New C# script…", "Open script…", "Reveal in folder", "Reload"]},
            {"section": "Describe", "items": ["Open the Alien panel", "Stop describing (while running)"]},
            {"section": "Inputs", "items": ["Show controls on the node", "Reset inputs to their defaults"]},
            {"section": "Share", "items": ["Copy node id (for agents)"]},
            {"section": "Settings", "items": ["Script repository…", "Open settings file (model, API key)"]},
        ],
        "double_click": "Selects the node and opens the panel (on a slider: type its value).",
        "while_describing": "The node's controls are locked (dimmed) while describe-a-node writes its script, because the "
                            "retry loop judges the next solve.",
        "saved_with_the_gh": ["the script reference", "the header snapshot (so opening a file doesn't rebuild pins)",
                              "manual values and the NODE_CONTROLS key each was set under", "the describe transcript (last 50)",
                              "whether controls show"],
    },
    "panel": {
        "hosts": ["Default: a window glued to the side of the Grasshopper editor that moves with it (macOS child window; "
                  "Windows owned window, untested).", "Setting panel: \"rhino\" (or the dock button) makes it a Rhino panel."],
        "header": ["The Alien mark (in the accent)", "Model picker for describe-a-node", "Settings (⚙)", "Dock / undock", "Close"],
        "help_view": ["? in the header; opens by itself the first time when no key is linked; a banner in the main view "
                      "links to it while no key is set",
                      "Connect OpenRouter, step by step: create an account, add credit, create an API key (with a credit limit), "
                      "paste it and Link key; buttons open the OpenRouter pages in the browser (only openrouter.ai and the "
                      "Alien repo are allowed)",
                      "Link key checks the key with OpenRouter (GET /api/v1/key and /api/v1/credits), saves it only if "
                      "accepted (or if OpenRouter can't be reached), and shows the credit left, the key's own limit and its "
                      "expiry; Check credit and Remove for a linked key; the page only ever sees 'sk-or-v1-…' + 4 characters",
                      "Getting started, What costs money, Troubleshooting, Open settings file"],
        "settings_view": ["Theme presets: Alien, Cursor Dark, One Dark, Dracula, Monokai, Nord, Tokyo Night, Solarized Dark, "
                          "High Contrast, Light, GitHub Light, Solarized Light",
                          "Background, text and accent colour pickers (with hex fields); changing one makes a custom theme",
                          "Reset to Alien; Open settings file (key, models, script folder, port)"],
        "node_section": ["Follows the selected Alien node: name, language, status, description, error",
                         "Describe box: a new node (none selected) or a change to the selected one; Enter sends, Shift+Enter "
                         "for a new line; Stop while running",
                         "Several nodes selected (or one native component, or 'Build a new node from this one' ticked): the "
                         "selection is listed, and describing builds a new node beside it that sees what each selected node "
                         "outputs and is wired to them",
                         "Requests: every describe request with its outcome and notes",
                         "Inputs (current values), Code (with Open in editor / Show file), Earlier versions (Restore)"],
        "library_tab": ["Every script with a header, grouped by category, with category chips and counts and a search",
                        "suite tag on preloaded nodes; hover shows the path and how the category was decided",
                        "Click a node to place it on the canvas; '+ New blank node'"],
        "generated_tab": ["Thumbnails of everything the suite generated, with kind chips (Objects, Materials, Drawings) and search",
                          "A card for the picked asset: size, materials, date, prompt, and actions"],
    },
    "library": {
        "sorting": ["1. A # CATEGORY: line in the script header (matched by label or key, any case).",
                    "2. The model's pick, cached in .alien/library.json under a hash of the header (editing the header re-sorts).",
                    "3. A keyword guess (name x3, description x2, rest of header x1) until the model has answered.",
                    "Uncategorised nodes go to the model in the background, up to 40 per request, at most one request at a time "
                    "and no sooner than 10 minutes after the last; only header comments are sent, never code.",
                    "Describe-a-node writes the CATEGORY line itself (the prompt lists each category's hint)."],
    },
    "assets": {
        "folder": "<repo>/.alien/assets/<kind>/<id>/",
        "kinds": [{"kind": "object", "from": "object_3d", "files": ["geometry.3dm", "build.py", "reference.<ext> (when an image was given)"]},
                  {"kind": "texture", "from": "material_texture", "files": ["texture.png"]},
                  {"kind": "graphic", "from": "scale_figure, scale_tree", "files": ["drawing.jpg|png", "geometry.3dm (outlines and hatches)"]}],
        "asset_json": {"id": "sha1 of (kind, prompt, params), 12 characters: regenerating the same thing updates it",
                       "kind": "object | texture | graphic", "name": "what was asked for", "prompt": "the full request",
                       "params": "what else made it (variant, model, image hash, ...)", "node": "the suite node that made it",
                       "inputs": "the node inputs that made it, for Place node", "files": "role -> file name",
                       "created": "yyyy-MM-dd HH:mm", "size": "objects: as built; textures: size_m"},
        "actions": [
            {"action": "Bake block", "for": "object, graphic", "does": "Defines a block per asset ('Alien · name · id6', scaled "
             "from the 3dm's metres to the model's units) and inserts one at the origin, selected, on the 'Alien assets' layer."},
            {"action": "Add material", "for": "texture", "does": "A PBR render material (base colour + bump, WCS box, real tile "
             "size), applied to the selected objects."},
            {"action": "Place node", "for": "assets with inputs", "does": "Places the node that made it with the same inputs "
             "(free: the result is cached)."},
            {"action": "Show files", "for": "all", "does": "Reveals the asset folder."},
            {"action": "Remove", "for": "all", "does": "Click twice: moves the folder to .alien/trash/assets (recoverable)."},
        ],
    },
    "describe_a_node": {
        "how": ["The request, the current script, earlier requests for the node and summaries of the wired inputs go to the "
                "model, which returns the whole file.",
                "Alien writes it (the old version goes to .alien/history), re-solves and judges the run report: an error, unset "
                "outputs, no outputs or every output empty goes back to the model. Up to 4 attempts.",
                "Progress shows on the node and in the panel; every request is kept in the node's transcript.",
                "From a selection: each selected node's id, header (or component name), output summaries and one building "
                "record in full go with the request; the model answers with a 'WIRES: input <- id.output; ...' line and "
                "Alien wires the new node before its first judged run. MCP generate takes the same as context: [ids]."],
        "models": "Default anthropic/claude-haiku-5.5 through OpenRouter (providers that don't store or train on prompts); any "
                  "OpenRouter model or a local Ollama model works.",
        "tested": ["2026-10-09: Haiku 5.5 10/10 working, 10/10 first try; GPT-6 Luna 10/10 (9 first try); DeepSeek V4.1 "
                   "Flash 10/10 (7 first try).",
                   "2026-10-12 (with CATEGORY lines): Haiku 10/10 first try, US$0.0011 per node, 9.6 s average."],
    },
    "data_privacy_security": [
        "Describe-a-node sends the request, the current script and summaries of wired inputs to the chosen model.",
        "The AI suite nodes send only their prompt (object_3d also the reference image given).",
        "vic_site_map sends the address to Vicmap (Nominatim as fallback); the terrain, tree and street requests send only the map box.",
        "Library sorting sends uncategorised scripts' header comments, not their code.",
        "The MCP server listens on loopback only and refuses non-loopback Origins; any local process can still call it, "
        "including run_code. Don't expose the port.",
        "Scripts run with the user's rights and can read settings.json (including the key). Read shared scripts before "
        "running them; the planned public library needs review before install and model calls routed through the plugin.",
    ],
    "development": {
        "build": ["Rhino fully closed (pgrep Rhinoceros empty), then dotnet build -c Release in src/ (builds and deploys).",
                  "While Rhino is open: dotnet build -c Release -p:GH_Libraries= compiles without deploying."],
        "verify": ["Launch: open -a \"Rhino 8\" --args -nosplash -runscript=\"_-Grasshopper _Window _Show _Enter\", then poll "
                   "http://127.0.0.1:9876/health.",
                   "Drive it with tools/ and look at the result (capture.py, screencapture); don't claim a UI change works "
                   "without seeing it.",
                   "Describe-a-node prompt or model changes: rerun tests/describe/suite.py.",
                   "Quit only a Rhino you opened: osascript -e 'tell application \"Rhino 8\" to quit saving no'."],
        "tools": [
            {"tool": "tools/mcp.py <tool> '<json>'", "what": "Call one MCP tool"},
            {"tool": "tools/capture.py out.png '<json>'", "what": "Save a viewport or canvas capture"},
            {"tool": "tools/click.py <nickname> <x> <y> [double]", "what": "Simulate a click on a node (tests its mouse handling)"},
            {"tool": "tools/panel.py '<json>' | --js '<script>' | --state out.json", "what": "Send the panel a message as its page would, run JavaScript in it, or save its state"},
            {"tool": "tools/pane_snapshot.swift <state.json> <out.png> [js]", "what": "Render the panel page off screen (WebKit) with a saved state"},
            {"tool": "tools/package.sh", "what": "Build Setup.exe (NSIS), the .pkg, the by-hand zip and checksums into dist/"},
            {"tool": "tools/windows.swift", "what": "List window ids for screencapture -l"},
            {"tool": "tools/docs_json.py [--check]", "what": "Write (and check) docs/docs.json"},
            {"tool": "tests/describe/suite.py <dir> <models...>", "what": "Run the describe-a-node test"},
        ],
        "conventions": ["Comments explain why; plain names; C# 9 / net7.0.", "MCP output stays compact JSON.",
                        "Header ranges and defaults are UI: give them in every node.",
                        "Suite nodes speak metres, scale to the model's units and show sample geometry when unwired.",
                        "Paid, slow or side-effecting steps go behind a button input; slider and toggle values never go into "
                        "a model prompt or cache key.",
                        "Keep README, AGENTS.md, DEV_NOTES, AGENT_GUIDE and this file in step with behaviour."],
    },
    "roadmap": ["Phase 2: a Cloudflare Worker, R2 and D1 for a shared library of nodes and generated assets (publish and "
                "install from the panel, review before install), a hosted model provider, a gallery site.",
                "Phase 3: Google sign-in, credits and Stripe (test mode), private nodes for Pro.",
                "Test on Windows: the build, the sidecar, the controls.",
                "vic_site_map: optional draped site curves, road markings, trees outside Tree Urban coverage.",
                "Dynamic inputs (pins that change with the result), left for later at the user's request."],
}


def build():
    previous = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                previous = json.load(f)
        except ValueError:
            pass
    nodes = []
    for name in sorted(os.listdir(os.path.join(ROOT, "suite"))):
        if not name.endswith(".py") or name.startswith("alien_"):
            continue
        h = parse_header(os.path.join(ROOT, "suite", name))
        if not (h["inputs"] or h["outputs"]):
            continue
        node = {"file": "suite/" + name, "name": name[:-3], "category": h["category"], "listing": h["description"]}
        node.update(SUITE.get(name, {}))
        node["inputs"], node["outputs"] = h["inputs"], h["outputs"]
        nodes.append(node)
    tools, instructions, read_from = mcp_section(previous)
    info = open(os.path.join(ROOT, "src", "AlienInfo.cs"), encoding="utf-8").read()
    version = re.search(r'Version => "([^"]+)"', info).group(1)
    doc = {"name": "Alien", "version": version, "generated": dt.date.today().isoformat(),
           "generator": "tools/docs_json.py (pins from the suite headers, categories from src/Library.cs, MCP tools from the server)"}
    doc.update(DOC)
    doc["library"] = dict(DOC["library"], categories=categories())
    doc["suite"] = {
        "install": "Embedded in Alien.gha. When the panel opens with a script repository set, suite files never installed "
                   "are added to <repo>/suite and unedited ones updated (hashes in .alien/suite.json); edited or deleted "
                   "files are left alone.",
        "conventions": ["Sizes in metres, scaled to the model's units.", "Sample geometry when unwired.",
                        "Helpers must sit in the same folder as the nodes that import them.",
                        "Paid steps cache their results and sit behind a button where repeating them would cost."],
        "nodes": nodes, "helpers": HELPERS,
    }
    doc["mcp"] = {
        "endpoint": "http://127.0.0.1:9876/mcp (port from settings)", "transport": "Streamable HTTP, JSON responses",
        "health": "GET http://127.0.0.1:9876/health",
        "connect": {"claude_code": "claude mcp add --transport http alien http://127.0.0.1:9876/mcp",
                    "cursor_and_others": {"mcpServers": {"alien": {"url": "http://127.0.0.1:9876/mcp"}}}},
        "notes": ["Ids are the 8-character ids from canvas/objects; a unique nickname also works for nodes.",
                  "Tool failures come back as isError results with a message, so agents can correct themselves.",
                  "Every tool that changes something returns the new state: don't poll."],
        "server_instructions": instructions, "read_from": read_from, "tools": tools,
    }
    return doc


def check(doc):
    problems = []
    for n in doc["suite"]["nodes"]:
        for key in ("summary", "behaviour", "category", "listing"):
            if not n.get(key):
                problems.append("suite node %s has no %s" % (n["name"], key))
    labels = {c["label"] for c in doc["library"]["categories"]}
    for n in doc["suite"]["nodes"]:
        if n.get("category") and n["category"] not in labels:
            problems.append("suite node %s: unknown category %r" % (n["name"], n["category"]))
    src = ""
    for d, _, files in os.walk(os.path.join(ROOT, "src")):
        if "/obj" in d or "/bin" in d:
            continue
        src += "".join(open(os.path.join(d, f), encoding="utf-8").read() for f in files if f.endswith(".cs"))
    suite = "".join(open(os.path.join(ROOT, "suite", f), encoding="utf-8").read() for f in os.listdir(os.path.join(ROOT, "suite")) if f.endswith(".py"))
    used = set(re.findall(r'Setting(?:Token)?\("([A-Za-z0-9]+)"', src)) | set(re.findall(r'Settings\.Value<[^>]+>\("([A-Za-z0-9]+)"', src)) \
        | set(re.findall(r'setting\("([A-Za-z0-9]+)"\)', suite))
    documented = {k["key"] for k in doc["settings"]["keys"]}
    problems += ["setting %r is read by the code but not documented" % k for k in sorted(used - documented)]
    problems += ["setting %r is documented but never read" % k for k in sorted(documented - used)]
    if not doc["mcp"]["tools"]:
        problems.append("no MCP tools")
    return problems


if __name__ == "__main__":
    doc = build()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("wrote %s: %d suite nodes, %d MCP tools, %d categories" % (
        os.path.relpath(OUT, ROOT), len(doc["suite"]["nodes"]), len(doc["mcp"]["tools"]), len(doc["library"]["categories"])))
    if "--check" in sys.argv:
        problems = check(doc)
        for p in problems:
            print("PROBLEM:", p)
        sys.exit(1 if problems else 0)
