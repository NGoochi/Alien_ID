"""Send a message to the open Alien panel as if its page sent it (window.eto.postMessage), or run JavaScript in it:

  python3 panel.py '{"type":"place","path":"suite/levels.py"}'
  python3 panel.py '{"type":"describe","text":"make the grid hexagonal"}'
  python3 panel.py '{"type":"dock","where":"rhino"}'      # or "grasshopper"
  python3 panel.py --js "document.getElementById('tabAssets').click()"   # clicks, tabs, filters (page-only state)
"""
import json
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python -I too
from mcp import call  # noqa: E402

PANEL = """
using System.Linq; using System.Reflection;
var t = System.AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("Alien.Ui.AlienPanel")).First(x => x != null);
var live = (System.Collections.IList)t.GetField("Live", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
if (live.Count == 0) result = "no Alien panel is open";
else
{
    var panel = live[live.Count - 1];
    %s
}
"""

if sys.argv[1] == "--js":
    body = """var web = (Eto.Forms.WebView)t.GetField("_web", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(panel);
    web.ExecuteScriptAsync(%s);
    result = "ran";""" % json.dumps(sys.argv[2])
else:
    body = """t.GetMethod("OnMessage", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(panel, new object[] { %s });
    result = "selected: " + string.Join(",", Grasshopper.Instances.ActiveCanvas.Document.SelectedObjects().Select(o => o.NickName));""" % json.dumps(sys.argv[1])
print(call("run_code", {"language": "csharp", "code": PANEL % body})["text"])
