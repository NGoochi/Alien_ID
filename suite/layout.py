# NODE_INPUTS: views:list[str] | "named views to place (e.g. from views or site_views); unwired: every view the Alien view nodes made"
# NODE_INPUTS: sheet:str | "sheet size" default="A1" options="A4|A3|A2|A1|A0", orientation:str default="landscape" options="landscape|portrait"
# NODE_INPUTS: arrange:str | "auto grid: several views a sheet; rows: plans, elevations, sections and 3D views in rows, one scale a row" default="auto grid" options="auto grid|one per sheet|rows"
# NODE_INPUTS: scale:str | "auto: the largest standard scale that fits each view" default="auto" options="auto|auto, one scale|1:20|1:50|1:100|1:200|1:250|1:500|1:1000|1:2000"
# NODE_INPUTS: display:str | "detail display mode: 'from view' uses each view's own (Technical instead of none or Wireframe), or a Rhino display mode name" default="from view"
# NODE_INPUTS: project:str | "title block: project" default="Project", title:str | "title block: drawing title" default="General arrangement"
# NODE_INPUTS: number:str | "drawing number (counts up over several sheets: A101, A102...)" default="A101", date:str | "date (blank: today)" default="", drawn:str | "drawn by" default="Alien"
# NODE_INPUTS: prefix:str | "sheet name prefix: a press replaces the sheets made with this prefix" default="Alien sheet"
# NODE_INPUTS: make:bool | "create (or replace) the layout pages in Rhino" button
# NODE_OUTPUTS: pages:list[str] | "layout page names", scales:list[str] | "scale of each placed view, in order ('1:100'; 'NTS' for perspectives)"
# NODE_OUTPUTS: sheets:list[Curve] | "preview of the sheets at paper size (1 mm = 1 mm) from the world origin: border, details, view titles, title block", info:str
# CATEGORY: Views & drawings
# Layout: named views on A4-A0 layout sheets at standard scales (1:20, 1:50, 1:100...), with view titles, scale labels and a title block.
#
# Each view is drawn at the largest standard scale its slot allows (or one scale for all, or a forced scale), as a
# locked detail the size of the view's frame, in the view's display mode with its clipping planes. Perspectives are
# fitted and marked NTS. The press replaces the sheets made with the same prefix and puts the model Perspective view
# back as the active view. Without the button, `sheets` previews the arrangement.
import datetime
import math
import re

import Rhino
import Rhino.Geometry as rg
import System
import System.Drawing

import alien_bim as ab
import alien_views as av

u = ab.units()
d = Rhino.RhinoDoc.ActiveDoc
notes = []
MM = Rhino.UnitSystem.Millimeters
SIZES = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0), "A0": (1189.0, 841.0)}
CAP = {"A4": 4, "A3": 6, "A2": 9, "A1": 12, "A0": 16}  # most views an auto grid puts on one sheet
TEXT = {"A4": 2.5, "A3": 3.0, "A2": 3.5, "A1": 3.5, "A0": 5.0}
SCALES = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 1250, 2000, 2500, 5000, 10000, 20000, 50000]

size = sheet if sheet in SIZES else "A1"
if sheet not in SIZES:
    notes.append("unknown sheet %r: used A1" % sheet)
pw, ph = SIZES[size]
orient = "portrait" if str(orientation).strip().lower() == "portrait" else "landscape"
if str(orientation).strip().lower() not in ("portrait", "landscape", "", "none"):
    notes.append("unknown orientation %r: used landscape" % orientation)
if orient == "portrait":
    pw, ph = ph, pw
th = TEXT[size]                  # text height on paper (mm)
b = 10.0                         # border inset
g = max(8.0, 2.5 * th)           # gap between views
title_h = 3.2 * th               # view title band under each detail
rh = 2.6 * th                    # title block row
tb_w, tb_h = min(180.0, pw - 2 * b), 4 * rh
area = (b + 5, b + tb_h + g, pw - b - 5, ph - b - 5)   # x0, y0, x1, y1: where views go
AW, AH = area[2] - area[0], area[3] - area[1]

# ── the views ──

names_in = [str(v).strip() for v in (views or []) if v is not None and str(v).strip()]
reg = av.registry(d) if d else {}
table = [d.NamedViews[i].Name for i in range(d.NamedViews.Count)] if d else []
if not names_in:
    names_in = [n for n in table if reg.get(n, {}).get("node") in ("views", "exploded_axo")]
    source = "every Alien named view"
else:
    source = "%d wired names" % len(names_in)

