using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

using Alien.Scripting;

namespace Alien
{
    /// <summary>
    /// Global settings + the user's script repository folder (lives outside the plugin).
    /// Settings: %APPDATA%/Alien/settings.json (macOS, inside Rhino: ~/Library/Application Support/Alien/settings.json).
    /// Script versions are kept in &lt;repo&gt;/.alien/history instead of .bak files next to scripts.
    /// </summary>
    public static class ScriptRepository
    {
        public const int DefaultPort = 9876;
        private const int HistoryPerScript = 20;

        private static readonly string SettingsDir =
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Alien");
        private static readonly string SettingsPath = Path.Combine(SettingsDir, "settings.json");

        private static JObject _settings;
        private static DateTime _settingsStamp;

        private static JObject Settings
        {
            get
            {
                // Re-read when the file changes, so a key pasted into settings.json works without restarting Rhino.
                var stamp = File.Exists(SettingsPath) ? File.GetLastWriteTimeUtc(SettingsPath) : DateTime.MinValue;
                if (_settings != null && stamp == _settingsStamp) return _settings;
                try { _settings = File.Exists(SettingsPath) ? JObject.Parse(File.ReadAllText(SettingsPath)) : new JObject(); }
                catch { _settings ??= new JObject(); } // half-edited file: keep what we had
                _settingsStamp = stamp;
                return _settings;
            }
        }

        public static string SettingsFile => SettingsPath;

        /// <summary>Any string setting (e.g. "openrouterKey", "model"), or null.</summary>
        public static string Setting(string key) => Settings.Value<string>(key);

        /// <summary>Set (or with null/empty, remove) a string setting and save.</summary>
        public static void SetSetting(string key, string value)
        {
            if (string.IsNullOrEmpty(value)) Settings.Remove(key);
            else Settings[key] = value;
            Save();
        }

        /// <summary>A setting of any JSON shape (e.g. "theme"), or null.</summary>
        public static JToken SettingToken(string key) => Settings[key];

        /// <summary>Set (or with null, remove) a setting of any JSON shape and save.</summary>
        public static void SetSettingToken(string key, JToken value)
        {
            if (value == null || value.Type == JTokenType.Null) Settings.Remove(key);
            else Settings[key] = value;
            Save();
        }

        /// <summary>Make sure the file exists and shows the describe-a-node keys, so people know where the key goes.</summary>
        public static void EnsureSettingsFile()
        {
            bool changed = !File.Exists(SettingsPath);
            foreach (var (key, value) in new[] { ("openrouterKey", ""), ("model", Ai.ModelChoice.DefaultModel) })
                if (Settings[key] == null) { Settings[key] = value; changed = true; }
            if (changed) Save();
        }

        private static void Save()
        {
            try
            {
                Directory.CreateDirectory(SettingsDir);
                File.WriteAllText(SettingsPath, Settings.ToString(Formatting.Indented));
                // It holds an API key: owner-only.
                if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(SettingsPath, UnixFileMode.UserRead | UnixFileMode.UserWrite);
                _settingsStamp = File.GetLastWriteTimeUtc(SettingsPath);
            }
            catch { /* settings are best-effort */ }
        }

        public static int Port => Settings.Value<int?>("port") ?? DefaultPort;

        /// <summary>Optional editor command for "Edit script" (e.g. "code", "cursor"). Empty = auto-detect.</summary>
        public static string Editor => Settings.Value<string>("editor");

        /// <summary>Repository root, or null if not configured / missing on disk.</summary>
        public static string Root
        {
            get
            {
                var r = Settings.Value<string>("repository");
                return !string.IsNullOrWhiteSpace(r) && Directory.Exists(r) ? r : null;
            }
        }

        public static bool IsConfigured => Root != null;

