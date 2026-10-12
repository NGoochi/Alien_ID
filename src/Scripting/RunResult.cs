using System.Collections.Generic;

namespace Alien.Scripting
{
    /// <summary>Outcome of one script execution.</summary>
    public sealed class RunResult
    {
        public bool Success { get; set; }
        public Dictionary<string, object> Outputs { get; } = new Dictionary<string, object>();
        /// <summary>Declared outputs the script never assigned.</summary>
        public List<string> Unset { get; } = new List<string>();
        public string Stdout { get; set; } = "";
        /// <summary>Short error, e.g. "NameError: name 'x' is not defined".</summary>
        public string Error { get; set; }
        /// <summary>1-based line in the user's script, when known.</summary>
        public int? Line { get; set; }
        /// <summary>Traceback / diagnostics limited to user frames.</summary>
        public string Trace { get; set; }
        public double Ms { get; set; }
        /// <summary>The script's NODE_CONTROLS (per-input slider hints) as JSON, or null.</summary>
        public string Controls { get; set; }

        public static RunResult Fail(string error, int? line = null, string trace = null) =>
            new RunResult { Success = false, Error = error, Line = line, Trace = trace };
    }

    /// <summary>Aggregate of all iterations in one Grasshopper solution (item-access inputs can run a script many times).</summary>
    public sealed class RunRecord
    {
        public int Iterations { get; set; }
        public int Failures { get; set; }
        public double Ms { get; set; }
        public RunResult FirstError { get; set; }
        public List<string> Unset { get; set; } = new List<string>();
        public string Stdout { get; set; } = "";
        public string Status { get; set; } = "idle"; // idle | ok | error | no-script | rebuilding
        public string Note { get; set; }

        public void Add(RunResult r)
        {
            Iterations++;
            Ms += r.Ms;
            if (!r.Success)
            {
                Failures++;
                FirstError ??= r;
            }
            if (Iterations == 1) Unset = r.Unset;
            if (!string.IsNullOrEmpty(r.Stdout) && Stdout.Length < 4000)
                Stdout += r.Stdout;
        }
    }
}