items, missing = [], []
for name in names_in:
    idx = table.index(name) if name in table else -1
    if idx < 0:  # a title without its prefix ('Ground plan') if it names exactly one view
        hits = [i for i, n in enumerate(table) if av.title_of(n).lower() == name.lower()]
        idx = hits[0] if len(hits) == 1 else -1
    if idx < 0:
        missing.append(name)
        continue
    vi = d.NamedViews[idx]
    vp = vi.Viewport
    ok, l, r, bt, tp, n_, f_ = vp.GetFrustum()
    if not ok or r - l <= 0 or tp - bt <= 0:
        missing.append(name)
        continue
    items.append({"name": table[idx], "title": av.title_of(table[idx]), "kind": av.kind_of(table[idx], d),
                  "parallel": vp.IsParallelProjection, "w": (r - l) / u * 1000.0, "h": (tp - bt) / u * 1000.0,  # model mm
                  "mode": vi.DisplayModeId})
# perspectives have no size: give them the height of the tallest drawing, so they share slots fairly
ref_h = max([it["h"] for it in items if it["parallel"]] or [10000.0])
for it in items:
    if not it["parallel"]:
        it["w"], it["h"] = ref_h * it["w"] / it["h"], ref_h

forced = None
if scale not in ("auto", "auto, one scale"):
    try:
        forced = int(str(scale).split(":")[-1])
    except ValueError:
        notes.append("unknown scale %r: used auto" % scale)


def std(need):
    """The largest standard scale (smallest n) at which 1:n is at least as small as 1:need."""
    return next((s for s in SCALES if s >= need - 1e-9), SCALES[-1])


def fit(it, cw, ch):
    return std(max(it["w"] / max(cw, 1e-6), it["h"] / max(ch, 1e-6)))


# ── arrangement: sheets of slots (x, y, w, h of the slot for the detail; the title goes under it) ──

def grid(chunk):
    """Best columns for a chunk of views: the one that gives the largest scales overall."""
    best = None
    for cols in range(1, len(chunk) + 1):
        rows = int(math.ceil(len(chunk) / float(cols)))
        cw = (AW - (cols - 1) * g) / cols
        ch = (AH - (rows - 1) * g) / rows - title_h
        if cw <= 0 or ch <= 0:
            continue
        # largest standard scales first; between equals, the grid with the most room to spare
        score = (round(sum(math.log(fit(it, cw, ch)) for it in chunk), 9),
                 sum(math.log(max(it["w"] / cw, it["h"] / ch)) for it in chunk))
        if best is None or score < best[0]:
            best = (score, cols, rows, cw, ch)
    if best is None:  # (a sheet too small for any grid: one row)
        best = (None, len(chunk), 1, (AW - (len(chunk) - 1) * g) / len(chunk), max(AH - title_h, 1.0))
    _, cols, rows, cw, ch = best
    slots = []
    for k, it in enumerate(chunk):
        r_, c_ = divmod(k, cols)
        x = area[0] + c_ * (cw + g)
        y = area[3] - (r_ + 1) * (ch + title_h) - r_ * g + title_h
        slots.append((it, x, y, cw, ch, "centre"))
    return slots


ROWS = [("plans", ("plan", "rcp", "top")), ("elevations", ("elevation",)), ("sections", ("section",)), ("3D", ("iso", "axo", "persp", "other"))]


def rows_sheet(chunk):
    """Rows by kind (wrapped at 4), height shared out by each row's tallest view; one scale per row."""
    rows = []
    for _, kinds in ROWS:
        group = [it for it in chunk if it["kind"] in kinds]
        for k in range(0, len(group), 4):
            rows.append(group[k:k + 4])
    total_h = sum(max(it["h"] for it in row) for row in rows)
    free_h = max(AH - len(rows) * title_h - (len(rows) - 1) * g, 10.0 * len(rows))
    slots, y = [], area[3]
    for row in rows:
        tall = max(it["h"] for it in row)
        budget = free_h * tall / total_h
        n = std(max(sum(it["w"] for it in row) / max(AW - (len(row) - 1) * g, 1e-6), tall / max(budget, 1e-6)))
        y -= budget
        x = area[0]
        widths = [it["w"] / n for it in row]
        spare = (AW - sum(widths) - (len(row) - 1) * g) / max(len(row), 1)  # spread spare width evenly
        for it, w in zip(row, widths):
            slots.append((it, x, y, w + spare, budget, "bottom", n))
            x += w + spare + g
        y -= title_h + g
    return slots


