using System;
using System.IO;
using System.Threading;

using Rhino;

namespace Alien
{
    /// <summary>
    /// FileSystemWatcher with debounce, marshalled to the Rhino UI thread.
    /// Watches the directory (not the file) so editors that save via rename/replace are caught.
    /// The component also checks timestamps each solve, so a missed event only delays a reload.
    /// </summary>
    public sealed class ScriptFileWatcher : IDisposable
    {
        private readonly Action _onChanged;
        private readonly int _debounceMs;
        private FileSystemWatcher _watcher;
        private Timer _timer;
        private string _path;
        private bool _disposed;

        public ScriptFileWatcher(Action onChanged, int debounceMs = 150)
        {
            _onChanged = onChanged ?? throw new ArgumentNullException(nameof(onChanged));
            _debounceMs = debounceMs;
        }

        public void Watch(string filePath)
        {
            if (_disposed || string.IsNullOrEmpty(filePath)) return;
            var full = Path.GetFullPath(filePath);
            if (_watcher != null && string.Equals(full, _path, StringComparison.OrdinalIgnoreCase)) return;

            Stop();
            _path = full;
            var dir = Path.GetDirectoryName(full);
            if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return;

            try
            {
                _watcher = new FileSystemWatcher(dir, Path.GetFileName(full))
                {
                    NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.FileName,
                    EnableRaisingEvents = true
                };
                _watcher.Changed += OnEvent;
                _watcher.Created += OnEvent;
                _watcher.Renamed += OnEvent;
            }
            catch (Exception ex)
            {
                RhinoApp.WriteLine($"Alien: file watcher failed for {full}: {ex.Message}");
                Stop();
            }
        }

        public void Stop()
        {
            _timer?.Dispose();
            _timer = null;
            if (_watcher == null) return;
            _watcher.EnableRaisingEvents = false;
            _watcher.Changed -= OnEvent;
            _watcher.Created -= OnEvent;
            _watcher.Renamed -= OnEvent;
            _watcher.Dispose();
            _watcher = null;
        }

        private void OnEvent(object sender, FileSystemEventArgs e)
        {
            if (_disposed) return;
            if (_timer == null) _timer = new Timer(_ => Fire(), null, _debounceMs, Timeout.Infinite);
            else _timer.Change(_debounceMs, Timeout.Infinite);
        }

        private void Fire()
        {
            if (_disposed) return;
            RhinoApp.InvokeOnUiThread(new Action(() => { if (!_disposed) _onChanged(); }));
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            Stop();
        }
    }
}
