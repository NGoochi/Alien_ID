"""
Drive the panel's project agent from a shell: send requests as the panel would, wait for each turn, and print what the
agent did (tool steps, replies, cost). Rhino + Grasshopper open, Alien loaded, the Alien panel open.

  python3 tests/agent/run.py "Set up a site, levels and grids for a three-storey building." "What could I build next?"
  python3 tests/agent/run.py --new "..."      start a new chat first (the project memory is kept)

Costs real model calls (the agent model in settings, default Claude Sonnet 5.5): a few cents a request.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from mcp import call  # noqa: E402

PANEL = r'''
using System.Linq; using System.Reflection;
var asm = System.AppDomain.CurrentDomain.GetAssemblies().First(a => a.GetName().Name == "Alien");
var t = asm.GetType("Alien.Ui.AlienPanel");
var live = (System.Collections.IList)t.GetField("Live", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
var agent = asm.GetType("Alien.Ai.ProjectAgent");
%s
'''


def send(message):
    body = '''if (live.Count == 0) result = "no panel";
else { t.GetMethod("OnMessage", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(live[0], new object[] { %s }); result = "sent"; }''' % json.dumps(json.dumps(message))
    return call("run_code", {"language": "csharp", "code": PANEL % body})["text"]


def state(path):
    body = '''System.IO.File.WriteAllText(%s, agent.GetMethod("StateJson").Invoke(null, null).ToString());
result = "ok";''' % json.dumps(path)
    call("run_code", {"language": "csharp", "code": PANEL % body})
    with open(path) as f:
        return json.load(f)


def main():
    args = sys.argv[1:]
    out = os.path.join(os.environ.get("TMPDIR", "/tmp"), "alien_agent_state.json")
    if args and args[0] == "--new":
        send({"type": "agentNew"})
        args = args[1:]
    for request in args:
        before = len(state(out)["entries"])
        cost0 = state(out)["cost"]
        print("\n>>> " + request)
        print(send({"type": "agentSend", "text": request}))
        t0 = time.time()
        while True:
            time.sleep(2)
            s = state(out)
            if not s["busy"] and len(s["entries"]) > before + 1:
                break
            if time.time() - t0 > 600:
                print("timed out")
                break
        for e in s["entries"][before:]:
            mark = {"user": ">>", "assistant": "<<", "tool": "  ⚙" if e.get("ok", True) else "  !", "error": "!!"}.get(e["role"], "  ")
            if e["role"] == "user":
                continue
            print(mark, e.get("text", "").replace("\n", "\n   "), "[image]" if e.get("image") else "")
        print("   (%.0f s, US$%.4f)" % (time.time() - t0, s["cost"] - cost0))
    print("\nmemory:", s["notes"] if args else state(out)["notes"])


if __name__ == "__main__":
    main()
