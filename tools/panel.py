"""Send a message to the open Alien panel as if its page sent it (window.eto.postMessage):

  python3 panel.py '{"type":"place","path":"suite/levels.py"}'
  python3 panel.py '{"type":"describe","text":"make the grid hexagonal"}'
  python3 panel.py '{"type":"dock","where":"rhino"}'      # or "grasshopper"
"""
import json
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python -I too
from mcp import call  # noqa: E402

code = """
using System.Linq; using System.Reflection;
var t = System.AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("Alien.Ui.AlienPanel")).First(x => x != null);
var live = (System.Collections.IList)t.GetField("Live", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
if (live.Count == 0) result = "no Alien panel is open";
else
{
    t.GetMethod("OnMessage", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(live[live.Count - 1], new object[] { %s });
    result = "selected: " + string.Join(",", Grasshopper.Instances.ActiveCanvas.Document.SelectedObjects().Select(o => o.NickName));
}
""" % json.dumps(sys.argv[1])
print(call("run_code", {"language": "csharp", "code": code})["text"])
