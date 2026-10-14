# NODE_INPUTS: grids:list[object] | "grid records (from grids); sample grids when unwired", levels:list[object] | "level records (from levels); sample levels when unwired"
# NODE_INPUTS: column:str | "column section: 310UC96.8, SHS 150x9, CHS 168.3x7.1, GL18 360x360, concrete 450x450 (blank: the material's default)" default=""
# NODE_INPUTS: beam:str | "beam section: 410UB53.7, 250PFC, RHS 200x100x6, LVL 360x63, concrete 600x400 (blank: the material's default)" default=""
# NODE_INPUTS: material:str | "frame material: picks the default sections and reads bare sizes like 400x400" default="steel" options="steel|timber|concrete"
# NODE_INPUTS: columns_at:str | "where columns go" default="all intersections" options="all intersections|perimeter|none", skip:str | "intersections left without a column, e.g. 'B/2, C/3'" default=""
# NODE_INPUTS: beams_on:str | "grid lines that get beams" default="both" options="both|letters|numbers|none"
# NODE_INPUTS: from_level:str | "lowest level: name, code or index (blank: the lowest)" default="", to_level:str | "top level (blank: the highest)" default=""
# NODE_INPUTS: rotation:float | "column rotation (degrees) on top of the grid's angle" min=-180 max=180 default=0, top_offset:float | "beam tops below each level (m), e.g. the slab thickness" min=0 max=1.5 default=0.15
# NODE_INPUTS: site:object | "site record (optional): unwired sample grids sit on its origin and rotation"
# NODE_OUTPUTS: members:list[str] | "member records: wire into structure_edit or connectors", solids:list[Brep] | "every member, in member order"
# NODE_OUTPUTS: columns:list[Brep], beams:list[Brep], axes:list[Curve] | "member centrelines", names:list[str] | "member ids, e.g. C-A/1-GF, B-L01-A-1/2"
# NODE_OUTPUTS: sections:list[str] | "section name per member", colours:list[Color] | "preview colour per member (wire with solids into Custom Preview)", info:str
# CATEGORY: Building elements
# Beams and columns: a structural frame on the grids, columns at intersections storey by storey and beams between intersections at each level, from Australian steel, timber or concrete sections.
#
# Columns stand at grid intersections (all, the perimeter, or none, less the skip list), one member per storey so they
# can be spliced; the top storey stops flush with the top beams. Each column turns with the grid it stands on (its
# depth along the numbered grid) plus `rotation`. Beams run between neighbouring intersections along the chosen grid
# lines, tops `top_offset` below each level above the base, ends 12 mm short of the column face they meet (flange, web
# or flange tips, by the column's orientation). Where no column stands, the direction with more beams is primary (ties:
# the lettered grids) and the others stop at its flange tips. Section names are read forgivingly (alien_sections).
import time

import alien_bim as ab
import alien_sections as S

t_start = time.time()
u = ab.units()
notes = []

mat = material if material in ("steel", "timber", "concrete") else "steel"
where = columns_at if columns_at in ("all intersections", "perimeter", "none") else "all intersections"
along = beams_on if beams_on in ("both", "letters", "numbers", "none") else "both"
offset = max(0.0, float(top_offset)) if top_offset is not None else 0.15
for given, used, label in ((material, mat, "material"), (columns_at, where, "columns_at"), (beams_on, along, "beams_on")):
    if given and given != used:
        notes.append("%s '%s' unknown: used %s" % (label, given, used))

# grids and levels: wired records, else samples (the grids placed on the site, if one is wired)
grid_recs = ab.grids_of(grids, sample=False)
if not grid_recs:
    grid_recs = ab.sample_grids()
    s = ab.site_of(site)
    plane = ab.site_plane(s, 1.0)
    for g in grid_recs:
        for k in ("start", "end"):
            p = plane.PointAt(g[k][0], g[k][1])
            g[k] = [p.X, p.Y, p.Z]
    notes.append("sample grids" + (" on the site" if ab.first(site, "site") else ""))
level_recs = ab.levels_of(levels, sample=False)
if not level_recs:
    level_recs = ab.sample_levels()
    notes.append("sample levels")

col_spec, note = S.lookup(column, mat, "column")
if note:
    notes.append("column: " + note)
beam_spec, note = S.lookup(beam, mat, "beam")
if note:
    notes.append("beam: " + note)


def pick(ref, label):
    if ref is None or not str(ref).strip():
        return None
    lv = ab.level_at(level_recs, str(ref).strip())
    if lv is None:
        notes.append("%s level '%s' not found: used the %s" % (label, ref, "lowest" if label == "from" else "highest"))
    return lv


lo, hi = pick(from_level, "from"), pick(to_level, "to")
recs, more = S.build_frame(grid_recs, level_recs, col_spec, beam_spec, where, skip, along, lo, hi,
                           float(rotation or 0.0), offset)
notes += more

members, solids, columns, beams, axes, names, sections, colours = [], [], [], [], [], [], [], []
kept = []
for r in recs:
    b = S.solid(r, u)
    if b is None:
        notes.append("%s has no length: left out" % r["id"])
        continue
    kept.append(r)
    members.append(ab.dump(r))
    solids.append(b)
    (columns if r["role"] == "column" else beams).append(b)
    axes.append(S.axis_curve(r, u))
    names.append(r["id"])
    sections.append(r["section_name"])
    colours.append(S.colour(r))

storeys = sorted({r["level"] for r in kept if r["role"] == "column"})
beam_levels = sorted({r["level"] for r in kept if r["role"] == "beam"})
used = [s for s in (col_spec["name"] if any(r["role"] == "column" for r in kept) else None,
                    beam_spec["name"] if any(r["role"] == "beam" for r in kept) else None) if s]
info = "%s; %s; columns on %d storey%s, beams at %d level%s; %d ms" % (
    S.totals(kept), " / ".join(used) or "no members", len(storeys), "" if len(storeys) == 1 else "s",
    len(beam_levels), "" if len(beam_levels) == 1 else "s", (time.time() - t_start) * 1000)
if notes:
    info += "\n" + "\n".join(notes)
