using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

using Rhino;

namespace Alien.Mcp
{
    /// <summary>
    /// In-process MCP server (Streamable HTTP, JSON responses only — no SSE stream).
    /// POST /mcp  → JSON-RPC (single or batch). GET/DELETE /mcp → 405. GET /health → status.
    /// Bound to loopback only; requests carrying a non-loopback Origin are rejected (DNS-rebinding guard).
    /// </summary>
    public sealed class McpServer
    {
        public const string Version = "0.02-alpha";
        private static readonly string[] ProtocolVersions = { "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05" };

        public static McpServer Instance { get; } = new McpServer();

        private readonly ConcurrentDictionary<Guid, AlienComponent> _nodes = new ConcurrentDictionary<Guid, AlienComponent>();
        private readonly Dictionary<string, Tool> _tools;
        private HttpListener _listener;
        private CancellationTokenSource _cts;

        public bool IsRunning { get; private set; }
        public int Port { get; private set; }
        public IEnumerable<AlienComponent> Nodes => _nodes.Values;

        private McpServer()
        {
            _tools = Tools.All().ToDictionary(t => t.Name);
        }

        public void Register(AlienComponent node) => _nodes[node.InstanceGuid] = node;
        public void Unregister(AlienComponent node) => _nodes.TryRemove(node.InstanceGuid, out _);

        // ── Lifecycle ────────────────────────────────────────────────

        public void Start(int port)
        {
            if (IsRunning) return;
            try
            {
                Port = port;
                _cts = new CancellationTokenSource();
                _listener = new HttpListener();
                _listener.Prefixes.Add($"http://127.0.0.1:{port}/");
                _listener.Prefixes.Add($"http://localhost:{port}/");
                _listener.Start();
                IsRunning = true;
                Task.Run(() => Loop(_cts.Token));
                RhinoApp.WriteLine($"Alien MCP: listening on http://127.0.0.1:{port}/mcp ({_tools.Count} tools)");
            }
            catch (Exception ex)
            {
                IsRunning = false;
                _listener = null;
                RhinoApp.WriteLine($"Alien MCP: could not start on port {port} ({ex.Message}). Is another Rhino running Alien?");
            }
        }

        public void Stop()
        {
            if (!IsRunning) return;
            IsRunning = false;
            try { _cts.Cancel(); _listener.Stop(); _listener.Close(); } catch { }
        }

        private async Task Loop(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested && _listener.IsListening)
            {
                HttpListenerContext ctx;
                try { ctx = await _listener.GetContextAsync().ConfigureAwait(false); }
                catch when (ct.IsCancellationRequested) { break; }
                catch (ObjectDisposedException) { break; }
                catch { continue; }
                _ = Task.Run(() => Handle(ctx));
            }
        }

        // ── HTTP ─────────────────────────────────────────────────────

        private async Task Handle(HttpListenerContext ctx)
        {
            var req = ctx.Request;
            var resp = ctx.Response;
            try
            {
                var origin = req.Headers["Origin"];
                if (!string.IsNullOrEmpty(origin) && !IsLoopbackOrigin(origin))
                {
                    resp.StatusCode = 403;
                    resp.Close();
                    return;
                }
                if (!string.IsNullOrEmpty(origin))
                {
                    resp.Headers["Access-Control-Allow-Origin"] = origin;
                    resp.Headers["Access-Control-Allow-Headers"] = "Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version";
                    resp.Headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS";
                }

                var path = req.Url?.AbsolutePath.TrimEnd('/') ?? "";
                if (req.HttpMethod == "OPTIONS") { resp.StatusCode = 204; resp.Close(); return; }

                if (path == "/mcp")
                {
                    if (req.HttpMethod != "POST")
                    {
                        resp.StatusCode = 405;
                        resp.Headers["Allow"] = "POST";
                        resp.Close();
                        return;
                    }
                    string body;
                    using (var reader = new StreamReader(req.InputStream, Encoding.UTF8)) body = await reader.ReadToEndAsync();
                    var reply = HandleRpcPayload(body);
                    if (reply == null) { resp.StatusCode = 202; resp.Close(); return; }
                    await Write(resp, 200, reply);
                    return;
                }

                if (path == "" || path == "/health")
                {
                    await Write(resp, 200, new JObject
                    {
                        ["server"] = "alien",
                        ["version"] = Version,
                        ["tools"] = _tools.Count,
                        ["nodes"] = _nodes.Count,
                        ["repository"] = ScriptRepository.Root
                    });
                    return;
                }

                resp.StatusCode = 404;
                resp.Close();
            }
            catch (Exception ex)
            {
                try { await Write(resp, 500, new JObject { ["error"] = ex.Message }); } catch { }
            }
        }

        private static bool IsLoopbackOrigin(string origin)
        {
            if (origin == "null") return false;
            return Uri.TryCreate(origin, UriKind.Absolute, out var u)
                && (u.Host == "127.0.0.1" || u.Host == "localhost" || u.Host == "[::1]");
        }

