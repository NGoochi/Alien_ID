"""Save an Alien `capture` (Rhino viewport or Grasshopper canvas) as a PNG:  python3 capture.py out.png '<json args>'

  python3 capture.py canvas.png '{"target":"canvas"}'
  python3 capture.py front.png '{"view":"front","display":"Shaded","focus":["<node id>"]}'
"""
import base64
import json
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python -I too
from mcp import call  # noqa: E402

r = call("capture", json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
print(r["text"])
if r["images"]:
    with open(sys.argv[1], "wb") as f:
        f.write(base64.b64decode(r["images"][0]))
    print("saved", sys.argv[1])
