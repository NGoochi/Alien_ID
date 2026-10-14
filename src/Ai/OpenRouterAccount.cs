using System;
using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading;
using System.Threading.Tasks;

using Newtonsoft.Json.Linq;

namespace Alien.Ai
{
    /// <summary>
    /// The help screen's key check: is this an OpenRouter key that works, and how much credit is left. Reads
    /// GET /api/v1/key (the key itself, its spend limit, expiry) and GET /api/v1/credits (the account balance).
    /// The result never contains the key.
    /// </summary>
    internal static class OpenRouterAccount
    {
        private const string Api = "https://openrouter.ai/api/v1/";
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(12) };
        private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        /// <summary>A key with the right shape: "sk-or-" and no spaces (a paste often brings a newline or quotes).</summary>
        public static string Clean(string key) => (key ?? "").Trim().Trim('"', '\'').Trim();

        public static bool LooksLikeKey(string key) =>
            key.StartsWith("sk-or-", StringComparison.Ordinal) && key.Length > 20 && key.IndexOfAny(new[] { ' ', '\n', '\r', '\t' }) < 0;

        /// <summary>{ok, reachable, message, credits?, keyLimitRemaining?, expires?}. ok = OpenRouter accepted the key.</summary>
        public static async Task<JObject> Check(string key, CancellationToken ct = default)
        {
            key = Clean(key);
            if (key.Length == 0) return Result(false, true, "Paste your key first.");
            if (!LooksLikeKey(key)) return Result(false, true, "That isn't an OpenRouter API key: they start with sk-or-v1- and have no spaces.");

            JObject keyInfo, credits = null;
            try
            {
                var keyTask = Get("key", key, ct);
                var creditsTask = Get("credits", key, ct);
                var (status, body) = await keyTask.ConfigureAwait(false);
                if (status == HttpStatusCode.Unauthorized || status == HttpStatusCode.Forbidden)
                    return Result(false, true, "OpenRouter doesn't accept this key. It may be mistyped, deleted or expired: make a new one on the API Keys page.");
                if (status != HttpStatusCode.OK) return Result(false, false, $"OpenRouter answered {(int)status}. Try again in a minute.");
                keyInfo = body["data"] as JObject ?? body;
                try
                {
                    var (cs, cb) = await creditsTask.ConfigureAwait(false);
                    if (cs == HttpStatusCode.OK) credits = cb["data"] as JObject ?? cb;
                }
                catch { /* the balance is a nice-to-have */ }
            }
            catch (Exception ex) when (ex is HttpRequestException || ex is TaskCanceledException)
            {
                return Result(false, false, "Couldn't reach OpenRouter (" + ex.Message + "). Check the internet connection.");
            }

            var r = Result(true, true, null);
            var parts = new System.Collections.Generic.List<string> { "Linked: OpenRouter accepts this key." };
            double? left = null;
            if (credits?["total_credits"] != null)
            {
                left = credits.Value<double>("total_credits") - credits.Value<double?>("total_usage").GetValueOrDefault();
                r["credits"] = Math.Round(left.Value, 2);
                parts.Add($"US${left.Value.ToString("0.00", Inv)} of credit left on the account.");
            }
            if (keyInfo?["limit_remaining"] is JToken lr && lr.Type is JTokenType.Float or JTokenType.Integer)
            {
                r["keyLimitRemaining"] = Math.Round(lr.Value<double>(), 2);
                parts.Add($"This key may spend US${lr.Value<double>().ToString("0.00", Inv)} more (its own limit).");
            }
            if (keyInfo?.Value<string>("expires_at") is string exp && DateTime.TryParse(exp, Inv, DateTimeStyles.AdjustToUniversal, out var when))
            {
                r["expires"] = when.ToString("d MMM yyyy", Inv);
                parts.Add($"It expires on {when.ToString("d MMM yyyy", Inv)}.");
            }
            if (left.HasValue && left.Value <= 0.01)
                parts.Add("Add credit (Settings > Credits) before using paid models.");
            r["message"] = string.Join(" ", parts);
            return r;
        }

        private static async Task<(HttpStatusCode, JObject)> Get(string path, string key, CancellationToken ct)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, Api + path);
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
            using var res = await Http.SendAsync(req, ct).ConfigureAwait(false);
            var text = await res.Content.ReadAsStringAsync().ConfigureAwait(false);
            JObject body;
            try { body = JObject.Parse(text); } catch { body = new JObject(); }
            return (res.StatusCode, body);
        }

        private static JObject Result(bool ok, bool reachable, string message) =>
            new JObject { ["ok"] = ok, ["reachable"] = reachable, ["message"] = message };

        /// <summary>"sk-or-v1-…9f3a": enough to recognise a key, never enough to use it.</summary>
        public static string Hint(string key)
        {
            key = Clean(key);
            if (key.Length < 12) return key.Length == 0 ? null : "set";
            int dash = key.StartsWith("sk-or-v1-", StringComparison.Ordinal) ? 9 : 6;
            return key.Substring(0, dash) + "…" + key.Substring(key.Length - 4);
        }
    }
}