sheets_plan = []   # list of slot lists
if items:
    if arrange == "one per sheet":
        sheets_plan = [[(it, area[0], area[1] + title_h, AW, AH - title_h, "centre")] for it in items]
    elif arrange == "rows":
        sheets_plan = [rows_sheet(items)]
    else:
        if arrange != "auto grid":
            notes.append("unknown arrangement %r: used auto grid" % arrange)
        k = int(math.ceil(len(items) / float(CAP[size])))
        per = int(math.ceil(len(items) / float(k)))
        sheets_plan = [grid(items[i:i + per]) for i in range(0, len(items), per)]

# scales: each slot's own, then one for all or forced
placed = []   # (sheet index, item, x0, y0, x1, y1, n or None, slot)
for si, slots in enumerate(sheets_plan):
    for s in slots:
        it, x, y, w, h, how = s[:6]
        n = s[6] if len(s) > 6 else fit(it, w, h)
        placed.append([si, it, n, s])
if scale == "auto, one scale" and placed:
    one = max(p[2] for p in placed if p[1]["parallel"]) if any(p[1]["parallel"] for p in placed) else None
    for p in placed:
        p[2] = one or p[2]
elif forced:
    for p in placed:
        p[2] = forced

details = []  # (sheet index, item, x0, y0, x1, y1, n)
cropped = []
for si, it, n, s in placed:
    _, x, y, w, h, how = s[:6]
    dw, dh = it["w"] / n, it["h"] / n
    if dw > w + 1e-6 or dh > h + 1e-6:
        if it["parallel"]:
            cropped.append((it["title"], fit(it, w, h)))
        k = min(w / dw, h / dh) if not it["parallel"] else 1.0
        dw, dh = (dw * k, dh * k) if not it["parallel"] else (min(dw, w), min(dh, h))
    x0 = x + (w - dw) / 2
    y0 = y + ((h - dh) / 2 if how == "centre" else 0.0)
    details.append((si, it, x0, y0, x0 + dw, y0 + dh, n))

if cropped:
    need = sorted(set(c[1] for c in cropped))
    notes.append("%d view%s cropped: too big for %s slot at %s (1:%d%s would fit): %s" % (
        len(cropped), "" if len(cropped) == 1 else "s", "its" if len(cropped) == 1 else "their",
        "1:%d" % forced if forced else "one scale for all", need[0], "" if len(need) == 1 else " to 1:%d" % need[-1],
        ", ".join(c[0] for c in cropped[:5]) + (" ..." if len(cropped) > 5 else "")))
scales = [("1:%d" % n) if it["parallel"] else "NTS" for _, it, _, _, _, _, n in details]


# ── names, title block text ──

P = av.with_separator(prefix)
count = len(sheets_plan)


def numbered(k):
    s = str(number or "").strip()
    m = re.search(r"(\d+)(\D*)$", s)
    if not s:
        return "%02d" % (k + 1)
    if not m:
        return s if k == 0 else "%s-%d" % (s, k + 1)
    digits = m.group(1)
    return s[:m.start(1)] + str(int(digits) + k).zfill(len(digits)) + m.group(2)


today = datetime.date.today().strftime("%d %b %Y")
date_s = str(date).strip() if date and str(date).strip() else today
sheet_names = [P + ("%s %s" % (numbered(k), str(title or "").strip())).strip() for k in range(count)]


def sheet_scale(si):
    ns = sorted(set(n for s_, it, *_r, n in details if s_ == si and it["parallel"]))
    return ("1:%d @ %s" % (ns[0], size)) if len(ns) == 1 else ("AS SHOWN @ %s" % size) if ns else "NTS"


def title_block(si):
    """(curves, texts) of the title block in paper mm: texts are (string, x, y, height, justify)."""
    x1, y0 = pw - b, b
    x0 = x1 - tb_w
    cur, txt = [], []
    rect = lambda a, c, e, f: rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(a, e), rg.Interval(c, f)).ToNurbsCurve()
    cur.append(rect(x0, y0, x1, y0 + tb_h))
    rows = [[("PROJECT", str(project or ""), 1.0)], [("DRAWING", str(title or ""), 1.0)],
            [("NUMBER", numbered(si), 0.34), ("SCALE", sheet_scale(si), 0.38), ("SHEET", "%d of %d" % (si + 1, count), 0.28)],
            [("DATE", date_s, 0.34), ("DRAWN", str(drawn or ""), 0.38), ("", "Alien", 0.28)]]
    for r_, cells in enumerate(rows):
        top = y0 + tb_h - r_ * rh
        if r_:
            cur.append(rg.LineCurve(rg.Point3d(x0, top, 0), rg.Point3d(x1, top, 0)))
        x = x0
        for k, (label, value, frac) in enumerate(cells):
            w = tb_w * frac
            if k:
                cur.append(rg.LineCurve(rg.Point3d(x, top - rh, 0), rg.Point3d(x, top, 0)))
            if label:
                txt.append((label, x + 1.5, top - 1.2, th * 0.45, "topleft"))
            txt.append((value, x + 1.5, top - rh + 1.3, th if r_ < 2 else th * 0.8, "bottomleft"))
            x += w
    return cur, txt


