"""
Alien sections: a catalogue of common Australian structural sections, and the frame helpers shared by beams_columns,
structure_edit and connectors. Keep this file next to alien_bim.py.

Catalogue. Steel sizes are real OneSteel/InfraBuild (300PLUS) and Austube (DuraGal, C350L0/C450L0) designations: UB,
UC and PFC with the catalogue's d, b, tf, tw and kg/m; SHS, RHS, CHS, EA and UA with their nominal sizes, and kg/m
worked out from the AS/NZS 1163 nominal geometry (corner radii 2t, or 2.5t above 3 mm), which lands within about 1%
of the catalogue. Timber is glulam (GL17, GL18 ...), LVL and sawn (MGP10/12, F17) at any size you name, with kg/m from
a typical density; concrete is any rectangle or round at 2,500 kg/m3.

lookup(name, material, role) reads forgiving names and returns (spec, note): "310UB40.4", "310 UB 40.4", "310ub40",
"150x150x9 SHS", "SHS 150x9", "RHS 200x100x6", "CHS 168.3x7.1", "EA 75x75x6", "250PFC", "GL18 360x90",
"LVL 300x63", "MGP10 90x45", "concrete 400x400", "600 dia". Steel names snap to the nearest catalogue size (with a
note); an unknown name falls back to the material's default section for that role (with a note). A profile record
(section_profile's output, or a library profile's record.json, as JSON text or a dict) is taken as it is, custom
shapes included (from_record).

A spec is what alien_bim.section()/member() take, in metres: {"shape": I|C|box|tube|L|rect|circle, "name", "family",
"material", "grade", "d", "b", "tf", "tw", "t", "kg_m"}. d is the depth (the section's local Y: up for a beam,
along the "numbers" grid for an unrotated column), b the width.

Frames. Member records (alien_bim "member") are metres in world coordinates. frame() gives a member's section axes
exactly as alien_bim.member() builds the solid (x = up x axis, y = the depth direction, turned by `rotation`);
face() says where a member arriving along a direction meets another member's surface (flange, web, flange tips, side);
build_frame() and fit_beams() make the columns and beams of a grid and trim beam ends to their supports.
"""
import json
import math
import re

import Rhino.Geometry as rg

import alien_bim as ab

STEEL = 7850.0  # kg/m3
GAP = 0.012     # m: a beam end stops this far from its support's face (room for a 12 mm end plate)
CATCH = 0.5     # m: a column whose outline comes this close to a beam's node supports that end

# ── catalogue ────────────────────────────────────────────────

# Universal beams and columns, 300PLUS: designation: (d, b, tf, tw) mm; kg/m is in the designation
UB = {
    "610UB125": (612, 229, 19.6, 11.9), "610UB113": (607, 228, 17.3, 11.2), "610UB101": (602, 228, 14.8, 10.6),
    "530UB92.4": (533, 209, 15.6, 10.2), "530UB82.0": (528, 209, 13.2, 9.6),
    "460UB82.1": (460, 191, 16.0, 9.9), "460UB74.6": (457, 190, 14.5, 9.1), "460UB67.1": (454, 190, 12.7, 8.5),
    "410UB59.7": (406, 178, 12.8, 7.8), "410UB53.7": (403, 178, 10.9, 7.6),
    "360UB56.7": (359, 172, 13.0, 8.0), "360UB50.7": (356, 171, 11.5, 7.3), "360UB44.7": (352, 171, 9.7, 6.9),
    "310UB46.2": (307, 166, 11.8, 6.7), "310UB40.4": (304, 165, 10.2, 6.1), "310UB32.0": (298, 149, 8.0, 5.5),
    "250UB37.3": (256, 146, 10.9, 6.4), "250UB31.4": (252, 146, 8.6, 6.1), "250UB25.7": (248, 124, 8.0, 5.0),
    "200UB29.8": (207, 134, 9.6, 6.3), "200UB25.4": (203, 133, 7.8, 5.8), "200UB22.3": (202, 133, 7.0, 5.0),
    "200UB18.2": (198, 99, 7.0, 4.5),
    "180UB22.2": (179, 90, 10.0, 6.0), "180UB18.1": (175, 90, 8.0, 5.0), "180UB16.1": (173, 90, 7.0, 4.5),
    "150UB18.0": (155, 75, 9.5, 6.0), "150UB14.0": (150, 75, 7.0, 5.0),
}
UC = {
    "310UC158": (327, 311, 25.0, 15.7), "310UC137": (321, 309, 21.7, 13.8), "310UC118": (315, 307, 18.7, 11.9),
    "310UC96.8": (308, 305, 15.4, 9.9),
    "250UC89.5": (260, 256, 17.3, 10.5), "250UC72.9": (254, 254, 14.2, 8.6),
    "200UC59.5": (210, 205, 14.2, 9.3), "200UC52.2": (206, 204, 12.5, 8.0), "200UC46.2": (203, 203, 11.0, 7.3),
    "150UC37.2": (162, 154, 11.5, 8.1), "150UC30.0": (158, 153, 9.4, 6.6), "150UC23.4": (152, 152, 6.8, 6.1),
    "100UC14.8": (97, 99, 7.0, 5.0),
}
# Parallel flange channels: (d, b, tf, tw, kg/m)
PFC = {
    "380PFC": (380, 100, 17.5, 10.0, 55.2), "300PFC": (300, 90, 16.0, 8.0, 40.1), "250PFC": (250, 90, 15.0, 8.0, 35.5),
    "230PFC": (230, 75, 12.0, 6.5, 25.1), "200PFC": (200, 75, 12.0, 6.0, 22.9), "180PFC": (180, 75, 11.0, 6.0, 20.9),
    "150PFC": (150, 75, 9.5, 6.0, 17.7), "125PFC": (125, 65, 7.5, 4.7, 11.9), "100PFC": (100, 50, 6.7, 4.2, 8.33),
    "75PFC": (75, 40, 6.1, 3.8, 5.92),
}
# Hollow sections (DuraGal C450L0 up to 150, C350L0 above): SHS (b, t), RHS (d, b, t), CHS (D, t), mm
SHS = [(50, 2.5), (50, 3.0), (50, 4.0), (65, 3.0), (65, 4.0), (75, 3.0), (75, 4.0), (75, 5.0), (75, 6.0),
       (89, 3.5), (89, 5.0), (89, 6.0), (100, 3.0), (100, 4.0), (100, 5.0), (100, 6.0), (100, 9.0),
       (125, 4.0), (125, 5.0), (125, 6.0), (125, 9.0), (150, 5.0), (150, 6.0), (150, 9.0), (150, 10.0),
       (200, 5.0), (200, 6.0), (200, 9.0), (200, 10.0), (200, 16.0), (250, 6.0), (250, 9.0), (250, 10.0),
       (250, 16.0), (300, 10.0), (300, 16.0)]
