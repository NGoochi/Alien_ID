using System;
using System.Collections.Generic;
using System.Linq;

namespace Alien.Ai
{
    /// <summary>One describe-a-node request on a node and how it went. Kept in the .gh file, shown in the Alien panel.</summary>
    public sealed class TranscriptEntry
    {
        public string Request { get; set; }
        public DateTime When { get; set; } = DateTime.Now;
        /// <summary>running | ok | failed | stopped</summary>
        public string State { get; set; } = "running";
        public string Model { get; set; }
        public int Attempts { get; set; }
        public double Seconds { get; set; }
        public double Cost { get; set; }
        public string Error { get; set; }
        /// <summary>One line per attempt: "1 · error — NameError: … (line 12)".</summary>
        public List<string> Notes { get; set; } = new List<string>();

        public void Complete(GenerateResult r)
        {
            Model = r.Model;
            Attempts = r.Attempts.Count;
            Seconds = Math.Round(r.Attempts.Sum(a => a.Seconds), 1);
            Cost = r.Cost;
            Notes = r.Attempts.Select(a => $"{a.N} · {a.Status}" + (a.Note != null ? " — " + a.Note : "")).ToList();
            State = r.Ok ? "ok" : "failed";
            Error = r.Ok ? null : r.Error;
        }

        public void Fail(string state, string error)
        {
            State = state;
            Error = error;
        }

        public string Summary => State switch
        {
            "ok" => $"works · {Attempts} attempt{(Attempts == 1 ? "" : "s")} · {Seconds:0} s" + (Cost > 0 ? $" · ${Cost:0.0000}" : ""),
            "running" => "working…",
            "stopped" => "stopped",
            _ => "failed: " + Error,
        };
    }
}
