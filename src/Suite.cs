using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Alien
{
    /// <summary>The preloaded nodes: shipped inside the plugin, copied into &lt;repository&gt;/suite for the library.</summary>
    public static class Suite
    {
        private const string Prefix = "Alien.suite.";

        public static IEnumerable<string> Files =>
            Assembly.GetExecutingAssembly().GetManifestResourceNames().Where(n => n.StartsWith(Prefix)).Select(n => n.Substring(Prefix.Length));

        /// <summary>
        /// Bring &lt;repository&gt;/suite up to date without touching edits: add suite files that were never installed, and
        /// update files that still match what was installed (by hash, recorded in .alien/suite.json). Files the user
        /// edited or deleted stay as they are. Returns the number of files written.
        /// </summary>
        public static int InstallIfMissing()
        {
            var root = ScriptRepository.Root;
            if (root == null) return 0;
            var dir = Path.Combine(root, "suite");
            var recordFile = Path.Combine(root, ".alien", "suite.json");
            JObject record;
            try { record = File.Exists(recordFile) ? JObject.Parse(File.ReadAllText(recordFile)) : new JObject(); }
            catch { record = new JObject(); }
            bool fresh = !Directory.Exists(dir);
            Directory.CreateDirectory(dir);
            int written = 0;
            foreach (var name in Files)
            {
                var path = Path.Combine(dir, name);
                var shipped = Read(name);
                var shippedHash = Hash(shipped);
                var installed = record.Value<string>(name);
                if (!File.Exists(path))
                {
                    if (installed != null && !fresh) continue; // installed before, then deleted: leave it gone
                    File.WriteAllText(path, shipped);
                }
                else
                {
                    var current = Hash(File.ReadAllText(path).Replace("\r\n", "\n"));
                    if (current == shippedHash) { record[name] = shippedHash; continue; }
                    if (installed == null || current != installed) continue; // edited (or unknown): keep the user's version
                    ScriptRepository.WriteScript(path, shipped); // unedited older version: update (old one goes to history)
                }
                record[name] = shippedHash;
                written++;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(recordFile));
            File.WriteAllText(recordFile, record.ToString(Formatting.Indented));
            return written;
        }

        private static string Read(string name)
        {
            using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream(Prefix + name);
            using var r = new StreamReader(s);
            return r.ReadToEnd().Replace("\r\n", "\n");
        }

        private static string Hash(string text)
        {
            using var h = SHA1.Create();
            return BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
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
