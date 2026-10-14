// Render the Alien panel page (src/Ui/pane.html) off screen in WebKit (the engine Rhino for Mac's panel uses) with a
// given state, run some JavaScript, and save a full-height PNG. For checking panel changes without bringing Rhino to
// the front. Get a real state from a running panel with `panel.py --state out.json`.
//   swift tools/pane_snapshot.swift <state.json> <out.png> ["js to run, e.g. showView('help')"] [width] [max height]
import AppKit
import WebKit

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: pane_snapshot.swift state.json out.png [js] [width]"); exit(2) }
let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let html = try String(contentsOf: root.appendingPathComponent("src/Ui/pane.html"))
let state = try String(contentsOf: URL(fileURLWithPath: args[1]))
let js = args.count > 3 ? args[3] : ""
let width = args.count > 4 ? Double(args[4]) ?? 340 : 340
let maxHeight = args.count > 5 ? Double(args[5]) : nil

_ = NSApplication.shared
let config = WKWebViewConfiguration()
// The page posts to C# through window.eto; here messages are only kept for inspection.
config.userContentController.addUserScript(WKUserScript(
    source: "window.eto = { postMessage: m => (window.__posted = window.__posted || []).push(m) };",
    injectionTime: .atDocumentStart, forMainFrameOnly: true))
let web = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: 900), configuration: config)
let window = NSWindow(contentRect: web.frame, styleMask: [.borderless], backing: .buffered, defer: false)
window.contentView = web

final class Done: NSObject, WKNavigationDelegate {
    var loaded = false
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) { loaded = true }
}
let done = Done()
web.navigationDelegate = done
web.loadHTMLString(html, baseURL: nil)
func spin(_ seconds: Double) { RunLoop.main.run(until: Date().addingTimeInterval(seconds)) }
while !done.loaded { spin(0.05) }

var height: Double = 900
var finished = false
web.evaluateJavaScript("window.alien.state(\(state)); \(js); document.body.scrollHeight") { r, e in
    if let e = e { print("js error:", e) }
    height = min((r as? Double) ?? 900, maxHeight ?? .infinity)
    finished = true
}
while !finished { spin(0.05) }
web.setFrameSize(NSSize(width: width, height: height))
window.setContentSize(NSSize(width: width, height: height))
spin(0.6)
let cfg = WKSnapshotConfiguration()
cfg.rect = NSRect(x: 0, y: 0, width: width, height: height)
var saved = false
web.takeSnapshot(with: cfg) { image, error in
    if let image = image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
       let png = rep.representation(using: .png, properties: [:]) {
        try? png.write(to: URL(fileURLWithPath: args[2]))
        print("saved \(args[2]) (\(Int(width)) x \(Int(height)))")
    } else { print("snapshot failed:", error as Any) }
    saved = true
}
while !saved { spin(0.05) }
