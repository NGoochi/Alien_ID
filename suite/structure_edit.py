# NODE_INPUTS: members:list[object] | "member records (from beams_columns or another structure_edit); a sample frame when unwired"
# NODE_INPUTS: select:str | "members to edit: ids with * wildcards (C-A/*, B-L02-*) and/or filters level=L02 grid=A role=beam section=310UC* material=steel; blank = none, * = all" default=""
# NODE_INPUTS: action:str | "what to do to the selection" default="resize" options="resize|move|rotate|remove|extend"
# NODE_INPUTS: section:str | "resize to: a section name (460UB67.1, SHS 200x9, GL18 450x135), a profile record from section_profile, or up / down a size" default="", offset:str | "move by: dx, dy, dz (m)" default="0, 0, 0"
# NODE_INPUTS: rotation:float | "rotate by (degrees) about each member's own axis" min=-180 max=180 default=0, extend:float | "extend both ends by (m); negative shortens" min=-3 max=3 default=0
# NODE_INPUTS: edits:list[str] | "more edits, one per line, in order: 'B-L02-*: section=460UB67.1', 'C-B/2-*: remove', 'level=RF role=beam: move=0,0,0.1 rotate=5', 'C-A/1-GF: extend=0.5,0'"
# NODE_OUTPUTS: members:list[str] | "member records, ids kept (wire into connectors)", solids:list[Brep] | "every member, in member order", columns:list[Brep], beams:list[Brep]
# NODE_OUTPUTS: colours:list[Color] | "preview colour per solid: edited members green", changed:list[str] | "ids of members that changed (edited, or beams refitted to an edited support)", info:str
# CATEGORY: Building elements
# Structure edit: resize, move, rotate, remove or extend members of a frame picked by id patterns or filters, several edits at once, keeping ids so connectors still find them.
#
# Select by id with * wildcards, by filters (level, grid, role, section, material, or any record field), or both (ids
# OR-ed, filters AND-ed). The dropdown action applies first, then each `edits` line ("pattern: key=value ...", keys
# section, move, rotate, extend, remove) in order. Beams keep their tops when resized or rotated, and beams framing into
# a resized, rotated, moved or removed member are refitted to its new face (beams you moved or extended keep exactly
# what you gave them). Edited members show green; `edited` in each record lists what happened to it.
import fnmatch
import re
import time

import alien_bim as ab
import alien_sections as S

t_start = time.time()
u = ab.units()
notes, steps = [], []

recs = ab.read(members, "member")
if not recs and any(m is not None for m in (members or [])):
    notes.append("no member records on members: wire beams_columns or structure_edit")
elif not recs:  # unwired (an empty wired list looks the same to a script)
    recs = S.sample_frame()
    notes.append("sample frame")
recs = [dict(r) for r in recs]
ids = [r.get("id") for r in recs]
if len(set(ids)) < len(ids):
    notes.append("some ids repeat: a pattern edits every member with that id")

edited = {}       # id -> what happened
explicit = set()  # beams moved or extended here: keep their geometry
touched = set()   # members resized, rotated, moved or removed: refit the beams framing into them
resized = set()   # beams resized or rotated: refit themselves
removed = []

# ── selection ──

TOKEN = re.compile(r"""([A-Za-z_]+)\s*=\s*("[^"]*"|'[^']*'|[^\s,;]+)|("[^"]*"|'[^']*'|[^\s,;]+)""")


def _q(s):
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'" else s


def _glob(value, pattern):
    return fnmatch.fnmatchcase(re.sub(r"\s", "", str(value)).upper(), re.sub(r"\s", "", pattern).upper())


def _field(r, key, val):
    if key == "grid":
        names = [str(r.get("grid") or "")] + str(r.get("grid") or "").split("/")
        return any(_glob(n, val) for n in names if n)
    if key == "role":
        return _glob(r.get("role", ""), val.rstrip("sS") if val.lower().endswith("s") and val not in ("*",) else val)
    if key == "section":
        return _glob(r.get("section_name") or (r.get("section") or {}).get("name", ""), val)
    if key == "level":
        return any(_glob(x, val) for x in (r.get("level"), (r.get("level_name"))) if x)
    return _glob(r.get(key, ""), val)  # any other record field


