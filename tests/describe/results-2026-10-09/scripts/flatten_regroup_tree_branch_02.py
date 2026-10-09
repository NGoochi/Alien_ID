# NODE_INPUTS: data:tree[object] | "Input tree; empty input uses sample data"
# NODE_OUTPUTS: flat, regrouped:tree[object]
# Flattens the input branches, then groups items by their index within each branch.
import Rhino.Geometry as rg

if data is None or data == []:
    # Sample data keeps the node useful before an input is wired.
    branches = [[1, 2, 3], [4, 5], [6, 7, 8, 9]]
else:
    branches = data if isinstance(data, list) else [data]
    branches = [branch if isinstance(branch, list) else [branch] for branch in branches]

flat = [item for branch in branches for item in branch]

max_length = max((len(branch) for branch in branches), default=0)
regrouped = [
    [branch[index] for branch in branches if index < len(branch)]
    for index in range(max_length)
]
