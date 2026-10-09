using System;
using System.Collections.Generic;

using Grasshopper.Kernel;
using Grasshopper.Kernel.Parameters;

using Rhino.Geometry;

namespace Alien.Scripting
{
    /// <summary>Header type names ↔ Grasshopper params ↔ .NET types. One table, used everywhere.</summary>
    public static class TypeMap
    {
        private sealed record Entry(string Key, Type ClrType, Func<IGH_Param> CreateParam);

        private static readonly Dictionary<string, Entry> Entries = new(StringComparer.OrdinalIgnoreCase);
        private static readonly Dictionary<string, string> Aliases = new(StringComparer.OrdinalIgnoreCase);

        static TypeMap()
        {
            Add("object", typeof(object), () => new Param_GenericObject(), "", "any", "generic", "obj");
            Add("float", typeof(double), () => new Param_Number(), "double", "number", "num", "real");
            Add("int", typeof(int), () => new Param_Integer(), "integer", "long");
            Add("bool", typeof(bool), () => new Param_Boolean(), "boolean");
            Add("str", typeof(string), () => new Param_String(), "string", "text");
            Add("Color", typeof(System.Drawing.Color), () => new Param_Colour(), "colour");
            Add("Interval", typeof(Interval), () => new Param_Interval(), "domain");
            Add("Point3d", typeof(Point3d), () => new Param_Point(), "point", "pt");
            Add("Vector3d", typeof(Vector3d), () => new Param_Vector(), "vector", "vec");
            Add("Plane", typeof(Plane), () => new Param_Plane());
            Add("Line", typeof(Line), () => new Param_Line());
            Add("Circle", typeof(Circle), () => new Param_Circle());
            Add("Arc", typeof(Arc), () => new Param_Arc());
            Add("Box", typeof(Box), () => new Param_Box());
            Add("Rectangle3d", typeof(Rectangle3d), () => new Param_Rectangle(), "rectangle");
            Add("Curve", typeof(Curve), () => new Param_Curve(), "polyline", "nurbscurve", "polylinecurve", "linecurve");
            Add("Surface", typeof(Surface), () => new Param_Surface());
            Add("Brep", typeof(Brep), () => new Param_Brep(), "extrusion");
            Add("Mesh", typeof(Mesh), () => new Param_Mesh());
            Add("SubD", typeof(SubD), () => new Param_SubD());
            Add("Geometry", typeof(GeometryBase), () => new Param_Geometry(), "geometrybase");
            Add("Transform", typeof(Transform), () => new Param_Transform(), "xform");
        }

        private static void Add(string key, Type clr, Func<IGH_Param> create, params string[] aliases)
        {
            Entries[key] = new Entry(key, clr, create);
            Aliases[key] = key;
            foreach (var a in aliases) Aliases[a] = key;
        }

        /// <summary>Normalise a header type string. Unknown types map to "object" with known=false.</summary>
        public static string Normalize(string raw, out bool known)
        {
            raw = (raw ?? "").Trim();
            if (raw.StartsWith("rg.", StringComparison.OrdinalIgnoreCase)) raw = raw.Substring(3);
            if (raw.StartsWith("Rhino.Geometry.", StringComparison.OrdinalIgnoreCase)) raw = raw.Substring(15);
            known = Aliases.TryGetValue(raw, out var key);
            return known ? key : "object";
        }

        public static Type ClrType(string key) =>
            Entries.TryGetValue(key ?? "object", out var e) ? e.ClrType : typeof(object);

        public static IGH_Param CreateInputParam(ParamDef def)
        {
            var p = Entries.TryGetValue(def.Type, out var e) ? e.CreateParam() : new Param_GenericObject();
            p.Name = def.Name;
            p.NickName = def.Name;
            p.Description = string.IsNullOrEmpty(def.Description) ? $"{def.Name} ({Describe(def)})" : $"{def.Description}\n({Describe(def)})";
            p.Access = def.Access switch
            {
                ParamAccess.List => GH_ParamAccess.list,
                ParamAccess.Tree => GH_ParamAccess.tree,
                _ => GH_ParamAccess.item
            };
            p.Optional = true;
            return p;
        }

        /// <summary>Outputs stay generic: the script decides what flows; typed outputs only cause cast failures.</summary>
        public static IGH_Param CreateOutputParam(ParamDef def)
        {
            return new Param_GenericObject
            {
                Name = def.Name,
                NickName = def.Name,
                Description = string.IsNullOrEmpty(def.Description) ? def.Name : def.Description
            };
        }

        public static string Describe(ParamDef def) => def.Access switch
        {
            ParamAccess.List => $"list[{def.Type}]",
            ParamAccess.Tree => $"tree[{def.Type}]",
            _ => def.Type
        };
    }
}