RHS = [(50, 25, 2.5), (50, 25, 3.0), (65, 35, 3.0), (75, 25, 2.5), (75, 50, 2.5), (75, 50, 3.0), (75, 50, 4.0),
       (100, 50, 2.5), (100, 50, 3.0), (100, 50, 4.0), (100, 50, 5.0), (100, 50, 6.0), (125, 75, 3.0),
       (125, 75, 4.0), (125, 75, 5.0), (125, 75, 6.0), (150, 50, 3.0), (150, 50, 4.0), (150, 50, 5.0),
       (150, 100, 4.0), (150, 100, 5.0), (150, 100, 6.0), (150, 100, 9.0), (200, 100, 4.0), (200, 100, 5.0),
       (200, 100, 6.0), (200, 100, 9.0), (250, 150, 5.0), (250, 150, 6.0), (250, 150, 9.0), (250, 150, 16.0),
       (300, 200, 10.0), (400, 200, 12.5), (400, 200, 16.0)]
CHS = [(26.9, 2.6), (33.7, 3.2), (42.4, 3.2), (48.3, 3.2), (48.3, 4.0), (60.3, 3.6), (60.3, 4.5), (76.1, 3.6),
       (76.1, 4.5), (88.9, 4.0), (88.9, 5.0), (101.6, 4.0), (101.6, 5.0), (114.3, 4.5), (114.3, 5.4),
       (139.7, 5.0), (139.7, 5.4), (165.1, 5.0), (165.1, 5.4), (168.3, 7.1), (219.1, 6.4), (219.1, 8.2),
       (273.1, 9.3), (323.9, 9.5), (323.9, 12.7), (406.4, 9.5), (406.4, 12.7), (457.0, 9.5), (508.0, 9.5),
       (610.0, 9.5)]
# Angles, 300PLUS: equal (b, t) and unequal (long leg, short leg, t), mm
EA = [(25, 3), (30, 3), (40, 3), (40, 5), (45, 3), (50, 3), (50, 5), (50, 6), (55, 5), (65, 5), (65, 6), (75, 5),
      (75, 6), (75, 8), (90, 6), (90, 8), (90, 10), (100, 6), (100, 8), (100, 10), (100, 12), (125, 8), (125, 10),
      (125, 12), (150, 10), (150, 12), (150, 16), (200, 13), (200, 16), (200, 18), (200, 20), (200, 26)]
UA = [(75, 50, 5), (75, 50, 6), (100, 75, 6), (100, 75, 8), (125, 75, 6), (125, 75, 8), (150, 90, 8),
      (150, 90, 10), (150, 100, 10), (150, 100, 12)]

# Timber: grade -> (family, density kg/m3); standard sizes (d, b) mm for stepping up and down a size
TIMBER = {"GL10": ("glulam", 470), "GL12": ("glulam", 480), "GL13": ("glulam", 500), "GL17": ("glulam", 560),
          "GL18": ("glulam", 600), "LVL": ("LVL", 620), "LVL11": ("LVL", 620), "LVL13": ("LVL", 620),
          "LVL15": ("LVL", 650), "MGP10": ("sawn", 520), "MGP12": ("sawn", 550), "MGP15": ("sawn", 580),
          "F7": ("sawn", 550), "F17": ("sawn", 900), "F27": ("sawn", 1000)}
GLULAM = sorted([(d, b) for b in (65, 85, 135, 185) for d in (130, 180, 225, 270, 315, 360, 405, 450, 495, 540, 585, 630)
                 if d >= b] + [(s, s) for s in (225, 270, 315, 360, 405, 450)], key=lambda x: x[0] * x[1])
LVL_SIZES = sorted([(d, b) for b in (35, 45, 63, 75, 90) for d in (95, 130, 150, 170, 200, 240, 300, 360, 400, 450, 600)],
                   key=lambda x: x[0] * x[1])
SAWN = sorted([(70, 35), (70, 45), (90, 35), (90, 45), (120, 35), (120, 45), (140, 35), (140, 45), (190, 35),
               (190, 45), (240, 35), (240, 45), (290, 45), (90, 90), (100, 100), (125, 125), (150, 150)],
              key=lambda x: x[0] * x[1])
CONCRETE = 2500.0

DEFAULTS = {
    "steel": {"column": "310UC96.8", "beam": "410UB53.7"},
    "timber": {"column": "GL18 360x360", "beam": "GL18 585x185"},
    "concrete": {"column": "Concrete 450x450", "beam": "Concrete 600x400"},
}


def _hollow_area(d, b, t):
    """Cross-section area (mm2) of a cold-formed RHS/SHS with AS/NZS 1163 corner radii."""
    ro = 2.0 * t if t <= 3.0 else 2.5 * t
    ri = ro - t
    return 2.0 * t * (b + d - 2.0 * t) - (4.0 - math.pi) * (ro * ro - ri * ri)


def _kg(area_mm2, density=STEEL):
    return round(area_mm2 * 1e-6 * density, 2)


def _fmt(x):
    return ("%.1f" % x).rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-6 else "%d" % round(x)


def _steel_specs():
    out = {}
    for fam, table in (("UB", UB), ("UC", UC)):
        for name, (d, b, tf, tw) in table.items():
            kg = float(re.search(r"(?:UB|UC)([\d.]+)", name).group(1))
            out[name] = {"shape": "I", "family": fam, "d": d, "b": b, "tf": tf, "tw": tw, "kg_m": kg, "grade": "300PLUS"}
    for name, (d, b, tf, tw, kg) in PFC.items():
        out[name] = {"shape": "C", "family": "PFC", "d": d, "b": b, "tf": tf, "tw": tw, "kg_m": kg, "grade": "300PLUS"}
    for b, t in SHS:
        out["%dx%dx%.1f SHS" % (b, b, t)] = {"shape": "box", "family": "SHS", "d": b, "b": b, "t": t,
                                             "kg_m": _kg(_hollow_area(b, b, t)), "grade": "C450L0" if b <= 150 else "C350L0"}
    for d, b, t in RHS:
        out["%dx%dx%.1f RHS" % (d, b, t)] = {"shape": "box", "family": "RHS", "d": d, "b": b, "t": t,
                                             "kg_m": _kg(_hollow_area(d, b, t)), "grade": "C450L0" if d <= 150 else "C350L0"}
    for D, t in CHS:
        out["%sx%.1f CHS" % (_fmt(D), t)] = {"shape": "tube", "family": "CHS", "d": D, "b": D, "t": t,
                                            "kg_m": _kg(math.pi * (D - t) * t), "grade": "C350L0"}
    for b, t in EA:
        out["%dx%dx%d EA" % (b, b, t)] = {"shape": "L", "family": "EA", "d": b, "b": b, "t": t,
                                          "kg_m": _kg(t * (2 * b - t)), "grade": "300PLUS"}
    for d, b, t in UA:
        out["%dx%dx%d UA" % (d, b, t)] = {"shape": "L", "family": "UA", "d": d, "b": b, "t": t,
                                          "kg_m": _kg(t * (d + b - t)), "grade": "300PLUS"}
    return out


CATALOGUE = _steel_specs()  # name -> sizes in mm


