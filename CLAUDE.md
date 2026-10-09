@AGENTS.md

## Claude Code specifics

- Connect to a running Rhino: `claude mcp add --transport http alien http://127.0.0.1:9876/mcp`. The `docs` tool serves
  docs/AGENT_GUIDE.md.
- To look at the Rhino UI, use `tools/capture.py` (canvas or viewport). On macOS, `screencapture -x -o -l <id>` with
  ids from `swift tools/windows.swift` captures the panel and dialogs. It needs Screen Recording permission for the
  terminal.
