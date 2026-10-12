using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

using Newtonsoft.Json.Linq;

namespace Alien.Ui
{
    /// <summary>
    /// Suggestions for a text input while it is typed in (header <c>suggest="provider"</c>). Providers are built in, run
    /// off the UI thread and return at most a handful of complete values.
    /// </summary>
    public static class Suggest
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(6) };

        public static readonly Dictionary<string, Func<string, CancellationToken, Task<string[]>>> Providers =
            new Dictionary<string, Func<string, CancellationToken, Task<string[]>>>(StringComparer.OrdinalIgnoreCase)
            {
                ["vic-address"] = VicAddress,
            };

        public static Func<string, CancellationToken, Task<string[]>> Find(string name) =>
            name != null && Providers.TryGetValue(name, out var p) ? p : null;

        static Suggest() => Http.DefaultRequestHeaders.UserAgent.ParseAdd("Alien/2.0 (Grasshopper address suggestions)");

        // ── Victorian addresses (Vicmap Address, the index vic_site_map geocodes with) ──

        private static readonly Dictionary<string, string> RoadTypes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["ST"] = "STREET", ["RD"] = "ROAD", ["AV"] = "AVENUE", ["AVE"] = "AVENUE", ["PDE"] = "PARADE", ["CR"] = "CRESCENT",
            ["CRES"] = "CRESCENT", ["CT"] = "COURT", ["DR"] = "DRIVE", ["PL"] = "PLACE", ["LN"] = "LANE", ["TCE"] = "TERRACE",
            ["GR"] = "GROVE", ["BVD"] = "BOULEVARD", ["BLVD"] = "BOULEVARD", ["HWY"] = "HIGHWAY", ["CL"] = "CLOSE",
            ["CCT"] = "CIRCUIT", ["SQ"] = "SQUARE", ["ESP"] = "ESPLANADE", ["HL"] = "HILL", ["RDGE"] = "RIDGE",
        };

        /// <summary>
        /// "201 napier st fitz" -> "201 Napier Street, Fitzroy VIC 3065", by prefix search on Vicmap's ezi_address
        /// ("201 NAPIER STREET FITZROY 3065"). Needs a street number first; abbreviations are expanded.
        /// </summary>
        public static async Task<string[]> VicAddress(string text, CancellationToken ct)
        {
            var t = Regex.Replace((text ?? "").ToUpperInvariant().Replace(",", " "), @"\s+", " ").Trim();
            t = Regex.Replace(t, @"\b(VIC|VICTORIA|AUSTRALIA)\b", "").Trim();
            var m = Regex.Match(t, @"^(?:(\w+)\s*/\s*)?(\d+[A-Z]?)\s+(.+)$");
            if (!m.Success || m.Groups[3].Value.Length < 2) return Array.Empty<string>();
            var words = m.Groups[3].Value.Split(' ').ToList();
            // expand a complete road-type abbreviation when more words follow it (a trailing "ST" may still be typing)
            for (int i = 1; i < words.Count; i++)
                if (RoadTypes.TryGetValue(words[i], out var full) && (i < words.Count - 1 || words[i].Length >= 2)) { words[i] = full; break; }
            var prefix = (m.Groups[1].Success && m.Groups[1].Value.Length > 0 ? m.Groups[1].Value + "/" : "") + m.Groups[2].Value + " " + string.Join(" ", words);
            var cql = $"ezi_address LIKE '{prefix.Replace("'", "''")}%'";
            var url = "https://opendata.maps.vic.gov.au/geoserver/wfs?service=WFS&version=1.1.0&request=GetFeature" +
                      "&typeName=open-data-platform:address&outputFormat=application/json&maxFeatures=12" +
                      "&propertyName=ezi_address,locality_name,postcode,is_primary&CQL_FILTER=" + Uri.EscapeDataString(cql);
            using var res = await Http.GetAsync(url, ct).ConfigureAwait(false);
            if (!res.IsSuccessStatusCode) return Array.Empty<string>();
            var json = JObject.Parse(await res.Content.ReadAsStringAsync().ConfigureAwait(false));
            var ti = CultureInfo.InvariantCulture.TextInfo;
            return (json["features"] as JArray ?? new JArray())
                .Select(f => f["properties"] as JObject).Where(p => p != null)
                .OrderBy(p => p.Value<string>("is_primary") != "Y").ThenBy(p => p.Value<string>("ezi_address"))
                .Select(p =>
                {
                    var ezi = p.Value<string>("ezi_address") ?? "";
                    var locality = p.Value<string>("locality_name") ?? "";
                    var postcode = p.Value<string>("postcode") ?? "";
                    var street = ezi;  // "201 NAPIER STREET FITZROY 3065" -> "201 NAPIER STREET"
                    var tail = (" " + locality + " " + postcode).TrimEnd();
                    if (tail.Trim().Length > 0 && street.EndsWith(tail)) street = street.Substring(0, street.Length - tail.Length);
                    return $"{ti.ToTitleCase(street.ToLowerInvariant())}, {ti.ToTitleCase(locality.ToLowerInvariant())} VIC {postcode}".Trim();
                })
                .Distinct().Take(8).ToArray();
        }
    }
}
