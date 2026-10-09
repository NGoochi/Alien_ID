"""Tiny Alien MCP client: mcp.py <tool> '<json args>'  → prints the tool's text result."""
import json, sys, urllib.request

def call(tool, args, timeout=900):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    req = urllib.request.Request("http://127.0.0.1:9876/mcp", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    if "error" in res:
        return {"rpc_error": res["error"]}
    out = res["result"]
    texts = [c["text"] for c in out["content"] if c["type"] == "text"]
    return {"isError": out.get("isError"), "text": "\n".join(texts)}

if __name__ == "__main__":
    r = call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    if r.get("isError"): print("ERROR:", end=" ")
    print(r.get("text", r))
