using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

using Rhino.Runtime.Code;
using Rhino.Runtime.Code.Diagnostics;
using Rhino.Runtime.Code.Execution;
using Rhino.Runtime.Code.Languages;

using RhinoCodePlatform.Rhino3D;

namespace Alien.Scripting
{
    /// <summary>
    /// Executes scripts through Rhino 8's RhinoCode (CPython 3 via pythonnet, C# via Roslyn, IronPython 2).
    /// One cached Code per file keeps RhinoCode's compile cache warm. UI thread only.
    ///
    /// Two source adjustments are applied to the text handed to RhinoCode (never to the user's file), both
    /// appended after the user's code so line numbers are unchanged:
    ///  • Python epilogue: tuples/sets/generators/numpy -> lists for outputs, and which outputs were never assigned.
    ///  • C#: outputs the script declares itself (`var points = ...`) or pass-through pins are written to the context
    ///    by us; RhinoCode only pre-declares the rest (it would otherwise emit a duplicate local -> CS0128).
    /// </summary>
    public static class ScriptRunner
    {
        private const string UnsetVar = "__alien_unset__";
        private const string ControlsVar = "__alien_controls__";  // NODE_CONTROLS as JSON (see AGENT_GUIDE "Inputs and outputs")
        private static readonly Dictionary<string, Code> Codes = new Dictionary<string, Code>(StringComparer.OrdinalIgnoreCase);
        private static Code _purge;
        private static DateTime _lastPurge = DateTime.MinValue;

        public static RunResult Run(ScriptLanguage lang, string path, string source, ScriptHeader header, IDictionary<string, object> inputs)
        {
            var sw = Stopwatch.StartNew();
            var stdout = new RunContextStream();
            var stderr = new RunContextStream();
            var result = new RunResult();
            int userLines = CountLines(source);
            try
            {
                var language = GetLanguage(lang);
                var key = Path.GetFullPath(path) + "|" + lang;
                if (!Codes.TryGetValue(key, out var code))
                    Codes[key] = code = language.CreateCode(new Uri(Path.GetFullPath(path)));

                bool python = lang != ScriptLanguage.CSharp;
                var outputs = header.Outputs.Select(o => o.Name).ToList();
                string text;
                List<string> declaredOutputs;
                if (python)
                {
                    text = source + PythonEpilogue(outputs);
                    declaredOutputs = outputs.Concat(new[] { UnsetVar, ControlsVar }).ToList();
                }
                else
                {
                    var selfWritten = CSharpSelfDeclared(source, outputs, header.Inputs.Select(i => i.Name));
                    text = CSharpWithOutputWrites(source, selfWritten);
                    declaredOutputs = outputs.Where(o => !selfWritten.Contains(o)).ToList();
                }

                if (!string.Equals((string)code.Text, text, StringComparison.Ordinal)) code.Text.Set(text);
                code.Inputs.Set(header.Inputs.Select(d => new Param(d.Name, InputType(d, python))).ToList());
                code.Outputs.Set(declaredOutputs.Select(n => new Param(n)).ToList());

                if (lang == ScriptLanguage.Python3 && source.Contains("import ")) PurgeLocalModules(path);

                // Fresh context every run: `# flag:` lines are merged into Options with Dictionary.Add.
                var ctx = new RunContext("Alien") { OutputStream = stdout, ErrorStream = stderr };
                foreach (var d in header.Inputs)
                    ctx.Inputs.Set(d.Name, Coerce(inputs.TryGetValue(d.Name, out var v) ? v : null, d, python));

                if (!code.TryBuild(ctx, out Diagnosis diagnosis))
                {
                    FromDiagnosis(result, diagnosis);
                }
                else
                {
                    code.Run(ctx);
                    foreach (var name in outputs)
                    {
                        ctx.Outputs.TryGet(name, out object value);
                        result.Outputs[name] = Normalize(value);
                    }
                    if (python && ctx.Outputs.TryGet(ControlsVar, out object controls) && controls is string json)
                        result.Controls = json;
                    if (python && ctx.Outputs.TryGet(UnsetVar, out object unset) && unset is IEnumerable names)
                        result.Unset.AddRange(names.Cast<object>().Select(n => n?.ToString()));
                    else if (!python)
                        result.Unset.AddRange(outputs.Where(o => result.Outputs[o] == null));
                    result.Success = true;
                }
            }
            catch (ExecuteException ex)
            {
                if (ex.InnerException is CompileException ce) FromDiagnosis(result, ce.Diagnosis);
                else FromExecute(result, ex, path);
            }
            catch (Exception ex)
            {
                result.Error = $"{ex.GetType().Name}: {ex.Message}";
            }

            if (result.Line.HasValue && result.Line > userLines)
            {
                result.Error += " (in Alien's output handling — check output values)";
                result.Line = null;
            }
            result.Stdout = stdout.GetContents() ?? "";
            if (!result.Success && string.IsNullOrEmpty(result.Trace))
            {
                var err = stderr.GetContents();
                if (!string.IsNullOrWhiteSpace(err)) result.Trace = Shorten(err, path);
            }
            result.Ms = sw.Elapsed.TotalMilliseconds;
            return result;
        }

