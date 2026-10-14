Alien 0.02 (alpha) for Rhino 8 + Grasshopper: installing by hand
================================================================

The easy way is the installer: Alien-0.02-alpha-Setup.exe (Windows) or Alien-0.02-alpha.pkg (macOS), from the same
release page. This zip is for machines where those can't run.

Quick start
  macOS:   quit Rhino (Cmd+Q), double-click install-mac.command (right-click > Open if macOS warns).
  Windows: close Rhino, unblock the zip (Properties > Unblock) before extracting, double-click install-windows.bat.
  Then:    start Rhino, type Grasshopper, place an Alien node from the Alien tab, click PANEL on it, and click ? (Help)
           in the panel: it shows how to make an OpenRouter account and link your key.

Full instructions, costs, troubleshooting and uninstalling: INSTALL.md

What's in this folder
  Alien.gha               the plugin (one file for macOS and Windows)
  install-mac.command     macOS script installer
  install-windows.bat     Windows script installer (runs install-windows.ps1)
  install-windows.ps1
  settings.example.json   example settings (your real file is created on first use)
  INSTALL.md              instructions

Alpha: tested on Rhino 8 for macOS; Windows is untested.