def _spec_from_mm(name, mm, material="steel"):
    """A catalogue row (mm) -> spec (metres)."""
    s = {"shape": mm["shape"], "name": name, "family": mm["family"], "material": material, "grade": mm.get("grade", "")}
    for k in ("d", "b", "tf", "tw", "t"):
        if mm.get(k):
            s[k] = round(mm[k] / 1000.0, 6)
    s["kg_m"] = mm["kg_m"]
    return s


def families():
    return ["UB", "UC", "PFC", "SHS", "RHS", "CHS", "EA", "UA", "glulam", "LVL", "sawn", "concrete"]


def catalogue(family=None):
    """Catalogue names (steel), optionally of one family, lightest first."""
    names = [n for n, m in CATALOGUE.items() if family is None or m["family"].lower() == str(family).lower()]
    return sorted(names, key=lambda n: (CATALOGUE[n]["family"], CATALOGUE[n]["kg_m"]))


# ── name reading ─────────────────────────────────────────────

def _norm(name):
    s = str(name if name is not None else "").strip().upper()
    s = s.replace("×", "X").replace("*", "X").replace("Ø", " DIA ").replace("⌀", " DIA ")
    s = re.sub(r"(?<=\d)\s*X\s*(?=\d)", "x", s)  # "150 X 150" -> "150x150"
    return re.sub(r"\s+", " ", s)


def _nums(s):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]


def _nearest(family, want, keyf):
    """Nearest catalogue name in a family to `want` (a tuple), by relative size difference."""
    best = None
    for n, m in CATALOGUE.items():
        if m["family"] != family:
            continue
        have = keyf(m)
        if len(have) != len(want):
            continue
        err = sum(abs(a - b) / max(b, 1.0) for a, b in zip(have, want))
        if best is None or err < best[0]:
            best = (err, n)
    return best[1] if best else None


def _steel(name, note_name):
    mm = CATALOGUE[name]
    spec = _spec_from_mm(name, mm)
    return spec, ("" if note_name is None else "%s is not a catalogue size: used %s" % (note_name, name))


def _timber(grade, d, b, raw):
    fam, rho = TIMBER.get(grade, ("glulam", 600))
    d, b = max(d, b), min(d, b)
    name = "%s %sx%s" % (grade, _fmt(d), _fmt(b))
    spec = {"shape": "rect", "name": name, "family": fam, "material": "timber", "grade": grade,
            "d": d / 1000.0, "b": b / 1000.0, "kg_m": _kg(d * b, rho)}
    return spec, ""


def _concrete(d, b=None, round_=False):
    if round_:
        name = "Concrete %s dia" % _fmt(d)
        return {"shape": "circle", "name": name, "family": "concrete", "material": "concrete", "grade": "N40",
                "d": d / 1000.0, "b": d / 1000.0, "kg_m": _kg(math.pi * d * d / 4.0, CONCRETE)}, ""
    b = b or d
    d, b = max(d, b), min(d, b)
    name = "Concrete %sx%s" % (_fmt(d), _fmt(b))
    return {"shape": "rect", "name": name, "family": "concrete", "material": "concrete", "grade": "N40",
            "d": d / 1000.0, "b": b / 1000.0, "kg_m": _kg(d * b, CONCRETE)}, ""


