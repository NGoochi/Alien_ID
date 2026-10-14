using System;
using System.Drawing;
using System.Globalization;
using System.Linq;

using Newtonsoft.Json.Linq;

namespace Alien.Ui
{
    /// <summary>
    /// Colours for everything Alien draws: the nodes, the text box on the canvas and the panel. People choose three
    /// (background, text, accent), from a preset or by hand; they are saved in settings.json as "theme". Every other
    /// colour is mixed from those three, so a light preset gives light nodes with readable edges.
    /// </summary>
    public sealed class Theme
    {
        public sealed record Preset(string Name, string Background, string Text, string Accent);

        public const string DefaultName = "Alien";

        /// <summary>The presets, in the order the panel shows them (dark first, then light).</summary>
        public static readonly Preset[] Presets =
        {
            new Preset("Alien", "#1e1e1e", "#d7d7d7", "#00c853"),
            new Preset("Cursor Dark", "#181818", "#e4e4e4", "#228df2"),
            new Preset("One Dark", "#282c34", "#abb2bf", "#61afef"),
            new Preset("Dracula", "#282a36", "#f8f8f2", "#bd93f9"),
            new Preset("Monokai", "#272822", "#f8f8f2", "#a6e22e"),
            new Preset("Nord", "#2e3440", "#d8dee9", "#88c0d0"),
            new Preset("Tokyo Night", "#1a1b26", "#c0caf5", "#7aa2f7"),
            new Preset("Solarized Dark", "#002b36", "#93a1a1", "#b58900"),
            new Preset("High Contrast", "#000000", "#ffffff", "#f38518"),
            new Preset("Light", "#f7f7f5", "#1f1f1f", "#00a344"),
            new Preset("GitHub Light", "#ffffff", "#24292f", "#0969da"),
            new Preset("Solarized Light", "#fdf6e3", "#586e75", "#268bd2"),
        };

        public string Name { get; private set; }
        public Color Background { get; private set; }
        public Color Text { get; private set; }
        public Color Accent { get; private set; }
        public bool IsLight { get; private set; }

        // Mixed from the three
        public Color Header { get; private set; }
        public Color Io { get; private set; }
        public Color Field { get; private set; }
        public Color FieldEdge { get; private set; }
        public Color Outline { get; private set; }
        public Color Dim { get; private set; }
        public Color Muted { get; private set; }
        public Color OnAccent { get; private set; }
        public Color Shadow { get; private set; }
        public Color Amber { get; private set; }
        public Color Red { get; private set; }
        public Color Blue { get; private set; }

        private static Theme _current;
        private static string _signature;
        private static long _checked;

        /// <summary>Raised (UI thread) after the theme is changed from the panel.</summary>
        public static event Action Changed;

        /// <summary>The theme in use. Re-reads settings at most once a second (it is asked on every node repaint).</summary>
        public static Theme Current
        {
            get
            {
                long now = Environment.TickCount64;
                if (_current != null && now - _checked < 1000) return _current;
                _checked = now;
                var token = ScriptRepository.SettingToken("theme");
                var sig = token?.ToString(Newtonsoft.Json.Formatting.None) ?? "";
                if (_current == null || sig != _signature)
                {
                    _signature = sig;
                    _current = From(token as JObject);
                }
                return _current;
            }
        }

        /// <summary>Save a theme: a preset by name, with any colour overridden ("#rrggbb"); null colours take the preset's.</summary>
        public static void Set(string preset, string background, string text, string accent)
        {
            var p = Find(preset);
            var theme = new JObject { ["preset"] = p?.Name ?? "Custom" };
            string Pick(string given, string fallback) => Parse(given).HasValue ? Hex(Parse(given).Value) : fallback;
            theme["background"] = Pick(background, p?.Background ?? Presets[0].Background);
            theme["text"] = Pick(text, p?.Text ?? Presets[0].Text);
            theme["accent"] = Pick(accent, p?.Accent ?? Presets[0].Accent);
            // Colours that match no preset any more make it a custom theme.
            if (p != null && !(Same(theme, "background", p.Background) && Same(theme, "text", p.Text) && Same(theme, "accent", p.Accent)))
                theme["preset"] = "Custom";
            bool isDefault = theme.Value<string>("preset") == DefaultName;
            ScriptRepository.SetSettingToken("theme", isDefault ? null : theme);
            _checked = 0;
            Changed?.Invoke();
        }

        private static bool Same(JObject t, string key, string hex) => string.Equals(t.Value<string>(key), hex, StringComparison.OrdinalIgnoreCase);

        public static Preset Find(string name) =>
            Presets.FirstOrDefault(p => string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase));

