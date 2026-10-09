// macOS: list Rhino's windows (id, title, size, on screen) so `screencapture -x -o -l <id> out.png` can grab one,
// e.g. the Grasshopper window or the Alien sidecar (untitled, 340 wide). Needs Screen Recording permission.
//   swift windows.swift
import CoreGraphics
let list = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as! [[String: Any]]
for w in list {
    let owner = w[kCGWindowOwnerName as String] as? String ?? ""
    guard owner.lowercased().contains("rhino") else { continue }
    let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let w0 = b["Width"] as? Double ?? 0, h0 = b["Height"] as? Double ?? 0
    if w0 < 60 || h0 < 60 { continue }
    let onscreen = (w[kCGWindowIsOnscreen as String] as? Bool) ?? false
    print(w[kCGWindowNumber as String]!, "|", w[kCGWindowName as String] ?? "", "|", Int(w0), "x", Int(h0), "|", onscreen ? "on screen" : "hidden")
}
