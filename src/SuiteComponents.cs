using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Security.Cryptography;
using System.Text;

using Grasshopper.Kernel;

namespace Alien
{
    /// <summary>
    /// A suite node with its own button on Grasshopper's Alien tab: an Alien node that binds itself to
    /// &lt;script folder&gt;/suite/&lt;script&gt;.py when placed, so the base nodes (3D object, 2D object, walls, views…)
    /// are found where Grasshopper users look first. Saved and reopened like any Alien node (the file keeps the script).
    /// The description is the node's own library line, read from the suite file embedded in the plugin.
    /// </summary>
    public abstract class SuiteComponent : AlienComponent
    {
        private readonly string _script, _glyph, _panel;
        private readonly GH_Exposure _exposure;

        protected SuiteComponent(string script, string title, string glyph, string panel, GH_Exposure exposure = GH_Exposure.primary)
            : base(title, script, (Suite.Description(script) ?? title) + "\n(Alien suite node: suite/" + script + ".py)", panel)
        {
            _script = script;
            _glyph = glyph;
            _panel = panel;
            _exposure = exposure;
        }

        public string Script => _script;

        /// <summary>Stable per script, so files keep opening as the same button's node.</summary>
        public override Guid ComponentGuid
        {
            get
            {
                using var md5 = MD5.Create();
                return new Guid(md5.ComputeHash(Encoding.UTF8.GetBytes("alien.suite." + _script)));
            }
        }

        public override GH_Exposure Exposure => _exposure;

        protected override Bitmap Icon => SuiteIcon.Draw(_glyph, _panel);

        public override void AddedToDocument(GH_Document document)
        {
            base.AddedToDocument(document);
            if (ScriptPath != null || !ScriptRepository.IsConfigured) return; // opened or pasted: it has its script
            Suite.InstallIfMissing();
            var path = Path.Combine(ScriptRepository.Root, "suite", _script + ".py");
            if (File.Exists(path)) SetScript(path);
        }
    }