def view_title(k, it, x0, y0, x1, n):
    """A numbered view title under a detail: (n) TITLE, a rule, the scale."""
    r = th * 0.75
    cy = y0 - th * 1.25
    cur = [rg.Circle(rg.Plane(rg.Point3d(x0 + r, cy, 0), rg.Vector3d.ZAxis), r).ToNurbsCurve(),
           rg.LineCurve(rg.Point3d(x0 + 2 * r + 1.0, cy - r * 0.15, 0), rg.Point3d(max(x1, x0 + 2 * r + 30.0), cy - r * 0.15, 0))]
    txt = [(str(k), x0 + r, cy, th * 0.7, "middlecenter"),
           (it["title"].upper(), x0 + 2 * r + 1.5, cy + 0.6, th, "bottomleft"),
           ("1:%d" % n if it["parallel"] else "NTS", x0 + 2 * r + 1.5, cy - r * 0.15 - 0.8, th * 0.7, "topleft")]
    return cur, txt


def sheet_drawing(si):
    """Everything drawn on one sheet (paper mm): border, title block, details' outlines (preview only), view titles."""
    cur = [rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(b, pw - b), rg.Interval(b, ph - b)).ToNurbsCurve()]
    txt = []
    c2, t2 = title_block(si)
    cur += c2
    txt += t2
    k = 0
    for s_, it, x0, y0, x1, y1, n in details:
        if s_ != si:
            continue
        k += 1
        c3, t3 = view_title(k, it, x0, y0, x1, n)
        cur += c3
        txt += t3
    return cur, txt


# ── preview: sheets side by side from the world origin, 1 paper mm = 1 model mm ──