        private static Theme From(JObject o)
        {
            var preset = Find(o?.Value<string>("preset")) ?? (o == null ? Presets[0] : null);
            Color Get(string key, string fallback) => Parse(o?.Value<string>(key)) ?? Parse(fallback).Value;
            var t = new Theme
            {
                Name = preset?.Name ?? "Custom",
                Background = Get("background", preset?.Background ?? Presets[0].Background),
                Text = Get("text", preset?.Text ?? Presets[0].Text),
                Accent = Get("accent", preset?.Accent ?? Presets[0].Accent),
            };
            t.Derive();
            return t;
        }

        private void Derive()
        {
            IsLight = Luminance(Background) > 0.45;
            Header = IsLight ? Mix(Background, Text, 0.06) : Mix(Background, Color.Black, 0.27);
            Io = Mix(Background, Text, 0.033);
            Field = Mix(Background, Text, IsLight ? 0.06 : 0.087);
            FieldEdge = Mix(Background, Text, IsLight ? 0.25 : 0.173);
            Outline = Mix(Background, Text, IsLight ? 0.3 : 0.108);
            Dim = Mix(Text, Background, 0.42);
            Muted = Mix(Background, Text, 0.3);
            OnAccent = Luminance(Accent) > 0.5 ? Color.FromArgb(20, 20, 20) : Color.White;
            Shadow = Color.FromArgb(IsLight ? 28 : 50, 0, 0, 0);
            Amber = IsLight ? Color.FromArgb(184, 118, 0) : Color.FromArgb(224, 160, 32);
            Red = IsLight ? Color.FromArgb(200, 45, 45) : Color.FromArgb(225, 70, 70);
            Blue = IsLight ? Color.FromArgb(30, 110, 220) : Color.FromArgb(90, 160, 255);
        }

        /// <summary>The theme for the panel: the three colours plus the page's CSS variables.</summary>
        public JObject ToJson() => new JObject
        {
            ["preset"] = Name,
            ["background"] = Hex(Background),
            ["text"] = Hex(Text),
            ["accent"] = Hex(Accent),
            ["light"] = IsLight,
            ["css"] = new JObject
            {
                ["--bg"] = Hex(Background),
                ["--card"] = Hex(Mix(Background, Text, 0.045)),
                ["--field"] = Hex(Mix(Background, Text, IsLight ? 0.05 : 0.087)),
                ["--edge"] = Hex(Mix(Background, Text, IsLight ? 0.18 : 0.15)),
                ["--hover"] = Hex(Mix(Background, Text, 0.3)),
                ["--text"] = Hex(Text),
                ["--dim"] = Hex(Dim),
                ["--accent"] = Hex(Accent),
                ["--on-accent"] = Hex(OnAccent),
                ["--code"] = Hex(IsLight ? Mix(Background, Text, 0.03) : Mix(Background, Color.Black, 0.25)),
                ["--tile"] = Hex(Mix(Background, Text, 0.07)),
                ["--toast"] = Hex(Mix(Background, Accent, 0.16)),
                ["--amber"] = Hex(Amber),
                ["--red"] = Hex(Red),
                ["--blue"] = Hex(Blue),
            },
        };

        public static JArray PresetsJson() => new JArray(Presets.Select(p => new JObject
        {
            ["name"] = p.Name, ["background"] = p.Background, ["text"] = p.Text, ["accent"] = p.Accent,
        }));

        // ── Colour arithmetic ──

        public static Color Mix(Color a, Color b, double t) => Color.FromArgb(
            (int)Math.Round(a.R + (b.R - a.R) * t), (int)Math.Round(a.G + (b.G - a.G) * t), (int)Math.Round(a.B + (b.B - a.B) * t));

        public static double Luminance(Color c)
        {
            static double Lin(int v) { double s = v / 255.0; return s <= 0.03928 ? s / 12.92 : Math.Pow((s + 0.055) / 1.055, 2.4); }
            return 0.2126 * Lin(c.R) + 0.7152 * Lin(c.G) + 0.0722 * Lin(c.B);
        }

        public static string Hex(Color c) => $"#{c.R:x2}{c.G:x2}{c.B:x2}";

        /// <summary>"#rgb" or "#rrggbb" (with or without #); null when it isn't one.</summary>
        public static Color? Parse(string s)
        {
            s = (s ?? "").Trim().TrimStart('#');
            if (s.Length == 3) s = string.Concat(s.Select(ch => new string(ch, 2)));
            if (s.Length != 6 || !int.TryParse(s, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var v)) return null;
            return Color.FromArgb((v >> 16) & 255, (v >> 8) & 255, v & 255);
        }
    }
}
