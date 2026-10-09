"""Call one Alien MCP tool (Rhino + Grasshopper open, Alien loaded):  python3 mcp.py <tool> '<json args>'

  python3 mcp.py canvas '{}'
  python3 mcp.py add '{"component":"python","script":"suite/levels.py"}'
  python3 mcp.py run_code '{"language":"csharp","code":"result = 1 + 1;"}'
"""
import json
import sys
import urllib.request

URL = "http://127.0.0.1:9876/mcp"


def call(tool, args=None, timeout=900):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    if "error" in res:
        return {"isError": True, "text": json.dumps(res["error"]), "images": []}
    content = res["result"]["content"]
    return {"isError": res["result"].get("isError"),
            "text": "\n".join(c["text"] for c in content if c["type"] == "text"),
            "images": [c["data"] for c in content if c["type"] == "image"]}


if __name__ == "__main__":
    r = call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    print(("ERROR: " if r["isError"] else "") + r["text"])