def parse(name, material=None):
    """Read a section name -> (spec, note), or (None, why) when it can't be read. material ("steel", "timber",
    "concrete") says how to read a bare size like "400x400". A profile record (JSON text or dict) is read as it is."""
    if isinstance(name, dict) or str(name if name is not None else "").lstrip().startswith("{"):
        rec = _as_record(name)
        return from_record(rec) if rec is not None else (None, "unreadable profile record (expected JSON text)")
    raw = str(name if name is not None else "").strip()
    s = _norm(raw)
    if not s:
        return None, "no section given"
    hint = str(material or "").lower()

    # exact catalogue name (any spacing or case)
    key = re.sub(r"\s", "", s)
    for n in CATALOGUE:
        if re.sub(r"\s", "", n.upper()) == key:
            return _steel(n, None)

    # universal beams and columns: 310UB40.4, 310 UB 40.4, UB310 40.4, 310UC97
    m = re.search(r"(\d{2,3})\s*(UB|UC)\s*(\d+(?:\.\d+)?)?", s) or re.search(r"(UB|UC)\s*(\d{2,3})\s*x?\s*(\d+(?:\.\d+)?)?", s)
    if m:
        g = m.groups()
        fam, depth, mass = (g[1], g[0], g[2]) if g[0][0].isdigit() else (g[0], g[1], g[2])
        group = [n for n in CATALOGUE if CATALOGUE[n]["family"] == fam and n.startswith(depth + fam)]
        if not group:  # nearest depth designation
            depths = sorted({int(re.match(r"\d+", n).group()) for n in CATALOGUE if CATALOGUE[n]["family"] == fam})
            dd = min(depths, key=lambda x: abs(x - int(depth)))
            group = [n for n in CATALOGUE if CATALOGUE[n]["family"] == fam and n.startswith("%d%s" % (dd, fam))]
        if mass:
            best = min(group, key=lambda n: abs(CATALOGUE[n]["kg_m"] - float(mass)))
            exact = abs(CATALOGUE[best]["kg_m"] - float(mass)) < 0.6 and best.startswith(depth + fam)  # 310UB40 = 310UB40.4
        else:
            best = min(group, key=lambda n: CATALOGUE[n]["kg_m"])
            exact = False
        return _steel(best, None if exact else raw)

    m = re.search(r"(\d{2,3})\s*PFC|PFC\s*(\d{2,3})", s)
    if m:
        depth = float(m.group(1) or m.group(2))
        best = min(PFC, key=lambda n: abs(PFC[n][0] - depth))
        return _steel(best, None if abs(PFC[best][0] - depth) < 0.5 else raw)

    fam = re.search(r"(?<![A-Z])(SHS|RHS|CHS|EA|UA)(?![A-Z])", s)
    metres = ""
    # sizes only: grades (C350L0, 300PLUS, N40, GL18, LVL13, MGP10, F17) are not dimensions
    nums = _nums(re.sub(r"(?<![A-Z])(C\d{3}L0|\d{3}PLUS|N\d{2}|GL\d{1,2}|LVL\d{0,2}|MGP\d{2}|F\d{1,2})(?![A-Z0-9])", " ", s))
    if nums and not fam and max(nums) < 5:  # "concrete 0.4x0.4": metres, not millimetres
        nums = [x * 1000.0 for x in nums]
        metres = "read %s as metres" % raw
    if fam:
        f = fam.group(1)
        if not nums:
            return None, "%s needs a size, e.g. %s" % (f, {"SHS": "SHS 150x9", "RHS": "RHS 200x100x6",
                                                           "CHS": "CHS 168.3x7.1", "EA": "EA 75x75x6", "UA": "UA 100x75x8"}[f])
        if f == "SHS":
            b, t = (nums[0], nums[-1]) if len(nums) >= 2 else (nums[0], 6.0)
            n = _nearest("SHS", (b, t), lambda m_: (m_["b"], m_["t"]))
            exact = abs(CATALOGUE[n]["b"] - b) < 0.5 and abs(CATALOGUE[n]["t"] - t) < 0.05
        elif f == "RHS":
            if len(nums) >= 3:
                d, b, t = max(nums[0], nums[1]), min(nums[0], nums[1]), nums[2]
            elif len(nums) == 2:
                d, b, t = nums[0], nums[0] / 2.0, nums[1]
            else:
                d, b, t = nums[0], nums[0] / 2.0, 5.0
            n = _nearest("RHS", (d, b, t), lambda m_: (m_["d"], m_["b"], m_["t"]))
            m_ = CATALOGUE[n]
            exact = len(nums) >= 3 and abs(m_["d"] - d) < 0.5 and abs(m_["b"] - b) < 0.5 and abs(m_["t"] - t) < 0.05
        elif f == "CHS":
            D, t = (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], None)
            if t is None:
                n = min((k for k in CATALOGUE if CATALOGUE[k]["family"] == "CHS"),
                        key=lambda k: (abs(CATALOGUE[k]["d"] - D), CATALOGUE[k]["t"]))
                exact = False
            else:
                n = _nearest("CHS", (D, t), lambda m_: (m_["d"], m_["t"]))
                exact = abs(CATALOGUE[n]["d"] - D) < 0.5 and abs(CATALOGUE[n]["t"] - t) < 0.05
        elif f == "EA":
            b, t = (nums[0], nums[-1]) if len(nums) >= 2 else (nums[0], 6.0)
            n = _nearest("EA", (b, t), lambda m_: (m_["b"], m_["t"]))
            exact = abs(CATALOGUE[n]["b"] - b) < 0.5 and abs(CATALOGUE[n]["t"] - t) < 0.05
        else:
            d, b, t = (nums + [0, 0, 0])[:3] if len(nums) >= 3 else (nums[0], nums[0] * 0.75, nums[-1] if len(nums) > 1 else 8)
            n = _nearest("UA", (max(d, b), min(d, b), t), lambda m_: (m_["d"], m_["b"], m_["t"]))
            exact = len(nums) >= 3 and abs(CATALOGUE[n]["d"] - max(d, b)) < 0.5 and abs(CATALOGUE[n]["t"] - t) < 0.05
        return _steel(n, None if exact else raw)

    # timber: GL18 360x90, LVL 300x63, LVL13 360x63, MGP10 90x45, F17 190x45, "glulam 450x135", "timber 200x200"
    tm = re.search(r"(?<![A-Z])(GL\d{1,2}|LVL\d{0,2}|MGP\d{2}|F\d{1,2}|GLULAM|HYSPAN|TIMBER|PINE|HARDWOOD|SAWN|WOOD)(?![A-Z])", s)
    if tm or (hint == "timber" and nums and not re.search(r"DIA|ROUND|CONC", s)):
        grade = tm.group(1) if tm else ""
        grade = {"GLULAM": "GL18", "HYSPAN": "LVL", "TIMBER": "", "WOOD": "", "PINE": "MGP10", "HARDWOOD": "F17",
                 "SAWN": "MGP10"}.get(grade, grade)
        if len(nums) >= 2:
            d, b = nums[0], nums[1]
        elif len(nums) == 1:
            d = b = nums[0]
        else:
            return None, "%s needs a size, e.g. GL18 360x90" % (tm.group(1) if tm else "timber")
        if not grade:  # a sawn size if it is one, else glulam
            grade = "MGP10" if (max(d, b), min(d, b)) in SAWN and max(d, b) <= 290 else "GL18"
        if grade.startswith("LVL") and grade not in TIMBER:
            grade = "LVL"
        if min(d, b) < 19 or max(d, b) > 2500:
            return None, "a timber size is two numbers in mm (19 to 2500), e.g. GL18 360x90"
        return _timber(grade, d, b, raw)[0], metres

    # concrete: concrete 400x400, 400x600 RC, 600 dia, dia 600, 450 round, concrete 500 (square)
    is_round = bool(re.search(r"(?<![A-Z])(DIA|DIAM|DIAMETER|ROUND|CIRC|CIRCULAR)(?![A-Z])", s))
    is_conc = bool(re.search(r"(?<![A-Z])(CONCRETE|CONC|RC|PRECAST|INSITU)(?![A-Z])", s))
    if nums and (is_round or is_conc or hint == "concrete" or (hint in ("", "steel") and re.fullmatch(r"[\d.x ]+", s))):
        if any(x < 50 or x > 5000 for x in nums[:2]):
            return None, "a concrete size is in mm (50 to 5000), e.g. concrete 400x400 or 600 dia"
        if hint == "steel" and not (is_round or is_conc):
            if len(nums) >= 3:  # 150x150x9 with no family: SHS or RHS
                return parse(("%sx%sx%s SHS" if nums[0] == nums[1] else "%sx%sx%s RHS") % tuple(_fmt(x) for x in nums[:3]))
            d = nums[0]
            b = nums[1] if len(nums) > 1 else d
            spec = {"shape": "rect", "name": "%sx%s bar" % (_fmt(max(d, b)), _fmt(min(d, b))), "family": "bar",
                    "material": "steel", "grade": "300", "d": max(d, b) / 1000.0, "b": min(d, b) / 1000.0, "kg_m": _kg(d * b)}
            return spec, "'%s' is not a steel section name: modelled as a solid steel bar" % raw
        if is_round:
            return _concrete(nums[0], round_=True)[0], metres
        return _concrete(nums[0], nums[1] if len(nums) > 1 else None)[0], metres
    return None, "unknown section '%s'" % raw


# ── profile records ──────────────────────────────────────────

SPEC_KEYS = ("shape", "name", "family", "material", "grade", "d", "b", "t", "tf", "tw", "kg_m")
DENSITY = {"steel": STEEL, "aluminium": 2700.0, "timber": 600.0, "concrete": CONCRETE}


def _as_record(value):
    """A record given as a dict or as JSON text -> dict, else None."""
    if isinstance(value, dict):
        return value
    try:
        v = json.loads(str(value).strip())
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def area_mm2(spec):
    """Nominal cross-section area (mm2) of a spec as alien_bim.section() draws it (sharp corners)."""
    hb, hd = halves(spec)
    b, d = 2000.0 * hb, 2000.0 * hd
    t, tf, tw = (x * 1000.0 for x in thick(spec))
    shape = shape_of(spec)
    if shape in ROUND_SHAPES:
        return math.pi * d * d / 4.0
    if shape in TUBE_SHAPES:
        t = min(t, d / 2.0)
        return math.pi * (d - t) * t
    if shape in BOX_SHAPES:
        t = min(t, b * 0.4, d * 0.4)
        return b * d - (b - 2 * t) * (d - 2 * t)
    if shape in I_SHAPES + C_SHAPES:
        return 2 * b * tf + (d - 2 * tf) * tw
    if shape in ("t", "tee"):
        return b * tf + (d - tf) * tw
    if shape in L_SHAPES:
        return t * (b + d - t)
    return b * d


