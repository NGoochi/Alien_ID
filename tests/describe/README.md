# Describe-a-node test

The brief's gate for describe-a-node: 8 of 10 test prompts must give a working node within 4 attempts on the cheap
default model. `suite.py` runs the 10 prompts through Alien's MCP `generate` tool (the same retry loop as the Describe
panel) and saves one JSON file per model.

```
# Rhino + Grasshopper open, Alien loaded, a script repository set
python3 suite.py results-YYYY-MM-DD anthropic/claude-haiku-5.5 openai/gpt-6-luna
```

"Working" here means the node ran without error, assigned every output, and produced non-empty output. Whether it did
*what was asked* still needs a person to read the scripts.

## 2026-10-09: gate passed

| Model | Working | First try | Avg attempts | Cost / node | Avg time |
|---|---|---|---|---|---|
| anthropic/claude-haiku-5.5 (now default) | 10/10 | 10/10 | 1.0 | US$0.0009 | 8 s |
| openai/gpt-6-luna | 10/10 | 9/10 | 1.1 | US$0.0004 | 7 s |
| deepseek/deepseek-v4.1-flash | 10/10 | 7/10 | 1.3 | US$0.0032 | 14 s |
| qwen/qwen3.8-flash | — | — | — | — | OpenRouter "provider returned error"; run stopped |

Every failed first attempt (4 of 30) was fixed by the loop: a missing `import`, a `None` in arithmetic, an invented
`rg.CircleCurve`, an undefined `ColorFromHSV`.

Changes after this run: the default model became Claude Haiku 5.5; the system prompt now asks for RhinoCommon's
built-in methods (DeepSeek had hand-written a 100-line mesh contourer instead of calling `Mesh.CreateContourCurves`;
after the change it wrote 37 lines using the built-in, on the first try).

## 2026-10-12: rerun after adding `# CATEGORY:` to the file layout

anthropic/claude-haiku-5.5: 10/10 working, 10/10 first try, US$0.0011 per node, 9.6 s average. Every script carried a
CATEGORY line (4 Geometry, 3 Massing & objects, 1 Data & lists, 1 Utilities, 1 Analysis). Two picks were loose
("attractor-point scaling of circles" as Massing & objects), so the prompt now lists each category's hint.

## 2026-10-13: rerun after adding the document's units to the request

The request now says which units the Rhino document uses (a millimetre document had produced a 5 mm "5 m" polygon).
Also new since the last run: the unset-output check works again (RhinoCode now pre-sets outputs to None).
anthropic/claude-haiku-5.5: 10/10 working, 8/10 first try (a `None` passed to `int()`, a slice on a .NET array; both
fixed on attempt 2), US$0.0018 per node, 14 s average (the voronoi first attempt took 41 s).

`results-2026-10-09/scripts/` holds the generated scripts. Files without a suffix are DeepSeek's, `_02` is GPT-6
Luna's and `_03` is Haiku's; the rest are from later spot checks.
