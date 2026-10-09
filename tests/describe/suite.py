"""Run the brief's 10 test prompts through Alien's `generate` tool for each model; save results as JSON."""
import json, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp import call

PROMPTS = [
    "divide a curve into N points",
    "a voronoi panel facade on a surface",
    "flatten and regroup a tree by branch index",
    "contour a mesh at intervals",
    "offset a closed curve inwards N times",
    "random-colour breps by area",
    "number panels for fabrication",
    "attractor-point scaling of circles",
    "bounding-box dimensions as text",
    "loft between sorted curves",
]

out_dir = sys.argv[1]
models = sys.argv[2:]
os.makedirs(out_dir, exist_ok=True)
for row, model in enumerate(models):
    results = []
    for i, p in enumerate(PROMPTS):
        t = time.time()
        r = call("generate", {"prompt": p, "model": model, "x": 300 + i * 260, "y": 200 + row * 260})
        try: data = json.loads(r["text"])
        except Exception: data = {"ok": False, "error": r.get("text")}
        data["prompt"] = p
        data["wall_s"] = round(time.time() - t, 1)
        results.append(data)
        att = len(data.get("attempts", []))
        print(f"{model:32} {i+1:2} {'OK ' if data.get('ok') else 'FAIL'} att={att} ${data.get('cost',0):.4f} {data['wall_s']:6.1f}s  {p}", flush=True)
        json.dump(results, open(os.path.join(out_dir, model.replace('/', '_').replace(':', '_') + ".json"), "w"), indent=1)
    ok = sum(1 for d in results if d.get("ok"))
    print(f"== {model}: {ok}/10 ok, ${sum(d.get('cost',0) for d in results):.4f}", flush=True)