def from_record(rec):
    """A profile record (alien "profile", metres; a bare spec dict works too) -> (spec, note), or (None, why). The
    record's own sizes are used as they are: no snapping to the catalogue."""
    kind = rec.get("alien")
    if kind not in (None, "profile"):
        return None, "a %s record is not a section profile" % kind
    shape = str(rec.get("shape") or "").lower()
    if shape not in I_SHAPES + C_SHAPES + L_SHAPES + BOX_SHAPES + TUBE_SHAPES + ROUND_SHAPES + ("rect", "t", "tee"):
        return None, "profile record: unknown shape '%s'" % rec.get("shape")
    spec = {k: rec[k] for k in SPEC_KEYS if rec.get(k) not in (None, "")}
    try:
        sizes = {k: float(spec[k]) for k in ("d", "b", "t", "tf", "tw") if k in spec}
    except (TypeError, ValueError):
        return None, "profile record: sizes are not numbers"
    if not sizes or max(sizes.values()) <= 0 or min(sizes.values()) < 0:
        return None, "profile record has no sizes"
    note = ""
    if max(sizes.values()) > 5.0:  # records are metres; a 300 here was meant as mm
        sizes = {k: v / 1000.0 for k, v in sizes.items()}
        note = "profile record sizes read as mm"
    spec.update({k: round(v, 6) for k, v in sizes.items()})
    spec.setdefault("name", "Profile")
    spec.setdefault("family", "custom")
    spec.setdefault("material", "steel")
    spec.setdefault("grade", "")
    try:
        spec["kg_m"] = float(spec.get("kg_m") or 0)
    except (TypeError, ValueError):
        spec["kg_m"] = 0.0
    if spec["kg_m"] <= 0:
        spec["kg_m"] = _kg(area_mm2(spec), DENSITY.get(str(spec["material"]).lower(), STEEL))
    return spec, note


def default_name(material="steel", role="column"):
    return DEFAULTS.get(str(material or "steel").lower(), DEFAULTS["steel"]).get(role, DEFAULTS["steel"]["column"])


def lookup(name, material="steel", role="column"):
    """A section by name -> (spec, note). Blank -> the material's default for the role; unreadable -> that default and
    a note saying so."""
    mat = str(material or "steel").lower()
    if mat not in DEFAULTS:
        mat = "steel"
    if not str(name if name is not None else "").strip():
        spec, _ = parse(default_name(mat, role), mat)
        return spec, ""
    spec, note = parse(name, mat)
    if spec is None:
        fb = default_name(mat, role)
        spec, _ = parse(fb, mat)
        return spec, "%s: used %s" % (note, fb)
    return spec, note


def step(spec, n=1):
    """The section n sizes up (n > 0) or down from spec in its family (by mass), or None at the end of the range."""
    if not spec or not n:
        return spec
    fam = spec.get("family")
    if fam in ("UB", "UC", "PFC", "SHS", "RHS", "CHS", "EA", "UA"):
        names = catalogue(fam)
        try:
            i = names.index(spec.get("name"))
        except ValueError:
            i = min(range(len(names)), key=lambda k: abs(CATALOGUE[names[k]]["kg_m"] - float(spec.get("kg_m") or 0)))
        j = i + n
        return _spec_from_mm(names[j], CATALOGUE[names[j]]) if 0 <= j < len(names) else None
    d, b = round(float(spec.get("d", 0)) * 1000), round(float(spec.get("b", 0)) * 1000)
    if fam == "concrete":
        d2, b2 = d + 50 * n, (b + 50 * n if spec.get("shape") == "circle" or d == b else b)
        if min(d2, b2) < 150:
            return None
        return _concrete(d2, None, True)[0] if spec.get("shape") == "circle" else _concrete(d2, b2)[0]
    sizes = {"glulam": GLULAM, "LVL": LVL_SIZES, "sawn": SAWN}.get(fam)
    if sizes:
        same_b = [x for x in sizes if x[1] == b] or sizes
        same_b.sort(key=lambda x: x[0] * x[1])
        cur = min(range(len(same_b)), key=lambda k: abs(same_b[k][0] * same_b[k][1] - d * b))
        j = cur + n
        if 0 <= j < len(same_b):
            return _timber(spec.get("grade") or "GL18", same_b[j][0], same_b[j][1], "")[0]
    return None


# ── section geometry (metres) ────────────────────────────────

I_SHAPES = ("i", "ub", "uc", "h", "wb", "wc")
C_SHAPES = ("c", "pfc", "channel")
L_SHAPES = ("l", "angle", "ea", "ua")
BOX_SHAPES = ("box", "shs", "rhs", "hollow")
TUBE_SHAPES = ("tube", "chs", "pipe")
ROUND_SHAPES = ("circle", "round", "rod")


def shape_of(spec):
    return str((spec or {}).get("shape", "rect")).lower()


def halves(spec):
    """(half width along local x, half depth along local y), metres, as alien_bim.section() draws it."""
    spec = spec or {}
    d = float(spec.get("d") or spec.get("b") or 0.3)
    b = float(spec.get("b") or spec.get("d") or 0.15)
    if shape_of(spec) in TUBE_SHAPES + ROUND_SHAPES:
        return d / 2.0, d / 2.0
    return b / 2.0, d / 2.0


def thick(spec):
    """(t, tf, tw), metres, with alien_bim.section()'s defaults for missing sizes."""
    spec = spec or {}
    hb, hd = halves(spec)
    b, d = 2 * hb, 2 * hd
    t = float(spec.get("t") or 0) or max(min(b, d) * 0.06, 0.002)
    tf = float(spec.get("tf") or 0) or max(d * 0.05, 0.004)
    tw = float(spec.get("tw") or 0) or max(b * 0.05, 0.003)
    return t, tf, tw


def radius(spec):
    hb, hd = halves(spec)
    return hd if shape_of(spec) in TUBE_SHAPES + ROUND_SHAPES else math.hypot(hb, hd)


def point(a):
    a = list(a or [0, 0, 0]) + [0.0] * 3
    return rg.Point3d(float(a[0]), float(a[1]), float(a[2]))


def coords(p):
    return [round(p.X, 6), round(p.Y, 6), round(p.Z, 6)]


_FRAMES = {}