        private static async Task Write(HttpListenerResponse resp, int status, JToken json)
        {
            var bytes = Encoding.UTF8.GetBytes(json.ToString(Formatting.None));
            resp.StatusCode = status;
            resp.ContentType = "application/json; charset=utf-8";
            resp.ContentLength64 = bytes.Length;
            await resp.OutputStream.WriteAsync(bytes, 0, bytes.Length);
            resp.Close();
        }

        // ── JSON-RPC ─────────────────────────────────────────────────

        /// <summary>Returns the response token, or null when the payload only held notifications/responses.</summary>
        internal JToken HandleRpcPayload(string body)
        {
            JToken payload;
            try { payload = JToken.Parse(body); }
            catch { return RpcError(null, -32700, "Parse error"); }

            if (payload is JArray batch)
            {
                var replies = new JArray(batch.OfType<JObject>().Select(HandleRpc).Where(r => r != null));
                return replies.Count > 0 ? replies : null;
            }
            return payload is JObject single ? HandleRpc(single) : RpcError(null, -32600, "Invalid request");
        }

        private JObject HandleRpc(JObject msg)
        {
            var id = msg["id"];
            var method = msg.Value<string>("method");
            if (method == null) return null;              // a response from the client — nothing to do
            bool isNotification = id == null;
            var prms = msg["params"] as JObject ?? new JObject();

            try
            {
                JToken result;
                switch (method)
                {
                    case "initialize":
                        var requested = prms.Value<string>("protocolVersion");
                        result = new JObject
                        {
                            ["protocolVersion"] = ProtocolVersions.Contains(requested) ? requested : ProtocolVersions[1],
                            ["capabilities"] = new JObject { ["tools"] = new JObject { ["listChanged"] = false } },
                            ["serverInfo"] = new JObject { ["name"] = "alien", ["version"] = Version },
                            ["instructions"] = Tools.Instructions
                        };
                        break;
                    case "ping":
                        result = new JObject();
                        break;
                    case "tools/list":
                        result = new JObject { ["tools"] = new JArray(_tools.Values.Select(t => t.Definition())) };
                        break;
                    case "tools/call":
                        result = CallTool(prms.Value<string>("name"), prms["arguments"] as JObject ?? new JObject());
                        break;
                    default:
                        if (isNotification) return null;  // notifications/initialized, cancelled, …
                        return RpcError(id, -32601, $"Method not found: {method}");
                }
                return isNotification ? null : new JObject { ["jsonrpc"] = "2.0", ["id"] = id, ["result"] = result };
            }
            catch (Exception ex)
            {
                return isNotification ? null : RpcError(id, -32603, ex.Message);
            }
        }

        /// <summary>Run a tool for the panel's project agent, exactly as an MCP client would (serialised with MCP calls).</summary>
        internal JObject Invoke(string name, JObject args) => CallTool(name, args);

        /// <summary>A tool's name, description and input schema, or null.</summary>
        internal Tool Find(string name) => name != null && _tools.TryGetValue(name, out var t) ? t : null;

        private JObject CallTool(string name, JObject args)
        {
            if (name == null || !_tools.TryGetValue(name, out var tool))
                throw new Exception($"Unknown tool: {name}");
            using (UiThread.Lock())
            {
                try { return tool.Handler(args).ToJson(); }
                catch (ToolException tex) { return ToolResult.Error(tex.Message).ToJson(); }
                catch (Exception ex) { return ToolResult.Error($"{ex.GetType().Name}: {ex.Message}").ToJson(); }
            }
        }

        private static JObject RpcError(JToken id, int code, string message) => new JObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["error"] = new JObject { ["code"] = code, ["message"] = message }
        };
    }

    /// <summary>A tool-level failure the agent should see (isError: true), not a protocol error.</summary>
    public sealed class ToolException : Exception
    {
        public ToolException(string message) : base(message) { }
    }

    public sealed class Tool
    {
        public string Name { get; }
        public string Description { get; }
        public JObject Schema { get; }
        public Func<JObject, ToolResult> Handler { get; }

        public Tool(string name, string description, JObject schema, Func<JObject, ToolResult> handler)
        {
            Name = name; Description = description; Schema = schema; Handler = handler;
        }

        public JObject Definition() => new JObject
        {
            ["name"] = Name,
            ["description"] = Description,
            ["inputSchema"] = Schema
        };
    }

    public sealed class ToolResult
    {
        private readonly JArray _content = new JArray();
        private bool _isError;

        public static ToolResult Json(JToken json) => Text(json.ToString(Formatting.None));

        public static ToolResult Text(string text)
        {
            var r = new ToolResult();
            r._content.Add(new JObject { ["type"] = "text", ["text"] = text ?? "" });
            return r;
        }

        public static ToolResult Error(string message)
        {
            var r = Text(message);
            r._isError = true;
            return r;
        }

        public ToolResult AddImage(string base64Png)
        {
            _content.Add(new JObject { ["type"] = "image", ["data"] = base64Png, ["mimeType"] = "image/png" });
            return this;
        }

        public JObject ToJson() => new JObject { ["content"] = _content, ["isError"] = _isError };
    }
}
