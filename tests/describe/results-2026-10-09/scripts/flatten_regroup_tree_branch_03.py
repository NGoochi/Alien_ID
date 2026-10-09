# NODE_INPUTS: tree:tree[float] | "data tree of numbers"
# NODE_OUTPUTS: regrouped:tree | "one branch per item index, collecting that index from every input branch", flat:list | "all values flattened in order"
# Flattens a number tree and regroups its values by item index (branch k holds the k-th item of each input branch).
if not tree:
    # Sample geometry-free data so the node shows a result when unwired
    tree = [[1.0, 2.0, 3.0], [4.0, 5.0], [6.0, 7.0, 8.0, 9.0]]

# Accept a flat list as a single branch
if tree and not isinstance(tree[0], list):
    tree = [tree]

groups = {}
for branch in tree:
    for i, value in enumerate(branch):
        groups.setdefault(i, []).append(value)

regrouped = [groups[k] for k in sorted(groups)]
flat = [v for branch in tree for v in branch]
