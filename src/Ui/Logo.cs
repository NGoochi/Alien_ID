using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Alien.Ui
{
    /// <summary>
    /// The Alien mark (docs/brand/alien-mark.svg) as a GDI path: drawn in the theme's accent on the nodes, in black
    /// for Grasshopper's toolbar icon. The panel page carries the same path as inline SVG.
    /// </summary>
    internal static class Logo
    {
        /// <summary>The SVG path data (absolute M, C, Z only) and its viewBox.</summary>
        internal const string Data = "M1167.6 1430.5C1129.0 1424.2 1089.8 1402.6 1063.7 1373.2C1049.5 1357.2 1034.2 1330.7 1027.6 1310.5C1018.5 1282.6 1017.0 1252.9 1023.1 1221.2C1033.4 1167.7 1067.8 1123.2 1117.2 1099.2C1133.5 1091.3 1149.0 1086.4 1169.0 1082.8C1180.5 1080.8 1215.0 1081.1 1227.5 1083.4C1282.4 1093.4 1326.5 1127.2 1352.5 1179.0C1370.0 1213.9 1375.4 1255.3 1367.4 1293.8C1363.4 1313.3 1351.6 1339.7 1339.3 1356.9C1313.0 1393.5 1273.2 1419.4 1228.5 1429.0C1215.3 1431.9 1181.2 1432.7 1167.6 1430.5ZM809.5 1112.4C801.2 1111.0 789.1 1107.7 782.8 1104.9C773.1 1100.6 767.8 1097.3 751.3 1084.9C727.5 1066.9 704.9 1054.2 679.3 1044.2C591.2 1009.8 495.5 1017.9 413.5 1066.6C380.8 1086.0 354.7 1095.3 322.5 1099.1C306.9 1100.9 278.3 1099.7 263.5 1096.6C224.3 1088.3 190.3 1069.5 162.0 1040.3C139.3 1017.0 124.6 993.1 114.4 962.8C107.1 941.1 104.5 922.2 105.3 896.1C106.2 868.0 111.6 845.5 123.4 820.5C143.7 777.8 175.3 745.9 217.5 725.5C235.0 717.0 245.9 713.5 268.5 708.9C301.1 702.4 324.3 694.6 350.0 681.7C378.7 667.3 402.2 650.6 424.8 628.5C453.3 600.9 471.9 575.3 488.1 541.5C507.8 500.4 517.2 459.5 517.2 414.5C517.2 385.5 515.0 369.2 506.9 337.2C500.6 312.1 498.8 298.8 498.8 277.5C498.8 209.0 530.4 145.2 584.5 104.8C606.9 88.0 637.9 74.3 666.5 68.5C735.7 54.5 808.3 76.5 859.1 127.0C917.6 185.0 936.2 273.1 906.5 350.5C888.0 398.5 856.6 434.7 810.5 461.0C739.8 501.2 692.4 573.1 682.4 655.0C680.2 673.2 680.9 704.4 683.9 721.5C693.5 775.5 715.4 819.4 751.8 857.2C780.0 886.4 811.7 907.2 849.0 920.9C868.8 928.3 877.0 933.1 888.9 944.4C898.2 953.4 903.8 961.3 909.5 973.2C916.5 988.0 918.4 997.4 918.4 1016.5C918.4 1030.0 918.1 1033.8 916.1 1041.0C907.0 1074.8 880.1 1101.3 846.0 1110.0C837.7 1112.2 816.8 1113.5 809.5 1112.4Z";
        private static readonly RectangleF ViewBox = new RectangleF(105, 64, 1266, 1368);

        private static GraphicsPath _unit;

        /// <summary>Width over height of the mark.</summary>
        public static float Aspect => ViewBox.Width / ViewBox.Height;

        /// <summary>The mark fitted (aspect kept, centred) into a rectangle. The caller disposes it.</summary>
        public static GraphicsPath Fit(RectangleF box)
        {
            _unit ??= Parse();
            float k = Math.Min(box.Width / ViewBox.Width, box.Height / ViewBox.Height);
            float x = box.X + (box.Width - ViewBox.Width * k) / 2, y = box.Y + (box.Height - ViewBox.Height * k) / 2;
            var p = (GraphicsPath)_unit.Clone();
            using var m = new Matrix();
            m.Translate(x, y);
            m.Scale(k, k);
            m.Translate(-ViewBox.X, -ViewBox.Y);
            p.Transform(m);
            return p;
        }

        public static void Draw(Graphics g, RectangleF box, Color colour)
        {
            using var p = Fit(box);
            using var b = new SolidBrush(colour);
            var mode = g.SmoothingMode;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.FillPath(b, p);
            g.SmoothingMode = mode;
        }

        /// <summary>A square icon: the mark in one colour on a transparent ground.</summary>
        public static Bitmap Icon(int size, Color colour)
        {
            var bmp = new Bitmap(size, size);
            using var g = Graphics.FromImage(bmp);
            g.Clear(Color.Transparent);
            float pad = size / 12f;
            Draw(g, new RectangleF(pad, pad, size - 2 * pad, size - 2 * pad), colour);
            return bmp;
        }

        private static GraphicsPath Parse()
        {
            var path = new GraphicsPath(FillMode.Winding);
            var tokens = Regex.Matches(Data, @"[MCZ]|-?\d*\.?\d+");
            var nums = new List<float>();
            char cmd = ' ';
            PointF cur = PointF.Empty, start = PointF.Empty;
            void Flush()
            {
                if (cmd == 'M' && nums.Count >= 2) { cur = start = new PointF(nums[0], nums[1]); path.StartFigure(); }
                else if (cmd == 'C')
                    for (int i = 0; i + 5 < nums.Count; i += 6)
                    {
                        var p3 = new PointF(nums[i + 4], nums[i + 5]);
                        path.AddBezier(cur, new PointF(nums[i], nums[i + 1]), new PointF(nums[i + 2], nums[i + 3]), p3);
                        cur = p3;
                    }
                nums.Clear();
            }
            foreach (Match t in tokens)
            {
                var s = t.Value;
                if (s.Length == 1 && char.IsLetter(s[0]))
                {
                    Flush();
                    cmd = s[0];
                    if (cmd == 'Z') { path.CloseFigure(); cur = start; }
                }
                else nums.Add(float.Parse(s, CultureInfo.InvariantCulture));
            }
            Flush();
            return path;
        }
    }
}
