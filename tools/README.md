# Dev tools: driving Rhino from a shell

For agents and people checking Alien changes in a real Rhino. They talk to Alien's MCP server on
`127.0.0.1:9876`, so Rhino must be open with Grasshopper loaded.

| Tool | What it does |
|---|---|
| `mcp.py <tool> '<json>'` | Calls any MCP tool (`canvas`, `add`, `inspect`, `set_values`, `run_code`, `generate`, …) |
| `capture.py out.png '<json>'` | Saves a capture of the viewport or the Grasshopper canvas (`{"target":"canvas"}`) |
| `click.py <node> <dx> <dy> [double]` | Simulates a click on a node, delivered to its own mouse handlers (sliders, toggles, PANEL, text fields) |
| `panel.py '<json>'` | Sends a message to the Alien panel as if its page did (`place`, `describe`, `restore`, `dock`, `close`, `bakeAsset`, `materialAsset`, `placeAsset`, `revealAsset` with `kind` and `id`) |
| `docs_json.py [--check]` | Rewrites `docs/docs.json` (suite pins from headers, categories from `Library.cs`, MCP tools from the running server); `--check` fails on undocumented nodes or settings |
| `panel.py --js '<script>'` | Runs JavaScript in the panel page, for page-only state: `document.getElementById('tabAssets').click()` |
| `windows.swift` | macOS: lists Rhino's windows, for `screencapture -x -o -l <id>` of the Grasshopper window or the sidecar |

## The loop

1. **Build:** close Rhino completely first (Cmd+Q on macOS: closing windows is not quitting), then run
   `dotnet build -c Release` in `src/`. It copies `Alien.gha` into Grasshopper's Libraries folder. Wait until
   `pgrep Rhinoceros` is empty: building while Rhino is still quitting overwrites a loaded assembly.
   To check that the code compiles while Rhino is open, run `dotnet build -c Release -p:GH_Libraries=`, which builds
   without deploying.
2. **Launch on macOS:** run
   `open -a "Rhino 8" --args -nosplash -runscript="_-Grasshopper _Window _Show _Enter"`, then poll
   `curl -s http://127.0.0.1:9876/health` until it answers.
3. **Place nodes:** `python3 mcp.py add '{"nodes":[{"key":"a","component":"python","script":"suite/levels.py"}]}'`.
4. **Check:**
   - `inspect` for numbers.
   - `capture.py` for the canvas or viewport.
   - `screencapture` plus `windows.swift` for the panel and the full screen. `screencapture -D 2` grabs a second display.
5. **Quit for the next build:** `osascript -e 'tell application "Rhino 8" to quit saving no'`. This discards unsaved
   work: only use it on a session you opened yourself.

`run_code` runs in its own Python scope: modules that nodes imported are not in its `sys.modules`. To debug a helper
such as `alien_graphics`, insert the repository path into `sys.path` and import it there.