        /// <summary>Throwaway snippet for the run_code tool. Assign `result` to return a value.</summary>
        public static RunResult RunSnippet(ScriptLanguage lang, string source)
        {
            var dir = Path.Combine(ScriptRepository.Root ?? Path.GetTempPath(), ".alien", "scratch");
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, "snippet" + ScriptLanguages.Extension(lang));
            File.WriteAllText(path, source);
            var header = new ScriptHeader(null, new[] { new ParamDef("result", "object", ParamAccess.Item, "", null, null, null, null, null) }, null);
            var r = Run(lang, path, source, header, new Dictionary<string, object>());
            r.Unset.Clear();
            return r;
        }

        // ── Languages ───────────────────────────────────────────────

        private static ILanguage GetLanguage(ScriptLanguage lang)
        {
            var spec = lang switch
            {
                ScriptLanguage.CSharp => LanguageSpec.CSharp,
                ScriptLanguage.Python2 => LanguageSpec.IronPython2,
                ScriptLanguage.Python3 => LanguageSpec.Python3,
                _ => throw new InvalidOperationException("Unsupported script type (use .py or .cs).")
            };
            var language = RhinoCode.Languages.QueryLatest(spec);
            if (language == null || !language.Status.IsComplete)
            {
                // Registers loaders (idempotent), initialises the runtime (first Python use unpacks it), blocks until ready.
                Registrar.StartScriptingLanguages(new ProgressStatusResponder(), spec);
                language = RhinoCode.Languages.QueryLatest(spec);
            }
            if (language == null || !language.Status.IsReady)
                throw new InvalidOperationException($"{lang} runtime is not available: {language?.Status.Progress.Message}");
            return language;
        }

        // ── Inputs ──────────────────────────────────────────────────

        /// <summary>Python gets untyped values (None stays None). C# gets typed locals.</summary>
        private static Type InputType(ParamDef d, bool python)
        {
            var item = python ? typeof(object) : TypeMap.ClrType(d.Type);
            return d.Access switch
            {
                ParamAccess.List => typeof(List<>).MakeGenericType(item),
                ParamAccess.Tree => typeof(List<>).MakeGenericType(typeof(List<>).MakeGenericType(item)),
                _ => item
            };
        }

