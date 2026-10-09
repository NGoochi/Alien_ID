using System;
using System.Diagnostics;
using System.IO;
using System.Linq;

using Rhino;

using Alien.Scripting;

namespace Alien
{
    /// <summary>Canvas-side dialogs and shell helpers (all UI thread).</summary>
    internal static class AlienUi
    {
        private static bool _prompting;

        public static bool PromptForRepository()
        {
            if (_prompting) return ScriptRepository.IsConfigured;
            _prompting = true;
            try
            {
                var dlg = new Eto.Forms.SelectFolderDialog
                {
                    Title = "Alien — choose your script repository folder (scripts are created and found here)"
                };
                if (ScriptRepository.Root != null) dlg.Directory = ScriptRepository.Root;
                if (dlg.ShowDialog(Rhino.UI.RhinoEtoApp.MainWindow) != Eto.Forms.DialogResult.Ok) return ScriptRepository.IsConfigured;
                ScriptRepository.SetRoot(dlg.Directory);
                RhinoApp.WriteLine($"Alien: script repository set to {ScriptRepository.Root}");
                return true;
            }
            finally { _prompting = false; }
        }

        public static void NewScript(AlienComponent node, ScriptLanguage language)
        {
            if (!ScriptRepository.IsConfigured && !PromptForRepository()) return;
            var suggested = node.ScriptPath != null ? Path.GetFileNameWithoutExtension(node.ScriptPath) + "_2" : "alien";
            if (!Rhino.UI.Dialogs.ShowEditBox("New Alien script",
                    $"Name for the new {(language == ScriptLanguage.CSharp ? "C#" : "Python")} script (created in the script repository):",
                    suggested, false, out var name) || string.IsNullOrWhiteSpace(name))
                return;
            var path = ScriptRepository.CreateScript(name, language, null);
            node.SetScript(path);
        }

        public static void OpenScript(AlienComponent node)
        {
            var dlg = new Eto.Forms.OpenFileDialog { Title = "Alien — open script" };
            dlg.Filters.Add(new Eto.Forms.FileFilter("Scripts", ".py", ".cs"));
            dlg.Filters.Add(new Eto.Forms.FileFilter("All files", ".*"));
            var dir = node.ScriptPath != null ? Path.GetDirectoryName(node.ScriptPath) : ScriptRepository.Root;
            if (dir != null && Directory.Exists(dir)) dlg.Directory = new Uri(dir);
            if (dlg.ShowDialog(Rhino.UI.RhinoEtoApp.MainWindow) == Eto.Forms.DialogResult.Ok)
                node.SetScript(dlg.FileName);
        }

        /// <summary>Open in a text editor — never the OS default action (on Windows that may *run* a .py).</summary>
        public static void OpenInEditor(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return;
            try
            {
                var editor = ScriptRepository.Editor;
                if (string.IsNullOrWhiteSpace(editor)) editor = FindOnPath("code") ?? FindOnPath("cursor");

                if (!string.IsNullOrWhiteSpace(editor))
                    Start(ResolveCommand(editor), $"\"{path}\"");
                else if (OperatingSystem.IsMacOS())
                    Start("open", $"-t \"{path}\"");
                else
                    Start("notepad.exe", $"\"{path}\"");
            }
            catch (Exception ex) { RhinoApp.WriteLine($"Alien: could not open editor: {ex.Message}"); }
        }

        /// <summary>Open settings.json (model, OpenRouter key, Ollama URL) in the editor.</summary>
        public static void OpenSettings()
        {
            ScriptRepository.EnsureSettingsFile();
            OpenInEditor(ScriptRepository.SettingsFile);
        }

        public static void Reveal(string path)
        {
            if (string.IsNullOrEmpty(path)) return;
            try
            {
                if (OperatingSystem.IsMacOS()) Start("open", $"-R \"{path}\"");
                else Start("explorer.exe", $"/select,\"{path}\"");
            }
            catch { }
        }

        public static void CopyText(string text)
        {
            try
            {
                var cb = new Eto.Forms.Clipboard();
                cb.Text = text;
            }
            catch { }
        }

        private static void Start(string file, string args) =>
            Process.Start(new ProcessStartInfo(file, args) { UseShellExecute = false, CreateNoWindow = true });

        private static string ResolveCommand(string cmd) =>
            File.Exists(cmd) ? cmd : FindOnPath(cmd) ?? cmd;

        private static string FindOnPath(string name)
        {
            var exts = OperatingSystem.IsWindows() ? new[] { ".cmd", ".exe", ".bat" } : new[] { "" };
            var dirs = (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator);
            return dirs.SelectMany(d => exts.Select(e => SafeCombine(d, name + e)))
                       .FirstOrDefault(p => p != null && File.Exists(p));
        }

        private static string SafeCombine(string dir, string file)
        {
            try { return Path.Combine(dir.Trim('"'), file); } catch { return null; }
        }
    }
}