    /// <summary>Toolbar icons for suite nodes: a rounded tile in the panel's colour with a short glyph.</summary>
    internal static class SuiteIcon
    {
        public static Bitmap Draw(string glyph, string panel)
        {
            var bmp = new Bitmap(24, 24);
            using var g = Graphics.FromImage(bmp);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
            g.Clear(Color.Transparent);
            var colour = panel switch
            {
                "Create" => Color.FromArgb(124, 77, 255),
                "Site" => Color.FromArgb(0, 160, 70),
                "Building" => Color.FromArgb(230, 120, 20),
                "Views" => Color.FromArgb(30, 120, 220),
                "Entourage" => Color.FromArgb(0, 150, 150),
                _ => Color.FromArgb(90, 90, 90),
            };
            using (var path = new GraphicsPath())
            {
                const float r = 5f;
                var rc = new RectangleF(1, 1, 22, 22);
                path.AddArc(rc.X, rc.Y, 2 * r, 2 * r, 180, 90);
                path.AddArc(rc.Right - 2 * r, rc.Y, 2 * r, 2 * r, 270, 90);
                path.AddArc(rc.Right - 2 * r, rc.Bottom - 2 * r, 2 * r, 2 * r, 0, 90);
                path.AddArc(rc.X, rc.Bottom - 2 * r, 2 * r, 2 * r, 90, 90);
                path.CloseFigure();
                using var fill = new SolidBrush(colour);
                g.FillPath(fill, path);
            }
            float size = glyph.Length <= 2 ? 9f : 7f;
            using var font = new Font(AlienAttributes.Family, size, FontStyle.Bold);
            using var white = new SolidBrush(Color.White);
            g.DrawString(glyph, font, white, new RectangleF(0, 0, 24, 24),
                new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center });
            return bmp;
        }
    }

    // ── the buttons: one class per suite node (Grasshopper registers components by type) ──
    // Panels on the Alien tab: Create, Site, Building, Views, Entourage, Data (the Alien node itself is under Script).

    public sealed class Object3dNode : SuiteComponent { public Object3dNode() : base("object_3d", "3D object", "3D", "Create") { } }
    public sealed class Object2dNode : SuiteComponent { public Object2dNode() : base("object_2d", "2D object", "2D", "Create") { } }
    public sealed class SymbolNode : SuiteComponent { public SymbolNode() : base("symbol", "Symbol", "Sy", "Create") { } }
    public sealed class MaterialNode : SuiteComponent { public MaterialNode() : base("material_texture", "Material", "Mt", "Create") { } }
    public sealed class DisplayModeNode : SuiteComponent { public DisplayModeNode() : base("display_mode", "Display mode", "Dm", "Create") { } }

    public sealed class SiteMapNode : SuiteComponent { public SiteMapNode() : base("vic_site_map", "Site map (VIC)", "Map", "Site") { } }
    public sealed class BimSiteNode : SuiteComponent { public BimSiteNode() : base("bim_site", "Site base", "Ste", "Site") { } }

    public sealed class LevelsNode : SuiteComponent { public LevelsNode() : base("levels", "Levels", "Lv", "Building") { } }
    public sealed class GridsNode : SuiteComponent { public GridsNode() : base("grids", "Grids", "Gr", "Building") { } }
    public sealed class WallsNode : SuiteComponent { public WallsNode() : base("walls", "Walls", "W", "Building") { } }
    public sealed class DoorsNode : SuiteComponent { public DoorsNode() : base("doors", "Doors", "D", "Building") { } }
    public sealed class WindowsNode : SuiteComponent { public WindowsNode() : base("windows", "Windows", "Wn", "Building") { } }
    public sealed class CurtainWallNode : SuiteComponent { public CurtainWallNode() : base("curtain_wall", "Curtain wall", "CW", "Building") { } }
    public sealed class SlabsNode : SuiteComponent { public SlabsNode() : base("slabs", "Slabs", "Sb", "Building") { } }
    public sealed class RoofsNode : SuiteComponent { public RoofsNode() : base("roofs", "Roofs", "Rf", "Building") { } }
    public sealed class StairsNode : SuiteComponent { public StairsNode() : base("stairs", "Stairs", "St", "Building") { } }
    public sealed class BalustradesNode : SuiteComponent { public BalustradesNode() : base("balustrades", "Balustrades", "Bl", "Building") { } }
    public sealed class WallTypesNode : SuiteComponent { public WallTypesNode() : base("wall_types", "Wall types", "WT", "Building", GH_Exposure.secondary) { } }
    public sealed class SlabTypesNode : SuiteComponent { public SlabTypesNode() : base("slab_types", "Slab types", "ST", "Building", GH_Exposure.secondary) { } }
    public sealed class RoofTypesNode : SuiteComponent { public RoofTypesNode() : base("roof_types", "Roof types", "RT", "Building", GH_Exposure.secondary) { } }
    public sealed class StairTypesNode : SuiteComponent { public StairTypesNode() : base("stair_types", "Stair types", "SrT", "Building", GH_Exposure.secondary) { } }
    public sealed class BalustradeTypesNode : SuiteComponent { public BalustradeTypesNode() : base("balustrade_types", "Balustrade types", "BlT", "Building", GH_Exposure.secondary) { } }
    public sealed class SectionProfileNode : SuiteComponent { public SectionProfileNode() : base("section_profile", "Section profile", "Pr", "Building", GH_Exposure.tertiary) { } }
    public sealed class BeamsColumnsNode : SuiteComponent { public BeamsColumnsNode() : base("beams_columns", "Beams and columns", "BC", "Building", GH_Exposure.tertiary) { } }
    public sealed class StructureEditNode : SuiteComponent { public StructureEditNode() : base("structure_edit", "Structure edit", "SE", "Building", GH_Exposure.tertiary) { } }
    public sealed class ConnectorsNode : SuiteComponent { public ConnectorsNode() : base("connectors", "Connectors", "Cn", "Building", GH_Exposure.tertiary) { } }

    public sealed class ViewsNode : SuiteComponent { public ViewsNode() : base("views", "Views", "Vw", "Views") { } }
    public sealed class LayoutNode : SuiteComponent { public LayoutNode() : base("layout", "Layout", "Lo", "Views") { } }
    public sealed class ExplodedAxoNode : SuiteComponent { public ExplodedAxoNode() : base("exploded_axo", "Exploded axo", "Ax", "Views") { } }
    public sealed class SiteViewsNode : SuiteComponent { public SiteViewsNode() : base("site_views", "Site views", "SV", "Views") { } }

    public sealed class ScaleFigureNode : SuiteComponent { public ScaleFigureNode() : base("scale_figure", "Scale figure", "Fg", "Entourage") { } }
    public sealed class ScaleTreeNode : SuiteComponent { public ScaleTreeNode() : base("scale_tree", "Scale tree", "Tr", "Entourage") { } }

    public sealed class BranchPickerNode : SuiteComponent { public BranchPickerNode() : base("branch_picker", "Branch picker", "Br", "Data") { } }
}
