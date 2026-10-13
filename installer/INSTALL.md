# Installing Alien 0.01 (alpha)

Alien is a Grasshopper plugin for Rhino 8: nodes whose logic is a Python or C# script, written for you from a plain
English description. It comes with a suite of architectural nodes (Victorian site maps with terrain, streets and
trees; plan, section and elevation views; 3D objects and material textures from a prompt; scale figures and trees).

**This is an alpha.** It is built and tested on Rhino 8 for macOS. The same plugin file is meant to run on Windows,
but Windows hasn't been tested yet. Please report what breaks.

## What you need

- **Rhino 8** (macOS or Windows) with Grasshopper.
- **Windows only:** Rhino must run on .NET Core, which is Rhino 8's default. To check, type `SetDotNetRuntime` in
  Rhino's command line. If it says `NETFramework`, choose `NETCore` and restart Rhino.
- **An OpenRouter API key** for describe-a-node and the AI nodes (3D objects, textures, figures, trees). Get one at
  [openrouter.ai](https://openrouter.ai) (Keys). Describe-a-node can use a local Ollama model instead.
- **An internet connection** for the map node and the AI nodes.

## Install on macOS

1. **Quit Rhino completely** (Cmd+Q; closing its windows is not enough).
2. **Unzip** the download and open the `Alien-0.01-alpha` folder.
3. **Double-click `install-mac.command`.** It copies `Alien.gha` into Grasshopper's Libraries folder and clears
   macOS's download quarantine flag, which would stop Rhino loading it.
   - macOS may say the script "cannot be opened because it is from an unidentified developer". If so, right-click it,
     choose **Open**, then **Open** again.
   - Or run it from Terminal: `bash ~/Downloads/Alien-0.01-alpha/install-mac.command`.

**Install by hand instead:** copy `Alien.gha` into
`~/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)/Libraries`.
In Grasshopper, **File > Special Folders > Components Folder** opens it. Then clear the quarantine flag in Terminal:
`xattr -d com.apple.quarantine "<that folder>/Alien.gha"`.

## Install on Windows

1. **Close Rhino.**
2. **Unblock the zip before extracting it:** right-click the downloaded zip, choose **Properties**, tick **Unblock**,
   then **OK**. Otherwise Windows marks every file as downloaded and Grasshopper refuses to load a blocked plugin.
3. **Extract** the zip and open the `Alien-0.01-alpha` folder.
4. **Double-click `install-windows.bat`.** It runs `install-windows.ps1`, which copies `Alien.gha` to
   `%APPDATA%\Grasshopper\Libraries` and unblocks it.

**Install by hand instead:** copy `Alien.gha` to `%APPDATA%\Grasshopper\Libraries`. In Grasshopper,
**File > Special Folders > Components Folder** opens it. Then right-click `Alien.gha`, choose **Properties**, tick
**Unblock**, then **OK**.

## First run

1. **Start Rhino** and type `Grasshopper`.
2. **Place an Alien node:** it's on the **Alien** tab, under **Script**. The first time, Alien asks for a **script
   folder**. Your nodes are saved there as plain script files. A new empty folder is fine, for example
   `Documents/Alien scripts`.
3. **Add your OpenRouter key:** right-click the node, choose **Open settings file**, and paste your key into
   `"openrouterKey"`. The file is read again as soon as you save it; there's no need to restart.
   `settings.example.json` shows the other settings.
   - macOS: `~/Library/Application Support/Alien/settings.json`
   - Windows: `%APPDATA%\Alien\settings.json`
4. **Open the Alien panel:** click **PANEL** on the node, or double-click it. The panel sits beside the Grasshopper
   window.
   - Describe a node in plain English ("divide a curve into 20 points"), or describe a change to the selected one.
   - Place a preloaded node from the **Library**. The suite is copied into your script folder's `suite` subfolder
     the first time the panel opens.
   - The **Generated** tab keeps the 3D objects, materials and drawings the suite makes.
5. **Try the map node:** place `vic_site_map` from the Library and type a Victorian address. Suggestions appear
   once you've typed the street number. It is free and needs no key.

**Optional, connect an AI agent:** for Claude Code, run
`claude mcp add --transport http alien http://127.0.0.1:9876/mcp`. Agents can then build and wire nodes while you
watch.

## What costs money

Only steps that call a model cost anything, and they're paid to OpenRouter with your key:

| Step | Cost |
|---|---|
| Describe-a-node (Claude Haiku 5.5) | about US$0.001 per node |
| `object_3d` (Claude Sonnet 5.5) | about US$0.01–0.02 per design |
| `material_texture`, `scale_figure`, `scale_tree` | about US$0.034 per image |
| `vic_site_map`, `site_views`, `levels`, `branch_picker` | free |

The AI nodes only call a model when you press their **Generate** button. Everything they make is cached, so moving
sliders and reopening files is free.

## Privacy and security

- Your API key stays in `settings.json`. It is never written into `.gh` files or scripts.
- Prompts go through OpenRouter, only to providers that don't store or train on them.
- Scripts, including ones a model writes, run inside Rhino with your user's permissions. Read shared scripts before
  running them.
- Alien's agent server listens only on your own machine (`127.0.0.1:9876`).

## Updating

Quit Rhino and run the installer from the new version. It replaces `Alien.gha`. Your scripts, settings and generated
library stay. Suite files you haven't edited are updated the next time the panel opens.

## Uninstalling

Quit Rhino and delete `Alien.gha` from the Libraries folder (see the install steps above for where it is). You can
also delete the `Alien` settings folder and your script folder.

## Troubleshooting

- **No Alien tab in Grasshopper:**
  - Check that `Alien.gha` is in the Libraries folder.
  - On Windows, check it is unblocked and that Rhino runs on .NET Core (`SetDotNetRuntime`).
  - On macOS, clear the quarantine flag (see the macOS install steps).
- **"Rhino is running" from the installer:** quit Rhino fully. On macOS, use Cmd+Q, and check the Dock.
- **The panel doesn't appear:** in `settings.json`, add `"panel": "rhino"` to dock it in Rhino instead, then open it
  from the node's PANEL button.
- **"No OpenRouter key":** add `"openrouterKey"` to `settings.json` and save.
- **Port 9876 in use:** set `"port"` in `settings.json` to another number and restart Rhino.
- **The map node fails:** it needs the internet, and works only for Victorian addresses. Vicmap's services
  sometimes change; the node falls back to a second source and says so in its `info` output.

Report problems, with what you did and what you saw, to the person who sent you this build.