        private static object Coerce(object value, ParamDef d, bool python)
        {
            var item = python ? typeof(object) : TypeMap.ClrType(d.Type);
            switch (d.Access)
            {
                case ParamAccess.List:
                    return TypedList(value, item);
                case ParamAccess.Tree:
                    var branches = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(typeof(List<>).MakeGenericType(item)));
                    if (value is IEnumerable outer && value is not string)
                        foreach (var b in outer) branches.Add(TypedList(b, item));
                    return branches;
                default:
                    return python ? value : CoerceItem(value, item);
            }
        }

        private static IList TypedList(object value, Type item)
        {
            var list = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(item));
            if (value is IEnumerable e && value is not string)
                foreach (var v in e) list.Add(CoerceItem(v, item));
            else if (value != null)
                list.Add(CoerceItem(value, item));
            return list;
        }

        private static object CoerceItem(object v, Type t)
        {
            if (t == typeof(object)) return v;
            if (v == null) return t.IsValueType ? Activator.CreateInstance(t) : null;
            if (t.IsInstanceOfType(v)) return v;
            try
            {
                if (v is IConvertible && typeof(IConvertible).IsAssignableFrom(t))
                    return Convert.ChangeType(v, t, CultureInfo.InvariantCulture);
                if (t == typeof(Rhino.Geometry.Curve) && v is Rhino.Geometry.Line ln) return new Rhino.Geometry.LineCurve(ln);
                if (t == typeof(Rhino.Geometry.Curve) && v is Rhino.Geometry.Polyline pl) return new Rhino.Geometry.PolylineCurve(pl);
                if (t == typeof(Rhino.Geometry.Point3d) && v is Rhino.Geometry.Point pt) return pt.Location;
            }
            catch { }
            return t.IsValueType ? Activator.CreateInstance(t) : null;
        }

        // ── Python helpers ──────────────────────────────────────────

        private static string PythonEpilogue(IList<string> outputs)
        {
            var names = "(" + string.Join(", ", outputs.Select(o => $"'{o}'")) + (outputs.Count == 1 ? ",)" : ")");
            var sb = new StringBuilder();
            sb.Append("\n\n# ---- appended by Alien at run time (not in your file) ----\n");
            sb.Append("def __alien_fix__(v, d=0):\n");
            sb.Append("    t = type(v)\n");
            sb.Append("    if d < 8:\n");
            sb.Append("        if t in (tuple, set, frozenset) or t.__name__ in ('range', 'generator', 'map', 'filter', 'zip', 'dict_values', 'dict_keys'):\n");
            sb.Append("            v = list(v)\n");
            sb.Append("        elif hasattr(v, 'tolist') and hasattr(v, 'ndim'):\n");
            sb.Append("            v = v.tolist()\n");
            sb.Append("        if type(v) is list:\n");
            sb.Append("            return [__alien_fix__(x, d + 1) for x in v]\n");
            sb.Append("    return v\n");
            sb.Append($"{UnsetVar} = [__n for __n in {names} if __n not in globals()]\n");
            sb.Append($"for __n in {names}:\n");
            sb.Append("    if __n in globals():\n");
            sb.Append("        globals()[__n] = __alien_fix__(globals()[__n])\n");
            var controls = $"\n{ControlsVar} = None\n" +
                           "if isinstance(globals().get('NODE_CONTROLS'), dict):\n" +
                           "    try:\n" +
                           "        import json as __alien_json__\n" +
                           $"        {ControlsVar} = __alien_json__.dumps(NODE_CONTROLS, default=str)\n" +
                           "    except Exception:\n" +
                           "        pass\n";
            return (outputs.Count == 0 ? $"\n{UnsetVar} = []\n" : sb.ToString()) + controls;
        }

        /// <summary>Drop cached modules loaded from the script's folder or the repository, so edited helpers reload.</summary>
        private static void PurgeLocalModules(string scriptPath)
        {
            try
            {
                var dirs = new[] { Path.GetDirectoryName(Path.GetFullPath(scriptPath)), ScriptRepository.Root }
                    .Where(r => r != null).Distinct(StringComparer.OrdinalIgnoreCase).ToList();

                // Only purge when a sibling .py changed since the last purge — keeps warm solves cheap.
                var newest = dirs.SelectMany(d => Directory.EnumerateFiles(d, "*.py"))
                    .Select(File.GetLastWriteTimeUtc).DefaultIfEmpty(DateTime.MinValue).Max();
                if (newest <= _lastPurge) return;
                _lastPurge = DateTime.UtcNow;

                var roots = dirs.Select(r => r.Replace('\\', '/').ToLowerInvariant()).ToList();
                if (_purge == null)
                {
                    _purge = GetLanguage(ScriptLanguage.Python3).CreateCode(
                        "import sys\n" +
                        "for __k, __m in list(sys.modules.items()):\n" +
                        "    __f = (getattr(__m, '__file__', None) or '').replace('\\\\', '/').lower()\n" +
                        "    if __f and any(__f.startswith(__r) for __r in roots):\n" +
                        "        del sys.modules[__k]\n");
                    _purge.Inputs.Set(new[] { new Param("roots", typeof(List<object>)) });
                }
                var ctx = new RunContext("AlienPurge");
                ctx.Inputs.Set("roots", roots.Cast<object>().ToList());
                if (_purge.TryBuild(ctx)) _purge.Run(ctx);
            }
            catch { /* best effort */ }
        }

        // ── C# helpers ──────────────────────────────────────────────

        /// <summary>Outputs the script declares itself at top level, plus pass-through pins (input name == output name).</summary>
        private static HashSet<string> CSharpSelfDeclared(string source, IEnumerable<string> outputs, IEnumerable<string> inputs)
        {
            var root = CSharpSyntaxTree.ParseText(source, new CSharpParseOptions(LanguageVersion.CSharp9)).GetCompilationUnitRoot();
            var declared = new HashSet<string>(root.Members.OfType<GlobalStatementSyntax>()
                .Select(g => g.Statement).OfType<LocalDeclarationStatementSyntax>()
                .SelectMany(d => d.Declaration.Variables.Select(v => v.Identifier.Text)));
            declared.UnionWith(inputs);
            return new HashSet<string>(outputs.Where(declared.Contains));
        }

        /// <summary>Append `__context__.Outputs["x"] = x;` right after the last top-level statement.</summary>
        private static string CSharpWithOutputWrites(string source, HashSet<string> names)
        {
            if (names.Count == 0) return source;
            var root = CSharpSyntaxTree.ParseText(source, new CSharpParseOptions(LanguageVersion.CSharp9)).GetCompilationUnitRoot();
            var last = root.Members.OfType<GlobalStatementSyntax>().LastOrDefault();
            int at = last?.Span.End ?? source.Length;
            var writes = string.Concat(names.Select(n => $" __context__.Outputs[\"{n}\"] = {n};"));
            return source.Substring(0, at) + writes + source.Substring(at);
        }

        // ── Results ─────────────────────────────────────────────────

        private static object Normalize(object v)
        {
            if (v == null || v is string || v is IDictionary) return v;
            if (v.GetType().FullName == "Python.Runtime.PyObject") return v.ToString(); // unconvertible Python object
            if (v is IList list && v.GetType().IsGenericType && v.GetType().GetGenericArguments()[0] == typeof(object))
                return list.Cast<object>().Select(Normalize).ToList();
            return v;
        }

        private static void FromDiagnosis(RunResult r, Diagnosis diagnosis)
        {
            var errors = diagnosis.Where(x => x.Severity == DiagnosticSeverity.Error).ToList();
            var d = errors.FirstOrDefault() ?? diagnosis.FirstOrDefault();
            r.Success = false;
            r.Error = d?.Message ?? "Compile error";
            if (d != null && d.HasReference) r.Line = d.Reference.Position.LineNumber;
            if (errors.Count > 1)
                r.Trace = string.Join("\n", errors.Take(8).Select(e =>
                    (e.HasReference ? $"line {e.Reference.Position.LineNumber}: " : "") + e.Message));
        }

        private static void FromExecute(RunResult r, ExecuteException ex, string path)
        {
            r.Success = false;
            var line = ex.Position?.LineNumber ?? 0;
            if (line > 0) r.Line = line;
            var trace = ex.StackTrace ?? "";
            var lines = trace.Split('\n').Select(l => l.TrimEnd()).Where(l => l.Trim().Length > 0).ToArray();
            r.Error = lines.Length == 0 ? ex.Message
                : lines[0].TrimStart().StartsWith("Traceback") ? lines[^1].Trim() : ex.Message;
            r.Trace = Shorten(trace, path);
        }

        /// <summary>Turn file:/// URIs into bare file names and cap length — tracebacks go to agents.</summary>
        private static string Shorten(string trace, string path)
        {
            if (string.IsNullOrEmpty(trace)) return trace;
            var name = Path.GetFileName(path);
            trace = Regex.Replace(trace, @"file:///[^""\s]+", m => Path.GetFileName(Uri.UnescapeDataString(m.Value)));
            if (!string.IsNullOrEmpty(name)) trace = trace.Replace(Path.GetFullPath(path), name);
            return trace.Length > 2000 ? "..." + trace.Substring(trace.Length - 2000) : trace;
        }

        private static int CountLines(string s) => string.IsNullOrEmpty(s) ? 0 : s.Split('\n').Length;
    }
}