def pick(pattern):
    """The records a pattern picks, in frame order."""
    p = str(pattern or "").strip()
    if not p:
        return []
    if p.lower() in ("*", "all"):
        return [r for r in recs if not r.get("_gone")]
    globs, filters = [], []
    for m in TOKEN.finditer(p):
        if m.group(1):
            filters.append((m.group(1).lower(), _q(m.group(2))))
        else:
            globs.append(_q(m.group(3)))
    out = []
    for r in recs:
        if r.get("_gone") or (globs and not any(_glob(r.get("id", ""), g) for g in globs)):
            continue
        if all(_field(r, k, v) for k, v in filters):
            out.append(r)
    return out


# ── edits ──

def _nums(text):
    return [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", str(text))]


def _mark(r, what):
    edited.setdefault(r["id"], []).append(what)
    r["edited"] = list(r.get("edited") or []) + [what]


def _shift_z(r, dz):
    for k in ("start", "end"):
        r[k] = [r[k][0], r[k][1], round(float(r[k][2]) + dz, 6)]


def do_resize(r, name):
    old = r.get("section") or {}
    m = re.fullmatch(r"\s*(up|down|\+|-)\s*(\d*)\s*|\s*([+-]\d+)\s*", str(name).lower())
    if m:
        n = int(m.group(3)) if m.group(3) else (int(m.group(2) or 1) * (1 if m.group(1) in ("up", "+") else -1))
        spec = S.step(old, n)
        if spec is None:
            return "%s: no size %s %s in its range" % (r["id"], "up" if n > 0 else "down", old.get("name"))
    else:
        spec, note = S.parse(name, r.get("material") or old.get("material"))
        if spec is None:
            return note
    vh0 = S.vertical_half(r)
    r["section"], r["section_name"], r["material"] = spec, spec["name"], spec.get("material", r.get("material"))
    if r.get("role") == "beam":  # keep the beam's top where it was
        _shift_z(r, vh0 - S.vertical_half(r))
        resized.add(r["id"])
    touched.add(r["id"])
    _mark(r, "section %s" % spec["name"])
    return None


def do_rotate(r, deg):
    vh0 = S.vertical_half(r)
    r["rotation"] = round(float(r.get("rotation") or 0.0) + deg, 4)
    if r.get("role") == "beam":
        _shift_z(r, vh0 - S.vertical_half(r))
        resized.add(r["id"])
    touched.add(r["id"])
    _mark(r, "rotate %+g" % deg)


def do_move(r, d):
    for k in ("start", "end"):
        r[k] = [round(float(a) + b, 6) for a, b in zip(r[k], d)]
    if r.get("nodes"):
        r["nodes"] = [[round(float(a) + b, 6) for a, b in zip(n, d)] for n in r["nodes"]]
    if r.get("role") == "beam":
        explicit.add(r["id"])
    touched.add(r["id"])
    _mark(r, "move %g,%g,%g" % tuple(d))


def do_extend(r, a, b):
    s, e = S.point(r["start"]), S.point(r["end"])
    v = e - s
    if v.Length < 1e-9 or v.Length + a + b < 0.01:
        return "%s: too short to shorten by %g m" % (r["id"], -(a + b))
    v.Unitize()
    r["start"], r["end"] = S.coords(s - v * a), S.coords(e + v * b)
    if r.get("role") == "beam":
        explicit.add(r["id"])
    touched.add(r["id"])
    _mark(r, "extend %g,%g" % (a, b))
    return None


def _short(v):
    """A profile record shows as its name in the steps."""
    if isinstance(v, str) and v.lstrip().startswith("{"):
        spec, _ = S.parse(v)
        return (spec or {}).get("name") or "a profile record"
    return v


def apply(pattern, ops, label):
    picked = pick(pattern)
    if not picked:
        notes.append("%s: nothing matches '%s'" % (label, pattern))
        return
    msgs = []
    for r in picked:
        if ops.get("remove"):
            r["_gone"] = True
            removed.append(r["id"])
            touched.add(r["id"])
            continue
        if ops.get("section"):
            m = do_resize(r, ops["section"])
            if m:
                msgs.append(m)
        if ops.get("rotate"):
            do_rotate(r, ops["rotate"])
        if ops.get("move") and any(abs(x) > 1e-9 for x in ops["move"]):
            do_move(r, ops["move"])
        if ops.get("extend") and any(abs(x) > 1e-9 for x in ops["extend"]):
            m = do_extend(r, *ops["extend"])
            if m:
                msgs.append(m)
    what = ", ".join("%s=%s" % (k, _short(v) if not isinstance(v, (list, tuple)) else ",".join("%g" % x for x in v))
                     for k, v in ops.items() if k != "remove") or "remove"
    steps.append("%s: %s -> %d member%s" % (pattern, what, len(picked), "" if len(picked) == 1 else "s"))
    for m in sorted(set(msgs))[:6]:
        notes.append(m)


ACTION = re.compile(r"([A-Za-z_]+)\s*=|(?<![\w=])(remove|delete)(?![\w=])", re.I)


def parse_ops(text, label):
    ops, found = {}, list(ACTION.finditer(text))
    if not found:
        notes.append("%s: no action in '%s' (use section=, move=, rotate=, extend=, remove)" % (label, text.strip()))
    for k, m in enumerate(found):
        key = (m.group(1) or m.group(2)).lower()
        val = text[m.end():found[k + 1].start() if k + 1 < len(found) else len(text)].strip().strip(",;").strip()
        if key in ("remove", "delete"):
            ops["remove"] = True
        elif key in ("section", "size", "resize"):
            ops["section"] = _q(val)
        elif key in ("move", "offset"):
            n = (_nums(val) + [0.0, 0.0, 0.0])[:3]
            ops["move"] = n
        elif key in ("rotate", "rotation"):
            n = _nums(val)
            if n:
                ops["rotate"] = n[0]
        elif key in ("extend", "extend_start", "extend_end"):
            n = _nums(val)
            if not n:
                continue
            a, b = ops.get("extend", (0.0, 0.0))
            if key == "extend":
                a, b = (n[0], n[0]) if len(n) == 1 else (n[0], n[1])
            elif key == "extend_start":
                a = n[0]
            else:
                b = n[0]
            ops["extend"] = (a, b)
        else:
            notes.append("%s: unknown key '%s'" % (label, key))
    return ops


# the dropdown action
if str(select or "").strip():
    act = action if action in ("resize", "move", "rotate", "remove", "extend") else "resize"
    ops = {}
    if act == "resize":
        if str(section or "").strip():
            ops["section"] = str(section).strip()
        else:
            notes.append("resize: give a section (or up / down)")
    elif act == "move":
        d = (_nums(offset) + [0.0, 0.0, 0.0])[:3]
        if any(abs(x) > 1e-9 for x in d):
            ops["move"] = d
        else:
            notes.append("move: offset is 0, 0, 0")
    elif act == "rotate":
        if rotation:
            ops["rotate"] = float(rotation)
        else:
            notes.append("rotate: rotation is 0")
    elif act == "extend":
        if extend:
            ops["extend"] = (float(extend), float(extend))
        else:
            notes.append("extend: extend is 0")
    else:
        ops["remove"] = True
    if ops:
        apply(select, ops, act)

# the edits lines
for i, line in enumerate(x for item in (edits or []) for x in str(item).splitlines()):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if ":" not in line:
        notes.append("edit '%s': write 'pattern: key=value'" % line)
        continue
    pat, rest = line.split(":", 1)
    ops = parse_ops(rest, "edit %d" % (i + 1))
    if ops:
        apply(pat.strip(), ops, "edit %d" % (i + 1))

# beams framing into what changed get refitted (not the ones moved or extended by hand)
recs = [r for r in recs if not r.get("_gone")]
refit = set()
for r in recs:
    if r.get("role") != "beam" or r["id"] in explicit or not r.get("nodes"):
        continue
    if r["id"] in resized or any(s in touched for s in (r.get("supports") or []) if s):
        refit.add(r["id"])
if refit:
    before = {id(r): (list(r["start"]), list(r["end"])) for r in recs if r["id"] in refit}
    S.fit_beams(recs, refit)
    for r in recs:
        if id(r) in before and r["id"] not in edited and before[id(r)] != (r["start"], r["end"]):
            _mark(r, "refit")
for r in recs:
    S.measure(r)

members, solids, columns, beams, colours = [], [], [], [], []
for r in recs:
    b = S.solid(r, u)
    if b is None:
        notes.append("%s has no length: left out" % r["id"])
        continue
    members.append(ab.dump(r))
    solids.append(b)
    (columns if r.get("role") == "column" else beams).append(b)
    colours.append(S.colour(r, r["id"] in edited))
changed = [r["id"] for r in recs if r["id"] in edited]

info = "%d changed, %d removed; %s; %d ms" % (len(changed), len(removed), S.totals(recs), (time.time() - t_start) * 1000)
if steps:
    info += "\n" + "\n".join(steps)
if removed:
    info += "\nremoved: " + ", ".join(removed[:20]) + (" ..." if len(removed) > 20 else "")
if notes:
    info += "\n" + "\n".join(notes)
