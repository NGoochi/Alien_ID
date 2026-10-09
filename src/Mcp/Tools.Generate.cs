using System;

using Newtonsoft.Json.Linq;

using Alien.Ai;
using Alien.Scripting;

namespace Alien.Mcp
{
    public static partial class Tools
    {
        private static ToolResult Generate(JObject a)
        {
            var prompt = Req(a, "prompt");
            var id = a.Value<string>("id");
            var node = UiThread.Run(() =>
            {
                if (!string.IsNullOrWhiteSpace(id)) return RequireAlien(id);
                var doc = RequireDoc(true);
                if (!ScriptRepository.IsConfigured)
                    throw new ToolException("Script repository not set. Ask the user for a folder, then call repo(set=...).");
                var n = new AlienComponent { SuppressRepositoryPrompt = true };
                n.CreateAttributes();
                n.Attributes.Pivot = Placement(doc, a, false);
                doc.AddObject(n, false);
                return n;
            });

            var lang = ScriptLanguages.Parse(a.Value<string>("language") ?? "python");
            if (lang == ScriptLanguage.None) throw new ToolException("language must be 'python' or 'csharp'");
            try
            {
                GenerateResult r;
                // A run takes seconds to minutes, mostly waiting on the model: don't block the agent's other calls.
                using (UiThread.Yield())
                    r = Generator.Run(node, prompt, lang, a.Value<string>("model"), a.Value<int?>("attempts") ?? Generator.DefaultAttempts)
                        .GetAwaiter().GetResult();
                var json = r.ToJson();
                json["id"] = UiThread.Run(() => Describe.ShortId(node));
                if (node.ScriptPath != null) json["script"] = ScriptRepository.ToStored(node.ScriptPath).Replace("repo:", "");
                return ToolResult.Json(json);
            }
            catch (InvalidOperationException ex) { throw new ToolException(ex.Message); }
        }
    }
}
