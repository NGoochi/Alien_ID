# NODE_INPUTS: tree:tree[object] | "tree to flatten and regroup"
# NODE_OUTPUTS: flat, regrouped, branches:int | "branch count after regrouping"
# Flattens a data tree into one list and regroups the items by their index inside each original branch.
import Rhino.Geometry as rg

# sample tree: 4 branches of 3 points each, so the node shows a result unwired
if not tree:
    tree = [[rg.Point3d(i, j, 0) for j in range(3)] for i in range(4)]

flat = [item for branch in tree for item in branch]

depth = max((len(b) for b in tree), default=0)
regrouped = [[] for _ in range(depth)]
for branch in tree:
    for j, item in enumerate(branch):
        regrouped[j].append(item)

branches = len(regrouped)