        /// <summary>
        /// First run, no script folder chosen yet: use Documents/Alien scripts, so nothing asks before the first node
        /// (the installers set the same folder). A folder that was chosen but is missing (a drive unplugged) is left
        /// for the user to fix. Right-click a node > Script repository changes it.
        /// </summary>
        public static void EnsureDefaultRoot()
        {
            if (!string.IsNullOrWhiteSpace(Settings.Value<string>("repository"))) return;
            try { SetRoot(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Alien scripts")); }
            catch { /* unwritable Documents: the first node asks instead */ }
        }

        public static void SetRoot(string path)
        {
            var full = Path.GetFullPath(path);
            Directory.CreateDirectory(full);
            Settings["repository"] = full;
            Save();
        }

        /// <summary>Store paths relative to the repo when inside it, so .gh files survive moving the repo.</summary>
        public static string ToStored(string fullPath)
        {
            if (string.IsNullOrEmpty(fullPath)) return "";
            var root = Root;
            if (root != null)
            {
                var rel = Path.GetRelativePath(root, fullPath);
                if (!rel.StartsWith("..") && !Path.IsPathRooted(rel)) return "repo:" + rel.Replace('\\', '/');
            }
            return fullPath;
        }

        public static string FromStored(string stored)
        {
            if (string.IsNullOrEmpty(stored)) return null;
            if (!stored.StartsWith("repo:", StringComparison.Ordinal)) return stored;
            var root = Root;
            return root == null ? null : Path.GetFullPath(Path.Combine(root, stored.Substring(5)));
        }

        /// <summary>Resolve a user/agent-supplied script reference (absolute, or relative to the repo).</summary>
        public static string Resolve(string pathOrName)
        {
            if (string.IsNullOrWhiteSpace(pathOrName)) return null;
            if (Path.IsPathRooted(pathOrName)) return Path.GetFullPath(pathOrName);
            var root = Root ?? throw new InvalidOperationException("Script repository not set. Use the `repo` tool (set) or pick one in Grasshopper.");
            return Path.GetFullPath(Path.Combine(root, pathOrName));
        }

        public static IEnumerable<string> ListScripts()
        {
            var root = Root;
            if (root == null) return Enumerable.Empty<string>();
            return Directory.EnumerateFiles(root, "*.*", SearchOption.AllDirectories)
                .Where(f => ScriptLanguages.FromPath(f) != ScriptLanguage.None)
                .Where(f => !f.Contains(Path.DirectorySeparatorChar + ".alien" + Path.DirectorySeparatorChar))
                .Select(f => Path.GetRelativePath(root, f).Replace('\\', '/'))
                .OrderBy(f => f, StringComparer.OrdinalIgnoreCase);
        }

        /// <summary>Create a new script file with a unique name. Returns its full path.</summary>
        public static string CreateScript(string name, ScriptLanguage language, string code)
        {
            var root = Root ?? throw new InvalidOperationException("Script repository not set.");
            var ext = ScriptLanguages.Extension(language);
            name = string.IsNullOrWhiteSpace(name) ? "alien" : name.Trim();
            if (name.EndsWith(ext, StringComparison.OrdinalIgnoreCase)) name = name.Substring(0, name.Length - ext.Length);
            name = SanitizeRelative(name);

            var path = Path.Combine(root, name + ext);
            for (int i = 2; File.Exists(path); i++)
                path = Path.Combine(root, $"{name}_{i:00}{ext}");

            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, string.IsNullOrEmpty(code) ? Templates.For(language) : Normalise(code), new UTF8Encoding(false));
            return path;
        }

        /// <summary>Write a script, keeping the previous version in .alien/history.</summary>
        public static void WriteScript(string path, string code)
        {
            if (File.Exists(path)) Snapshot(path);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, Normalise(code), new UTF8Encoding(false));
        }

        private static void Snapshot(string path)
        {
            try
            {
                var root = Root ?? Path.GetDirectoryName(path);
                var histDir = Path.Combine(root, ".alien", "history");
                Directory.CreateDirectory(histDir);
                var stem = Path.GetFileNameWithoutExtension(path);
                var ext = Path.GetExtension(path);
                File.Copy(path, Path.Combine(histDir, $"{stem}.{DateTime.Now:yyyyMMdd-HHmmss-fff}{ext}"), overwrite: true);

                var old = Directory.GetFiles(histDir, stem + ".*" + ext)
                    .OrderByDescending(f => f, StringComparer.Ordinal)
                    .Skip(HistoryPerScript);
                foreach (var f in old) File.Delete(f);
            }
            catch { /* history is best-effort; never block a write */ }
        }

        private static string Normalise(string code) => (code ?? "").Replace("\r\n", "\n");

        private static string SanitizeRelative(string name)
        {
            var invalid = Path.GetInvalidFileNameChars().Where(c => c != '/' && c != '\\').ToArray();
            var cleaned = new string(name.Select(c => invalid.Contains(c) ? '_' : c).ToArray());
            cleaned = cleaned.Replace('\\', '/').TrimStart('/');
            if (cleaned.Split('/').Any(seg => seg == "..")) throw new ArgumentException("Script name may not contain '..'");
            return cleaned.Replace('/', Path.DirectorySeparatorChar);
        }
    }
}