def frame(rec):
    """(start, end, x, y, z) of a member in metres: z along it, y the section depth, x = up x z, turned by rotation,
    matching alien_bim.member(). Cached by geometry (plain arithmetic: RhinoCommon calls are slow from Python)."""
    a, b = list(rec.get("start") or [0, 0, 0]) + [0.0] * 3, list(rec.get("end") or [0, 0, 1]) + [0.0] * 3
    rot = float(rec.get("rotation") or 0.0)
    key = (float(a[0]), float(a[1]), float(a[2]), float(b[0]), float(b[1]), float(b[2]), rot)
    hit_ = _FRAMES.get(key)
    if hit_ is None:
        zx, zy, zz = key[3] - key[0], key[4] - key[1], key[5] - key[2]
        L = math.sqrt(zx * zx + zy * zy + zz * zz)
        zx, zy, zz = (zx / L, zy / L, zz / L) if L > 1e-12 else (0.0, 0.0, 1.0)
        ux, uy, uz = (0.0, 1.0, 0.0) if abs(zz) > 0.999 else (0.0, 0.0, 1.0)
        xx, xy, xz = uy * zz - uz * zy, uz * zx - ux * zz, ux * zy - uy * zx  # up x z
        n = math.sqrt(xx * xx + xy * xy + xz * xz)
        xx, xy, xz = xx / n, xy / n, xz / n
        yx, yy, yz = zy * xz - zz * xy, zz * xx - zx * xz, zx * xy - zy * xx  # z x x
        c, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        hit_ = (rg.Point3d(key[0], key[1], key[2]), rg.Point3d(key[3], key[4], key[5]),
                rg.Vector3d(xx * c + yx * sn, xy * c + yy * sn, xz * c + yz * sn),
                rg.Vector3d(-xx * sn + yx * c, -xy * sn + yy * c, -xz * sn + yz * c), rg.Vector3d(zx, zy, zz))
        if len(_FRAMES) > 20000:
            _FRAMES.clear()
        _FRAMES[key] = hit_
    return hit_  # shared: callers must not change these in place (Unitize, Transform)


def extent_along(rec, direction):
    """Half the member's section size measured along a direction (metres)."""
    _, _, x, y, _ = frame(rec)
    hb, hd = halves(rec.get("section"))
    if shape_of(rec.get("section")) in TUBE_SHAPES + ROUND_SHAPES:
        return hd
    return abs(direction * x) * hb + abs(direction * y) * hd


def axis_point(rec, p):
    """Closest point to p on a member's (infinite) axis line, metres."""
    s, e, _, _, z = frame(rec)
    return s + z * ((p - s) * z)


def face(sup, v, inc=None, at=None):
    """Where a member arriving along v (unit vector pointing at the support) meets member `sup`. Returns a dict (metres):
    n (the face's normal, pointing back at the arriving member), dist (support axis to the face along n), weld (axis to
    the surface a fin plate welds to: the web behind flange tips), name ("flange", "web", "flange tips", "side",
    "oblique"), wall (plate thickness behind the face for bolts, None for solid or open faces), width (usable face
    width across the support), clear (clear depth between flanges, None when unlimited), origin (the face point near
    `at`, or near the support's start), axis (the support's axis point there), ys (the support's depth direction).
    None when v runs along the support."""
    s, e, x, y, z = frame(sup)
    vp = v - z * (v * z)
    if vp.Length < 1e-6:
        return None
    vp.Unitize()
    spec = sup.get("section") or {}
    shape = shape_of(spec)
    hb, hd = halves(spec)
    t, tf, tw = thick(spec)
    base = axis_point(sup, at) if at is not None else s
    a, b = vp * x, vp * y
    solid = shape not in I_SHAPES + C_SHAPES + BOX_SHAPES + TUBE_SHAPES + L_SHAPES + ("t", "tee")
    out = {"clear": None}
    if shape in TUBE_SHAPES + ROUND_SHAPES:
        out.update(n=vp * -1.0, dist=hd, weld=hd, name="side", wall=None if solid else t, width=2 * hd * 0.7)
    elif abs(b) >= 0.985:  # onto the depth side: an I's or channel's flange, a box's side
        out.update(n=y * (-1.0 if b > 0 else 1.0), dist=hd, weld=hd, width=2 * hb,
                   name="flange" if shape in I_SHAPES + C_SHAPES else "side",
                   wall=None if solid else (tf if shape in I_SHAPES + C_SHAPES else t))
    elif abs(a) >= 0.985:  # onto the width side: an I's web (between the flanges) or its flange tips
        n = x * (-1.0 if a > 0 else 1.0)
        if shape in I_SHAPES + C_SHAPES:
            fits = True
            if inc is not None:
                off = abs(((at if at is not None else base) - base) * y)
                fits = off + extent_along(inc, y) <= hd - tf - 0.005
            web = tw / 2.0 if shape in I_SHAPES else (hb if a > 0 else -(hb - tw))  # a channel's web is at -x
            clear = 2 * (hd - tf) - 0.02
            if shape in C_SHAPES and a > 0:  # onto the channel's back: the web's outer face
                out.update(n=n, dist=hb, weld=hb, name="web", wall=tw, width=2 * hd, clear=None)
            elif fits:
                out.update(n=n, dist=web, weld=web, name="web", wall=tw, width=2 * (hd - tf), clear=clear)
            else:
                out.update(n=n, dist=hb, weld=web, name="flange tips", wall=None, width=2 * (hd - tf), clear=clear)
        else:
            out.update(n=n, dist=hb, weld=hb, name="side", wall=None if solid else t, width=2 * hd)
    else:
        r = math.hypot(hb, hd)
        out.update(n=vp * -1.0, dist=r, weld=r, name="oblique", wall=None, width=min(hb, hd) * 2)
    out["origin"] = base + out["n"] * out["dist"]
    out["axis"] = base
    out["ys"] = y  # the support's depth direction: its flanges lie across it
    out["support"] = sup
    return out


def covers(col, p, v, slack=0.02):
    """Whether a column stands across the line through p along v (plan), not beside it."""
    ap = axis_point(col, p)
    lat = rg.Vector3d(-v.Y, v.X, 0.0)
    if lat.Length < 1e-9:
        return True
    lat.Unitize()
    return abs((ap - p) * lat) <= extent_along(col, lat) + slack


def hit(p, v, origin, n):
    """Where the line through p along v meets the plane (origin, n); None if nearly parallel."""
    dn = v * n
    if abs(dn) < 0.2:
        return None
    return p + v * (((origin - p) * n) / dn)


# ── member records ───────────────────────────────────────────

def measure(rec):
    """Set length (m) and kg on a member record."""
    L = point(rec["start"]).DistanceTo(point(rec["end"]))
    rec["length"] = round(L, 4)
    rec["kg"] = round(L * float((rec.get("section") or {}).get("kg_m") or 0.0), 2)
    return rec


def solid(rec, u=None):
    """The member's solid (Brep, model units), or None."""
    u = u or ab.units()
    return ab.member(ab.P(rec["start"], u), ab.P(rec["end"], u), rec.get("section") or {}, float(rec.get("rotation") or 0.0))


def axis_curve(rec, u=None):
    u = u or ab.units()
    return rg.LineCurve(ab.P(rec["start"], u), ab.P(rec["end"], u))


def colour(rec, highlight=False):
    """Preview colour: the material's, columns a shade darker than beams; highlighted (edited) members green, which
    stands apart from the connectors' orange plates."""
    import System.Drawing
    if highlight:
        return System.Drawing.Color.FromArgb(70, 170, 110)
    c = ab.colour(rec.get("material") or (rec.get("section") or {}).get("material"))
    if rec.get("role") == "column":
        return System.Drawing.Color.FromArgb(max(0, int(c.R * 0.82)), max(0, int(c.G * 0.82)), max(0, int(c.B * 0.82)))
    return c