JUST = {"topleft": ("left", "top"), "bottomleft": ("left", "bottom"), "middlecenter": ("center", "middle")}
mm = u / 1000.0
sheets = []
for si in range(count):
    ox = si * (pw + 40.0)
    xf = rg.Transform.Scale(rg.Point3d.Origin, mm) * rg.Transform.Translation(ox, 0, 0)
    cur, txt = sheet_drawing(si)
    cur.append(rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(0, pw), rg.Interval(0, ph)).ToNurbsCurve())
    for s_, it, x0, y0, x1, y1, n in details:
        if s_ == si:
            cur.append(rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(x0, x1), rg.Interval(y0, y1)).ToNurbsCurve())
    for s, x, y, h, j in txt:
        ha, va = JUST[j]
        cur += av.letters(s, rg.Plane(rg.Point3d(x, y, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis), h, ha, va) if s.strip() else []
    for c in cur:
        c.Transform(xf)
    sheets += cur

# ── info ──

lines = ["%d views (%s) on %d %s %s sheet%s, %s" % (len(details), source, count, size, orient, "" if count == 1 else "s",
                                                    arrange if arrange in ("auto grid", "one per sheet", "rows") else "auto grid")]
for si in range(count):
    on = ["%s %s" % (it["title"], ("1:%d" % n) if it["parallel"] else "NTS") for s_, it, *_r, n in details if s_ == si]
    lines.append("  %s: %s" % (sheet_names[si], ", ".join(on)))
override = None if str(display or "").strip().lower() in ("", "from view") else av.find_mode(display)
if str(display or "").strip().lower() not in ("", "from view") and override is None:
    notes.append("no display mode %r: details use each view's own (have: %s)" % (display, ", ".join(av.mode_names())))
if missing:
    notes.append("no named view %s (press make on the views node first?)" % ", ".join(repr(m) for m in missing))
if not items:
    notes.append("no views to place: press make on a views node, or wire named view names")


# ── create in Rhino (button only) ──

def tagged(page, layer):
    a = Rhino.DocObjects.ObjectAttributes()
    a.LayerIndex = layer
    a.Space = Rhino.DocObjects.ActiveSpace.PageSpace
    a.ViewportId = page.MainViewport.Id
    a.SetUserString(av.LAYOUT, P)
    return a


def clear_sheets():
    """Close the pages this prefix made last time (and take their details off any clipping planes)."""
    gone = 0
    clips = av.clip_objects(d)
    pages = {str(p.MainViewport.Id): p for p in d.Views.GetPageViews()}
    for key, info_ in av.registry(d, av.LAYOUT).items():
        if info_.get("set") != P:
            continue
        page = pages.get(key)
        if page is not None:
            ids = [dv.Viewport.Id for dv in page.GetDetailViews() or []]
            for o in clips:
                vps = list(o.ClippingPlaneGeometry.ViewportIds() or [])
                if any(i in vps for i in ids):
                    for i in ids:
                        o.ClippingPlaneGeometry.RemoveClipViewportId(i)
                    o.CommitChanges()
            page.Close()
            gone += 1
        av.forget(d, key, av.LAYOUT)
    for o in list(d.Objects.FindByUserString(av.LAYOUT, P, True) or []):
        d.Objects.Delete(o, True)
    return gone


if make and d is not None:
    gone = clear_sheets()
    lay = d.Layers.FindName("Alien sheets")
    layer = lay.Index if lay is not None else d.Layers.Add("Alien sheets", System.Drawing.Color.Black)
    fallback = av.find_mode("Technical")
    pages, clip_later = [], []
    try:
        for si in range(count):
            page = d.Views.AddPageView(sheet_names[si], pw, ph)
            if page is None:
                notes.append("Rhino refused the page %s" % sheet_names[si])
                continue
            av.register(d, str(page.MainViewport.Id), section=av.LAYOUT, set=P, page=sheet_names[si])
            pages.append(page.PageName)
            for s_, it, x0, y0, x1, y1, n in details:
                if s_ != si:
                    continue
                det = page.AddDetailView(it["title"], rg.Point2d(x0, y0), rg.Point2d(x1, y1), Rhino.Display.DefinedViewportProjection.Top)
                if det is None:
                    notes.append("could not add a detail for %s" % it["title"])
                    continue
                page.SetActiveDetail(det.Id)      # camera changes only stick on the active detail
                vp = det.Viewport
                vi = d.NamedViews[d.NamedViews.FindByName(it["name"])]
                d.NamedViews.Restore(d.NamedViews.FindByName(it["name"]), vp, False)   # camera + clipping planes
                clip_later += [(cid, vp.Id) for cid in vi.ClippingPlanesIds or []]
                own = Rhino.Display.DisplayModeDescription.GetDisplayMode(it["mode"])
                if own is not None and own.EnglishName == "Wireframe":  # a view saved without a chosen mode
                    own = None
                mode = override or own or fallback
                if mode is not None:
                    vp.DisplayMode = mode
                det.CommitViewportChanges()
                if it["parallel"]:
                    det.DetailGeometry.SetScale(n, MM, 1, MM)
                    det.DetailGeometry.IsProjectionLocked = True
                det.CommitChanges()
                a = det.Attributes.Duplicate()
                a.Name = it["title"]
                a.SetUserString(av.LAYOUT, P)
                a.LayerIndex = layer
                d.Objects.ModifyAttributes(det, a, True)
            cur, txt = sheet_drawing(si)
            for c in cur:
                d.Objects.AddCurve(c, tagged(page, layer))
            J = {"topleft": Rhino.Geometry.TextJustification.TopLeft, "bottomleft": Rhino.Geometry.TextJustification.BottomLeft,
                 "middlecenter": Rhino.Geometry.TextJustification.MiddleCenter}
            for s, x, y, h, j in txt:
                if s.strip():
                    d.Objects.AddText(s, rg.Plane(rg.Point3d(x, y, 0), rg.Vector3d.ZAxis), h, "Arial", False, False, J[j], tagged(page, layer))
            page.Redraw()
        # Restore lists each detail on its view's clipping planes, but the details only clip once the planes are
        # committed after the pages exist.
        for cid, vid in clip_later:
            o = d.Objects.FindId(cid)
            if o is not None:
                o.ClippingPlaneGeometry.AddClipViewportId(vid)
                o.CommitChanges()
    finally:
        av.back_to_model(d)
    lines.append("Made %d layout page%s; replaced %d from the last press" % (len(pages), "" if len(pages) == 1 else "s", gone))
    pages_out = pages
else:
    pages_out = sheet_names
    if count:
        lines.append("Press make to create the layout page%s." % ("" if count == 1 else "s"))

pages = pages_out
info = "\n".join(lines + notes)
