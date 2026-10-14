# Installing Alien 0.02 (alpha)

Alien is a Grasshopper plugin for Rhino 8: nodes whose logic is a Python or C# script, written for you from a plain
English description. It comes with a suite of architectural nodes (Victorian site maps with terrain, streets and
trees; plan, section and elevation views; 3D objects and material textures from a prompt; scale figures and trees;
and a first building suite: site, levels, grids, walls, doors, windows, curtain walls, beams, columns and connectors).

**This is an alpha.** It is built and tested on Rhino 8 for macOS. The Windows installer and plugin are built the same
way but haven't been tried on a Windows PC yet. Please report what breaks.

## What you need

- **Rhino 8** (Windows or macOS) with Grasshopper.
- **Windows only:** Rhino must run on .NET Core, which is Rhino 8's default. To check, type `SetDotNetRuntime` in
  Rhino's command line. If it says `NETFramework`, choose `NETCore` and restart Rhino.
- **An OpenRouter account** for describe-a-node and the AI nodes (3D objects, textures, figures, trees). The Alien
  panel's Help walks you through making one and linking it; everything else works without it.
- **An internet connection** for the map node and the AI nodes.

## Windows: run the installer

1. **Close Rhino.**
2. **Download `Alien-0.02-alpha-Setup.exe`** and double-click it.
3. Windows may show **"Windows protected your PC"** (SmartScreen), because the installer isn't signed yet. Click
   **More info**, then **Run anyway**.
4. Follow the steps:
   - **Welcome:** it installs for your Windows account only; no administrator rights are needed.
   - **Connect OpenRouter (optional):** paste your OpenRouter API key if you have one, or leave it empty and link it
     later from the Alien panel's Help.
   - **Finish:** tick *Start Rhino 8 with Grasshopper* to open Rhino with Grasshopper ready.

What it does: copies `Alien.gha` into `%APPDATA%\Grasshopper\Libraries`, creates your script folder
(`Documents\Alien scripts`), writes `%APPDATA%\Alien\settings.json` (with your key if you gave one, keeping any
settings already there), and adds **Alien for Grasshopper** to *Settings > Apps* so you can uninstall it. If Rhino is
still running, it asks you to close it and retry.

## macOS: open the installer package

1. **Quit Rhino** (Cmd+Q; closing its windows is not enough).
2. **Download `Alien-0.02-alpha.pkg`** and double-click it.
3. macOS may say it **can't verify the developer**, because the package isn't signed yet. Open **System Settings >
   Privacy & Security**, scroll down to the message about `Alien-0.02-alpha.pkg` and click **Open Anyway** (on older
   macOS: right-click the package, choose **Open**, then **Open**).
4. Click through **Continue** and **Install**. It installs for your user only, into Grasshopper's Libraries folder
   (`~/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-…)/Libraries`). No password
   is needed. If Rhino is open, it tells you to quit it first.

## First run (both)

1. **Start Rhino** and type `Grasshopper` (the Windows installer can do this for you).
2. **Place an Alien node:** it's on the **Alien** tab, under **Script**. Your scripts are kept in
   `Documents/Alien scripts`; right-click a node > *Script repository* to choose another folder.
3. **Open the Alien panel:** click **PANEL** on the node, or double-click it. The panel sits beside the Grasshopper
   window.
4. **Connect OpenRouter:** the first time, the panel opens on **Help** if no key is linked yet (later, click **?**).
   It takes about 3 minutes:
   1. Create an OpenRouter account (Google, GitHub or email).
   2. Add some credit (US$5 goes a long way: a described node costs about US$0.002).
   3. Create an API key (*Settings > API Keys > Create API Key*; set a credit limit if you like) and copy it.
   4. Paste it into Help and click **Link key**. Alien checks it with OpenRouter and shows your remaining credit.
5. **Try it:** describe a node in plain English ("divide a curve into 20 points"), or place a ready-made node from
   the **Library**. The **Generated** tab keeps the 3D objects, materials and drawings the suite makes; **⚙** picks a
   theme. Select two or more nodes and describe a node that uses them: it is built beside them, already wired.

**Optional, connect an AI agent:** for Claude Code, run
`claude mcp add --transport http alien http://127.0.0.1:9876/mcp`. Agents can then build and wire nodes while you
watch.

## What costs money

Only steps that call a model cost anything, and they're paid to OpenRouter from your own credit:

| Step | Cost |
|---|---|
| Describe-a-node (Claude Haiku 5.5) | about US$0.002 per node |
| `object_3d` (Claude Sonnet 5.5) | about US$0.01–0.02 per design |
| `material_texture`, `scale_figure`, `scale_tree` | about US$0.03 per image |
| `vic_site_map`, `site_views`, `branch_picker`, the building suite (`bim_site`, `levels`, `grids`, walls, openings, curtain walls, structure) | free |

The AI nodes only call a model when you press their **Generate** button. Everything they make is cached, so moving
sliders and reopening files is free.

## Privacy and security

- Your API key stays in `settings.json` on your computer (owner-only on macOS). It is never written into `.gh` files
  or scripts. The panel shows only its first and last characters.
- Prompts go through OpenRouter, only to providers that don't store or train on them.
- Scripts, including ones a model writes, run inside Rhino with your user's permissions. Read shared scripts before
  running them.
- Alien's agent server listens only on your own machine (`127.0.0.1:9876`).

## Updating

Quit Rhino and run the installer from the new version: it replaces `Alien.gha` and keeps your scripts, settings and
generated library. Suite files you haven't edited are updated the next time the panel opens.

## Uninstalling

- **Windows:** *Settings > Apps > Alien for Grasshopper > Uninstall* (close Rhino first). It asks whether to remove
  your settings too; your scripts are always kept.
- **macOS:** quit Rhino and delete `Alien.gha` from Grasshopper's Libraries folder (in Grasshopper:
  *File > Special Folders > Components Folder*). You can also delete `~/Library/Application Support/Alien`.

## Installing by hand

The zip (`Alien-0.02-alpha.zip`) has `Alien.gha` and the older install scripts (`install-windows.bat`,
`install-mac.command`), for machines where the installers can't run.

- **Windows:** right-click the zip > *Properties* > tick **Unblock** > *OK*, extract it, then copy `Alien.gha` to
  `%APPDATA%\Grasshopper\Libraries` (or run `install-windows.bat`).
- **macOS:** copy `Alien.gha` into the Libraries folder above, then run
  `xattr -d com.apple.quarantine "<that folder>/Alien.gha"` (or run `install-mac.command`).

## Troubleshooting

- **No Alien tab in Grasshopper:**
  - Check that `Alien.gha` is in the Libraries folder.
  - On Windows, check that Rhino runs on .NET Core (`SetDotNetRuntime`). If you installed by hand, check the file is
    unblocked.
- **The installer says Rhino is running:** quit Rhino fully. On macOS, use Cmd+Q and check the Dock.
- **The panel doesn't appear:** in `settings.json`, add `"panel": "rhino"` to dock it in Rhino instead, then open it
  from the node's PANEL button.
- **"No OpenRouter key", 401 or "insufficient credits":** open the panel's Help (**?**): link a key, check your credit,
  or make a new key.
- **Port 9876 in use:** set `"port"` in `settings.json` to another number and restart Rhino.
- **The map node fails:** it needs the internet, and works only for Victorian addresses. Vicmap's services
  sometimes change; the node falls back to a second source and says so in its `info` output.

Report problems, with what you did and what you saw, to the person who sent you this build.
