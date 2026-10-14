using System;
using System.Drawing;

using Grasshopper.Kernel;

using Alien.Mcp;

namespace Alien
{
    public class AlienInfo : GH_AssemblyInfo
    {
        public override string Name => "Alien";
        public override Bitmap Icon => Ui.Logo.Icon(24, Color.Black);
        public override string Description => "Live Python 3 / C# script nodes that AI agents build, wire and edit over MCP.";
        public override Guid Id => new Guid("D610DE6A-0FB9-41BD-9AE8-02295A9BBA00");
        public override string AuthorName => "Nick Gauci";
        public override string AuthorContact => "";
        public override string Version => "0.02-alpha";
    }

    /// <summary>Start the MCP server when Grasshopper loads, so an agent can create the first node itself.</summary>
    public class AlienPriority : GH_AssemblyPriority
    {
        public override GH_LoadingInstruction PriorityLoad()
        {
            ScriptRepository.EnsureSettingsFile();
            ScriptRepository.EnsureDefaultRoot();
            McpServer.Instance.Start(ScriptRepository.Port);
            Ui.AlienPanel.Register();
            return GH_LoadingInstruction.Proceed;
        }
    }
}
