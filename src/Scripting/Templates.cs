using System;
using System.IO;
using System.Text.RegularExpressions;

namespace Alien.Scripting
{
    public enum ScriptLanguage { None, Python3, Python2, CSharp }

    public static class ScriptLanguages
    {
        private static readonly Regex Py2Shebang = new Regex(@"^\s*#!\s*python\s*2", RegexOptions.IgnoreCase | RegexOptions.Compiled);

        /// <summary>Extension decides the language; `#! python 2` opts a .py file into legacy IronPython.</summary>
        public static ScriptLanguage Detect(string path, string source)
        {
            var lang = FromPath(path);
            if (lang == ScriptLanguage.Python3 && source != null)
            {
                var firstLine = source.Split('\n')[0];
                if (Py2Shebang.IsMatch(firstLine)) return ScriptLanguage.Python2;
            }
            return lang;
        }

        public static ScriptLanguage FromPath(string path)
        {
            var ext = Path.GetExtension(path ?? "").ToLowerInvariant();
            return ext switch
            {
                ".py" => ScriptLanguage.Python3,
                ".cs" => ScriptLanguage.CSharp,
                _ => ScriptLanguage.None
            };
        }

        public static ScriptLanguage Parse(string s) => (s ?? "").Trim().ToLowerInvariant() switch
        {
            "python" or "python3" or "py" or "py3" => ScriptLanguage.Python3,
            "csharp" or "c#" or "cs" => ScriptLanguage.CSharp,
            "python2" or "ironpython" or "py2" => ScriptLanguage.Python2,
            _ => ScriptLanguage.None
        };

        public static string Extension(ScriptLanguage lang) => lang == ScriptLanguage.CSharp ? ".cs" : ".py";

        public static string Tag(ScriptLanguage lang) => lang switch
        {
            ScriptLanguage.Python3 => "PY",
            ScriptLanguage.Python2 => "PY2",
            ScriptLanguage.CSharp => "C#",
            _ => "--"
        };
    }

    public static class Templates
    {
        public static string For(ScriptLanguage lang) => lang == ScriptLanguage.CSharp ? CSharp : Python;

        public const string Python =
@"#! python 3
# NODE_INPUTS: origin:Point3d, count:int | ""Number of points"" min=1 max=100 default=10, spacing:float | ""Gap between points"" min=0.1 max=10 default=1
# NODE_OUTPUTS: points, log
import Rhino.Geometry as rg

# Unwired inputs arrive as their header default (or None / [] when there is none).
origin = origin or rg.Point3d.Origin

points = [origin + rg.Vector3d(i * spacing, 0, 0) for i in range(count)]
log = f""{len(points)} points at spacing {spacing}""
";

        public const string CSharp =
@"// NODE_INPUTS: origin:Point3d, count:int | ""Number of points"" min=1 max=100 default=10, spacing:float | ""Gap between points"" min=0.1 max=10 default=1
// NODE_OUTPUTS: points, log
using System.Collections.Generic;
using Rhino.Geometry;

// Inputs are typed variables. Unwired value types get their header default (or default(T)).
var points = new List<Point3d>();
for (int i = 0; i < count; i++)
    points.Add(origin + new Vector3d(i * spacing, 0, 0));

var log = $""{points.Count} points at spacing {spacing}"";
";
    }
}