def totals(recs):
    """A short summary: counts, lengths, steel tonnes, timber and concrete volumes."""
    cols = [r for r in recs if r.get("role") == "column"]
    beams = [r for r in recs if r.get("role") == "beam"]
    other = [r for r in recs if r.get("role") not in ("column", "beam")]
    L = lambda rs: sum(float(r.get("length") or 0) for r in rs)
    steel = sum(float(r.get("kg") or 0) for r in recs if (r.get("material") or "") == "steel") / 1000.0
    vol = {}
    for r in recs:
        m = r.get("material") or ""
        if m in ("timber", "concrete"):
            hb, hd = halves(r.get("section"))
            area = math.pi * hd * hd if shape_of(r.get("section")) in ROUND_SHAPES else 4 * hb * hd
            vol[m] = vol.get(m, 0.0) + area * float(r.get("length") or 0)
    parts = ["%d columns (%.1f m)" % (len(cols), L(cols)), "%d beams (%.1f m)" % (len(beams), L(beams))]
    if other:
        parts.append("%d other (%.1f m)" % (len(other), L(other)))
    mass = []
    if steel:
        mass.append("steel %.2f t" % steel)
    for m, v in sorted(vol.items()):
        mass.append("%s %.2f m3" % (m, v))
    return ", ".join(parts) + ("; " + ", ".join(mass) if mass else "")


# ── building a frame ─────────────────────────────────────────

def _code(level):
    return str(level.get("code") or level.get("name") or "L%s" % level.get("index", ""))


def _angle(g):
    a, b = g.get("start") or [0, 0, 0], g.get("end") or [1, 0, 0]
    return math.degrees(math.atan2(float(b[1]) - float(a[1]), float(b[0]) - float(a[0])))


def _norm_angle(a):
    """Into (-90, 90]: I, box and rectangle sections look the same turned half a turn."""
    a = (a + 90.0) % 180.0 - 90.0
    return 90.0 if abs(a + 90.0) < 1e-9 else a


def parse_nodes(text):
    """'B/2, C/3' -> [frozenset({'B','2'}), ...] (either order; '/' or '-' between)."""
    out = []
    for tok in re.split(r"[,;\s]+", str(text or "")):
        tok = tok.strip()
        if not tok:
            continue
        parts = [p.strip().upper() for p in re.split(r"[/\-]", tok) if p.strip()]
        if len(parts) == 2:
            out.append((tok, frozenset(parts)))
        else:
            out.append((tok, None))
    return out


def build_frame(grids, levels, column_spec, beam_spec, columns_at="all intersections", skip="", beams_on="both",
                base=None, top=None, rotation=0.0, top_offset=0.15):
    """Columns at grid intersections, one per storey from `base` to `top`, and beams along grid lines between
    intersections at each level above the base (tops `top_offset` below the level). Returns (records, notes); records
    are member dicts (metres), columns first."""
    notes = []
    nodes = ab.intersections(grids, 1.0)  # metres
    lv = []
    for r in levels:  # drop repeated elevations
        if not lv or abs(float(r["elevation"]) - float(lv[-1]["elevation"])) > 1e-6:
            lv.append(r)
    if not lv:
        return [], ["no levels"]
    lo, hi = base or lv[0], top or lv[-1]
    if float(lo["elevation"]) > float(hi["elevation"]):
        lo, hi = hi, lo
        notes.append("from and to levels were swapped")
    z_lo, z_hi = float(lo["elevation"]), float(hi["elevation"])
    storeys = [r for r in lv if z_lo - 1e-6 <= float(r["elevation"]) < z_hi - 1e-6]
    beam_levels = [r for r in lv if z_lo + 1e-6 < float(r["elevation"]) <= z_hi + 1e-6]
    if not storeys:
        beam_levels = [lo]
        notes.append("from and to are the same level: beams only, no columns")
    has_beams = beams_on != "none"

    # where columns go
    col_nodes = []
    if columns_at != "none" and storeys:
        ext = {}
        for g in grids:
            grp = g.get("group")
            i = int(g.get("index", 0) or 0)
            lo_i, hi_i = ext.get(grp, (i, i))
            ext[grp] = (min(lo_i, i), max(hi_i, i))

        def edge(g):
            lo_i, hi_i = ext.get(g.get("group"), (0, 0))
            return int(g.get("index", 0) or 0) in (lo_i, hi_i)

        skips = parse_nodes(skip)
        used = set()
        for nd in nodes:
            name, p, ga, gb = nd
            key = frozenset([str(ga["name"]).upper(), str(gb["name"]).upper()])
            hit_ = [tok for tok, k in skips if k == key]
            if hit_:
                used.update(hit_)
                continue
            if columns_at == "perimeter" and not (edge(ga) or edge(gb)):
                continue
            col_nodes.append(nd)
        bad = [tok for tok, k in skips if tok not in used]
        if bad:
            notes.append("skip: no intersection %s" % ", ".join(bad))

    recs = []
    for i, L in enumerate(storeys):
        z0 = float(L["elevation"])
        nxt = lv[lv.index(L) + 1] if lv.index(L) + 1 < len(lv) else None
        z1 = float(nxt["elevation"]) if nxt else z0 + float(L.get("height") or 3.0)
        top_storey = nxt is None or float(nxt["elevation"]) >= z_hi - 1e-6
        if top_storey and has_beams:
            z1 = max(z0 + 0.1, z1 - top_offset)  # flush with the top beams' tops
        for name, p, ga, gb in col_nodes:
            rot = _norm_angle(_angle(gb) - 90.0) + float(rotation or 0.0)
            recs.append({"alien": "member", "id": "C-%s-%s" % (name, _code(L)), "role": "column",
                         "start": [p.X, p.Y, z0], "end": [p.X, p.Y, z1], "section": dict(column_spec),
                         "section_name": column_spec["name"], "rotation": round(rot, 4), "material": column_spec["material"],
                         "level": _code(L), "level_name": L.get("name"), "level_top": _code(nxt) if nxt else None,
                         "grid": name, "justify": "centre"})

    # beams along grid lines, intersection to intersection
    lines = []
    for g in grids:
        if beams_on == "none":
            break
        if beams_on in ("letters", "numbers") and g.get("group") != beams_on:
            continue
        on = []
        a, b = point(g["start"]), point(g["end"])
        dvec = b - a
        dvec.Unitize()
        for name, p, ga, gb in nodes:
            if ga is g or gb is g:
                other = gb if ga is g else ga
                on.append(((rg.Point3d(p.X, p.Y, 0) - rg.Point3d(a.X, a.Y, 0)) * dvec, name, p, other))
        on.sort(key=lambda r: r[0])
        lines.append((g, on))
    for L in beam_levels:
        ztop = float(L["elevation"]) - float(top_offset or 0.0)
        for g, on in lines:
            for (t0, n0, p0, o0), (t1, n1, p1, o1) in zip(on, on[1:]):
                if t1 - t0 < 0.05:
                    continue
                hd = vertical_half({"section": beam_spec, "rotation": 0.0})
                recs.append({"alien": "member", "id": "B-%s-%s-%s/%s" % (_code(L), g["name"], o0["name"], o1["name"]),
                             "role": "beam", "start": [p0.X, p0.Y, ztop - hd], "end": [p1.X, p1.Y, ztop - hd],
                             "section": dict(beam_spec), "section_name": beam_spec["name"], "rotation": 0.0,
                             "material": beam_spec["material"], "level": _code(L), "level_name": L.get("name"),
                             "grid": str(g["name"]),
                             "group": g.get("group"), "span": "%s/%s" % (o0["name"], o1["name"]), "node_names": [n0, n1],
                             "nodes": [[p0.X, p0.Y, ztop], [p1.X, p1.Y, ztop]], "supports": [None, None], "justify": "top"})
    fit_beams(recs)
    for r in recs:
        measure(r)
        for k in ("start", "end"):
            r[k] = [round(float(c), 6) for c in r[k]]
    return recs, notes


