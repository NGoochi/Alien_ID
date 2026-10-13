Alien 0.01 (alpha) for Rhino 8 + Grasshopper
=============================================

Live Python / C# script nodes that a model writes from a plain-English description, plus a suite of architectural
nodes (Victorian site maps, site views, 3D objects and textures from a prompt, scale figures and trees).

Quick start
  macOS:   quit Rhino (Cmd+Q), double-click install-mac.command (right-click > Open if macOS warns).
  Windows: close Rhino, unblock the zip (Properties > Unblock) before extracting, double-click install-windows.bat.
  Then:    start Rhino, type Grasshopper, place an Alien node from the Alien tab, and add your OpenRouter key
           (right-click the node > Open settings file).

Full instructions, costs, troubleshooting and uninstalling: INSTALL.md

What's in this folder
  Alien.gha               the plugin (one file for macOS and Windows)
  install-mac.command     macOS installer
  install-windows.bat     Windows installer (runs install-windows.ps1)
  install-windows.ps1
  settings.example.json   example settings (your real file is created on first use)
  INSTALL.md              instructions

Alpha: tested on Rhino 8 for macOS; Windows is untested.
