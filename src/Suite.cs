using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;

namespace Alien
{
    /// <summary>The preloaded nodes: shipped inside the plugin, copied into &lt;repository&gt;/suite for the library.</summary>
    public static class Suite
    {
        private const string Prefix = "Alien.suite.";

        public static IEnumerable<string> Files =>
            Assembly.GetExecutingAssembly().GetManifestResourceNames().Where(n => n.StartsWith(Prefix)).Select(n => n.Substring(Prefix.Length));

        /// <summary>Copy the suite into a repository that has no suite folder yet. Returns the number of files written.</summary>
        public static int InstallIfMissing()
        {
            var root = ScriptRepository.Root;
            return root == null || Directory.Exists(Path.Combine(root, "suite")) ? 0 : Install(false);
        }

        /// <summary>Copy the suite into &lt;repository&gt;/suite; existing files are kept unless overwrite (they may have been edited).</summary>
        public static int Install(bool overwrite)
        {
            var root = ScriptRepository.Root;
            if (root == null) return 0;
            var dir = Path.Combine(root, "suite");
            Directory.CreateDirectory(dir);
            int written = 0;
            foreach (var name in Files)
            {
                var path = Path.Combine(dir, name);
                if (File.Exists(path) && !overwrite) continue;
                using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream(Prefix + name);
                using var r = new StreamReader(s);
                if (File.Exists(path)) ScriptRepository.WriteScript(path, r.ReadToEnd());
                else File.WriteAllText(path, r.ReadToEnd().Replace("\r\n", "\n"));
                written++;
            }
            return written;
        }
    }
}
