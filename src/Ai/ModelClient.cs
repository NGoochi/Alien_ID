using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Alien.Ai
{
    /// <summary>
    /// Which model answers: "vendor/model" goes to OpenRouter (key from settings), "ollama:name" to a local Ollama.
    /// Both speak the OpenAI chat-completions API, so one client serves them.
    /// </summary>
    public sealed class ModelChoice
    {
        // Picked from the 10-prompt test (2026-10-09): 10/10 first try, ~8 s and ~US$0.001 per node.
        public const string DefaultModel = "anthropic/claude-haiku-5.5";
        private const string OpenRouterUrl = "https://openrouter.ai/api/v1/chat/completions";

        /// <summary>As the user wrote it, e.g. "ollama:qwen3:8b".</summary>
        public string Label { get; private set; }
        /// <summary>As sent to the endpoint.</summary>
        public string Model { get; private set; }
        public bool Local { get; private set; }
        public string Endpoint { get; private set; }
        public string Key { get; private set; }

        /// <summary>Null/empty → settings "model" → <see cref="DefaultModel"/>.</summary>
        public static ModelChoice Resolve(string model = null)
        {
            var label = string.IsNullOrWhiteSpace(model) ? ScriptRepository.Setting("model") : model.Trim();
            if (string.IsNullOrWhiteSpace(label)) label = DefaultModel;

            if (label.StartsWith("ollama:", StringComparison.OrdinalIgnoreCase))
            {
                var url = ScriptRepository.Setting("ollamaUrl");
                if (string.IsNullOrWhiteSpace(url)) url = "http://localhost:11434";
                return new ModelChoice
                {
                    Label = label, Model = label.Substring("ollama:".Length), Local = true,
                    Endpoint = url.TrimEnd('/') + "/v1/chat/completions"
                };
            }

            var key = ScriptRepository.Setting("openrouterKey");
            if (string.IsNullOrWhiteSpace(key))
                throw new InvalidOperationException(
                    "No OpenRouter key yet: open the Alien panel's Help (?) and follow \"Connect OpenRouter\" (about 3 minutes), " +
                    "or pick a local model (\"ollama:qwen3:8b\").");
            return new ModelChoice { Label = label, Model = label, Endpoint = OpenRouterUrl, Key = key.Trim() };
        }
    }

    public sealed class ChatMessage
    {
        public string Role { get; }
        public string Content { get; }
        public ChatMessage(string role, string content) { Role = role; Content = content; }
    }

    public sealed class ModelReply
    {
        public string Text { get; set; } = "";
        public int PromptTokens { get; set; }
        public int CompletionTokens { get; set; }
        /// <summary>US$ as reported by OpenRouter; 0 for local models.</summary>
        public double Cost { get; set; }
    }

    public static class ModelClient
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromMinutes(4) };

        /// <summary>One chat completion. A transient failure (network, 429, 5xx, "Provider returned error") is resent once.</summary>
        public static async Task<ModelReply> Complete(ModelChoice m, IEnumerable<ChatMessage> messages, CancellationToken ct)
        {
            var list = messages.ToList();
            try { return await Send(m, list, ct).ConfigureAwait(false); }
            catch (TransientException) { }
            catch (HttpRequestException) { }
            await Task.Delay(1500, ct).ConfigureAwait(false);
            try { return await Send(m, list, ct).ConfigureAwait(false); }
            catch (TransientException tex) { throw new InvalidOperationException(tex.Message); }
        }

        private sealed class TransientException : Exception
        {
            public TransientException(string message) : base(message) { }
        }

        private static async Task<ModelReply> Send(ModelChoice m, List<ChatMessage> messages, CancellationToken ct)
        {
            var body = new JObject
            {
                ["model"] = m.Model,
                ["messages"] = new JArray(messages.Select(x => new JObject { ["role"] = x.Role, ["content"] = x.Content })),
                ["temperature"] = 0.2,
            };
            var effort = ScriptRepository.Setting("reasoning");
            if (m.Local)
            {
                // Local thinking models (qwen3.x) otherwise think for minutes before writing a short script.
                body["reasoning_effort"] = string.IsNullOrWhiteSpace(effort) ? "none" : effort;
            }
            else
            {
                body["usage"] = new JObject { ["include"] = true };
                // Never route prompts to providers that store or train on them.
                body["provider"] = new JObject { ["data_collection"] = "deny" };
                body["reasoning"] = new JObject { ["effort"] = string.IsNullOrWhiteSpace(effort) ? "low" : effort };
            }

            using var req = new HttpRequestMessage(HttpMethod.Post, m.Endpoint)
            {
                Content = new StringContent(body.ToString(Formatting.None), Encoding.UTF8, "application/json")
            };
            if (m.Key != null)
            {
                req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", m.Key);
                req.Headers.Add("X-Title", "Alien");
            }

            using var res = await Http.SendAsync(req, ct).ConfigureAwait(false);
            var text = await res.Content.ReadAsStringAsync().ConfigureAwait(false);
            JObject json;
            try { json = JObject.Parse(text); }
            catch
            {
                var msg = $"{m.Label}: HTTP {(int)res.StatusCode} {Clip(text, 300)}";
                if ((int)res.StatusCode >= 500) throw new TransientException(msg);
                throw new InvalidOperationException(msg);
            }
            if (!res.IsSuccessStatusCode || json["error"] != null)
            {
                var msg = $"{m.Label}: {json.SelectToken("error.message")?.ToString() ?? $"HTTP {(int)res.StatusCode}"}";
                int code = json.SelectToken("error.code")?.Type == JTokenType.Integer ? json.SelectToken("error.code").Value<int>() : (int)res.StatusCode;
                if (code == 429 || code >= 500 || msg.Contains("Provider returned error")) throw new TransientException(msg);
                throw new InvalidOperationException(msg);
            }

            var usage = json["usage"] as JObject;
            return new ModelReply
            {
                Text = json.SelectToken("choices[0].message.content")?.ToString() ?? "",
                PromptTokens = usage?.Value<int?>("prompt_tokens") ?? 0,
                CompletionTokens = usage?.Value<int?>("completion_tokens") ?? 0,
                Cost = usage?.Value<double?>("cost") ?? 0,
            };
        }

        private static string Clip(string s, int max) => s == null ? "" : s.Length > max ? s.Substring(0, max) + "…" : s;
    }
}
