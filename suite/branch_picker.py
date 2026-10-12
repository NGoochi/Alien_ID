# NODE_INPUTS: tree:tree[object] | "data tree to pick from", pick:str | "branches to keep: 0, 2, 4-6, -1 (last), * (all), even, odd" default="0"
# NODE_INPUTS: flatten:bool | "merge the picked branches into one list" default=false
# NODE_OUTPUTS: picked | "the picked branches, in the order asked for", rest | "the branches not picked", count:int | "branches picked", branches:str | "every branch: index, size, contents"
# CATEGORY: Data & lists
# Branch picker: pick one or several branches of a data tree by index ("0, 2, 4-6", "-1", "*", "even") and pass them through.
import re

import Rhino.Geometry as rg

# sample tree when nothing is wired: 4 branches of points
if not tree:
    tree = [[rg.Point3d(i, j, 0) for j in range(3 + i)] for i in range(4)]

n = len(tree)


def parse(text):
    """'0, 2, 4-6, -1' -> branch indices in the order given (duplicates dropped), plus words it didn't understand."""
    out, bad = [], []
    for tok in re.split(r"[,;\s]+", (text or "").strip().lower()):
        if not tok:
            continue
        if tok in ("*", "all"):
            idx = range(n)
        elif tok in ("even", "odd"):
            idx = range(0 if tok == "even" else 1, n, 2)
        elif re.fullmatch(r"-?\d+", tok):
            i = int(tok)
            idx = [i + n if i < 0 else i]
        elif re.fullmatch(r"-?\d+-(-?\d+)", tok):  # 4-6, -3--1
            a, b = re.fullmatch(r"(-?\d+)-(-?\d+)", tok).groups()
            a, b = int(a) + (n if int(a) < 0 else 0), int(b) + (n if int(b) < 0 else 0)
            idx = range(a, b + 1) if a <= b else range(a, b - 1, -1)
        else:
            bad.append(tok)
            continue
        for i in idx:
            if 0 <= i < n and i not in out:
                out.append(i)
            elif not 0 <= i < n:
                bad.append(str(i))
    return out, bad


def describe(branch):
    kinds = sorted({type(x).__name__ for x in branch})
    sample = ", ".join(str(x) for x in branch[:2])
    return "%d item%s, %s%s" % (len(branch), "" if len(branch) == 1 else "s", "/".join(kinds) or "empty",
                                (": " + sample[:60]) if branch else "")


chosen, bad = parse(pick)
picked = [tree[i] for i in chosen]
rest = [tree[i] for i in range(n) if i not in chosen]
if flatten:
    picked = [x for b in picked for x in b]
count = len(chosen)

lines = ["%s %d: %s" % ("✓" if i in chosen else " ", i, describe(tree[i])) for i in range(n)]
if bad:
    lines.append("ignored: %s (the tree has branches 0-%d)" % (", ".join(bad), n - 1))
    print("ignored in pick: " + ", ".join(bad))
branches = "\n".join(lines)