def vertical_half(rec):
    """Half the section's height for a horizontal member, after its rotation (metres)."""
    hb, hd = halves(rec.get("section"))
    if shape_of(rec.get("section")) in TUBE_SHAPES + ROUND_SHAPES:
        return hd
    r = math.radians(float(rec.get("rotation") or 0.0))
    return abs(math.cos(r)) * hd + abs(math.sin(r)) * hb


CELL = 2.0  # m: plan cells for finding supports near a node


def cell(x, y):
    return (int(math.floor(x / CELL)), int(math.floor(y / CELL)))


def near(index, x, y):
    cx, cy = cell(x, y)
    seen, out = set(), []
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            for item in index.get((cx + i, cy + j), ()):
                if id(item) not in seen:
                    seen.add(id(item))
                    out.append(item)
    return out


def fit_beams(members, ids=None):
    """Trim beam ends (records with "nodes", the grid points at the beam's top) to their supports: the face of a column
    at the node (+ GAP), else the web or flange tips of the primary beam crossing there (+ GAP), else the node (or
    half a GAP short of a beam continuing in line). At a node without a column, the beams in the direction with more
    members there are primary (ties: the lettered grids') and carry the others. Only beams whose id is in `ids` are
    changed (all if None). Sets start, end (the centreline, top kept at the nodes' height) and supports."""
    cols = [m for m in members if m.get("role") == "column"]
    beams = [m for m in members if m.get("role") == "beam" and len(m.get("nodes") or []) == 2]
    cidx = {}
    for c in cols:
        s, e = [float(x) for x in c["start"]], [float(x) for x in c["end"]]
        item = [c, min(s[2], e[2]), max(s[2], e[2]), radius(c.get("section"))]
        for k in {cell(s[0], s[1]), cell(e[0], e[1])}:
            cidx.setdefault(k, []).append(item)
    data, bidx = [], {}
    for o in beams:
        a, b = [float(x) for x in o["nodes"][0]], [float(x) for x in o["nodes"][1]]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        item = [o, a, b, (dx / L, dy / L) if L > 1e-9 else (0.0, 0.0), L]
        data.append(item)
        n = max(1, int(L / (CELL / 2.0)) + 1)
        for k in {cell(a[0] + dx * t / n, a[1] + dy * t / n) for t in range(n + 1)}:
            bidx.setdefault(k, []).append(item)
    for item in data:
        b, n0, n1, d, L = item
        if ids is not None and b.get("id") not in ids:
            continue
        hv = vertical_half(b)
        ends, sups = [], []
        for N, O, sgn in ((n0, n1, -1.0), (n1, n0, 1.0)):
            A = rg.Point3d(N[0], N[1], N[2] - hv)
            if L < 1e-9:
                ends.append(A)
                sups.append(None)
                continue
            v = rg.Vector3d(d[0] * sgn, d[1] * sgn, 0.0)  # pointing into the node
            end, sup = _fit_end(item, A, N, v, near(cidx, N[0], N[1]), near(bidx, N[0], N[1]))
            if (end - rg.Point3d(O[0], O[1], O[2] - hv)) * v < 0.05:  # trimmed past the other end: keep the node
                end, sup = A, None
            ends.append(end)
            sups.append(sup)
        b["start"], b["end"], b["supports"] = coords(ends[0]), coords(ends[1]), sups
    return members


def _fit_end(item, A, N, v, col_cands, beam_cands):
    b = item[0]
    # a column at the node
    best = None
    for c, zlo, zhi, rad in col_cands:
        if not (zlo - 1e-3 <= A.Z <= zhi + 1e-3):
            continue
        ap = axis_point(c, A)
        dd = math.hypot(A.X - ap.X, A.Y - ap.Y)
        if dd <= rad + CATCH and covers(c, A, v) and (best is None or dd < best[0]):
            best = (dd, c)
    if best:
        f = face(best[1], v, b, A)
        if f:
            end = hit(A, v, f["origin"] + f["n"] * GAP, f["n"])
            if end is not None:
                return end, best[1].get("id")
    # beams meeting at the node
    vx, vy = v.X, v.Y
    partners, ending, through = [], [], []
    for other in beam_cands:
        o, a, bb, od, Lo = other
        if o is b or Lo < 1e-9 or abs(a[2] - N[2]) > 0.05 or abs(bb[2] - N[2]) > 0.05:
            continue
        wx, wy = N[0] - a[0], N[1] - a[1]
        tpar = wx * od[0] + wy * od[1]
        if math.hypot(wx - od[0] * tpar, wy - od[1] * tpar) > 0.05 or tpar < -0.05 or tpar > Lo + 0.05:
            continue
        at_end = tpar <= 0.05 or tpar >= Lo - 0.05
        cos = abs(od[0] * vx + od[1] * vy)
        if cos > 0.95:
            if at_end:
                partners.append(o)
        elif cos < 0.5:
            (ending if at_end else through).append(o)
    if through:
        sup = sorted(through, key=lambda o: str(o.get("id")))[0]
    elif ending:
        mine, theirs = 1 + len(partners), len(ending)
        if mine != theirs:
            primary = mine > theirs
        else:
            ml = b.get("group") == "letters" or any(o.get("group") == "letters" for o in partners)
            tl = any(o.get("group") == "letters" for o in ending)
            if ml != tl:
                primary = ml
            else:
                primary = min(str(o.get("id")) for o in [b] + partners) < min(str(o.get("id")) for o in ending)
        if primary:
            if partners:
                return A - v * (GAP / 2.0), None
            return A + v * max(halves(o.get("section"))[0] for o in ending), None  # carry the secondaries' joint
        sup = sorted(ending, key=lambda o: str(o.get("id")))[0]
    elif partners:
        return A - v * (GAP / 2.0), None
    else:
        return A, None
    f = face(sup, v, b, A)
    if f:
        end = hit(A, v, f["origin"] + f["n"] * GAP, f["n"])
        if end is not None:
            return end, sup.get("id")
    return A, None


def sample_frame():
    """The frame beams_columns makes unwired: sample grids and levels, steel defaults."""
    col, _ = lookup("", "steel", "column")
    beam, _ = lookup("", "steel", "beam")
    recs, _ = build_frame(ab.sample_grids(), ab.sample_levels(), col, beam)
    return recs
