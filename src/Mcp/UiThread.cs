using System;
using System.Threading;

using Grasshopper;
using Grasshopper.Kernel;

using Rhino;

namespace Alien.Mcp
{
    /// <summary>
    /// All Rhino/GH API calls must run on the UI thread. MCP requests arrive on pool threads;
    /// this marshals them and serialises tool calls so overlapping requests can't interleave document edits.
    /// </summary>
    public static class UiThread
    {
        private static readonly SemaphoreSlim Gate = new SemaphoreSlim(1, 1);

        public static T Run<T>(Func<T> func)
        {
            T result = default;
            Exception error = null;
            RhinoApp.InvokeAndWait(() =>
            {
                try { result = func(); }
                catch (Exception ex) { error = ex; }
            });
            if (error != null) throw error;
            return result;
        }

        public static void Run(Action action) => Run(() => { action(); return 0; });

        /// <summary>Hold the tool gate for the duration of a tool call (not the UI thread).</summary>
        public static IDisposable Lock()
        {
            Gate.Wait();
            return new Releaser();
        }

        private sealed class Releaser : IDisposable
        {
            private int _done;
            public void Dispose() { if (Interlocked.Exchange(ref _done, 1) == 0) Gate.Release(); }
        }

        /// <summary>
        /// Inside a tool call (gate held): let other tool calls run while this one waits on something slow, e.g. a
        /// model. Re-takes the gate on dispose, so the outer <see cref="Lock"/> still balances.
        /// </summary>
        public static IDisposable Yield()
        {
            Gate.Release();
            return new Retaker();
        }

        private sealed class Retaker : IDisposable
        {
            private int _done;
            public void Dispose() { if (Interlocked.Exchange(ref _done, 1) == 0) Gate.Wait(); }
        }

        /// <summary>Active GH document (UI thread only).</summary>
        public static GH_Document Document => Instances.ActiveCanvas?.Document;
    }
}
