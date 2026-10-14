// Draws the installers' artwork from the Alien mark (docs/brand/alien-mark.svg): icon PNGs for the Windows .ico,
// the Windows wizard's side and header images, and the macOS installer background (light and dark).
//   swift installer/art/render.swift <repo root> <out dir>
import AppKit
import CoreGraphics
import Foundation

let args = CommandLine.arguments
let root = URL(fileURLWithPath: args[1]), out = URL(fileURLWithPath: args[2])
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
let svg = try String(contentsOf: root.appendingPathComponent("docs/brand/alien-mark.svg"))
let d = svg.components(separatedBy: " d=\"")[1].components(separatedBy: "\"")[0]
let view = CGRect(x: 105, y: 64, width: 1266, height: 1368)  // the SVG's viewBox

// The path data has only absolute M, C and Z.
func markPath() -> CGPath {
    let p = CGMutablePath()
    let scanner = Scanner(string: d)
    scanner.charactersToBeSkipped = CharacterSet(charactersIn: " ,\n")
    var cmd: Character = " "
    func num() -> CGFloat { CGFloat(scanner.scanDouble() ?? 0) }
    while !scanner.isAtEnd {
        if let c = scanner.scanCharacter(), "MCZ".contains(c) { cmd = c } else { break }
        switch cmd {
        case "M": p.move(to: CGPoint(x: num(), y: num()))
        case "C":
            while true {
                let save = scanner.currentIndex
                guard let x1 = scanner.scanDouble() else { scanner.currentIndex = save; break }
                let c1 = CGPoint(x: x1, y: num()), c2 = CGPoint(x: num(), y: num()), e = CGPoint(x: num(), y: num())
                p.addCurve(to: e, control1: c1, control2: c2)
            }
        case "Z": p.closeSubpath()
        default: break
        }
    }
    return p
}
let mark = markPath()

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255, blue: CGFloat(hex & 255) / 255, alpha: a)
}
let green = rgb(0x00c853), dark = rgb(0x1e1e1e)

/// A bitmap context with a top-left origin (like the SVG), drawn by `draw`, saved as PNG.
func render(_ name: String, _ w: Int, _ h: Int, alpha: Bool = true, _ draw: (CGContext) -> Void) {
    let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: (alpha ? CGImageAlphaInfo.premultipliedLast : CGImageAlphaInfo.noneSkipLast).rawValue)!
    ctx.translateBy(x: 0, y: CGFloat(h)); ctx.scaleBy(x: 1, y: -1)
    ctx.setShouldAntialias(true); ctx.interpolationQuality = .high
    draw(ctx)
    let image = ctx.makeImage()!
    let rep = NSBitmapImageRep(cgImage: image)
    try! rep.representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent(name + ".png"))
    if !alpha { writeBMP(image, out.appendingPathComponent(name + ".bmp")) }
}

/// A 24-bit bottom-up BMP, the kind NSIS's wizard pages load (sips writes top-down ones, which crash makensis).
func writeBMP(_ image: CGImage, _ url: URL) {
    let w = image.width, h = image.height, row = (w * 3 + 3) & ~3
    var px = [UInt8](repeating: 0, count: w * h * 4)
    let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))  // memory rows run top to bottom
    var data = Data()
    func u32(_ v: Int) { var x = UInt32(v).littleEndian; data.append(Data(bytes: &x, count: 4)) }
    func u16(_ v: Int) { var x = UInt16(v).littleEndian; data.append(Data(bytes: &x, count: 2)) }
    data.append(contentsOf: [0x42, 0x4D]); u32(54 + row * h); u32(0); u32(54)          // file header
    u32(40); u32(w); u32(h); u16(1); u16(24); u32(0); u32(row * h); u32(2835); u32(2835); u32(0); u32(0)  // info header
    for y in stride(from: h - 1, through: 0, by: -1) {                                  // bottom row first
        var line = [UInt8](repeating: 0, count: row)
        for x in 0..<w {
            let i = (y * w + x) * 4
            line[x * 3] = px[i + 2]; line[x * 3 + 1] = px[i + 1]; line[x * 3 + 2] = px[i]  // BGR
        }
        data.append(contentsOf: line)
    }
    try! data.write(to: url)
}

/// The mark fitted into a box (aspect kept, centred), filled in one colour.
func drawMark(_ ctx: CGContext, _ box: CGRect, _ colour: CGColor) {
    let k = min(box.width / view.width, box.height / view.height)
    ctx.saveGState()
    ctx.translateBy(x: box.midX - view.width * k / 2, y: box.midY - view.height * k / 2)
    ctx.scaleBy(x: k, y: k); ctx.translateBy(x: -view.minX, y: -view.minY)
    ctx.addPath(mark); ctx.setFillColor(colour); ctx.fillPath()
    ctx.restoreGState()
}

/// Text centred on centreX, its baseline at y.
func text(_ ctx: CGContext, _ s: String, _ size: CGFloat, _ colour: CGColor, centreX: CGFloat, y: CGFloat, bold: Bool = true) {
    let font = NSFont.systemFont(ofSize: size, weight: bold ? .bold : .regular)
    let str = NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: NSColor(cgColor: colour)!])
    let line = CTLineCreateWithAttributedString(str)
    let width = CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil))
    ctx.saveGState()
    ctx.textMatrix = CGAffineTransform(scaleX: 1, y: -1)  // undo the flip for text
    ctx.textPosition = CGPoint(x: centreX - width / 2, y: y)
    CTLineDraw(line, ctx)
    ctx.restoreGState()
}

// Icons for the .ico (green mark, transparent ground)
for s in [16, 24, 32, 48, 64, 128, 256] {
    render("icon-\(s)", s, s) { ctx in
        let pad = CGFloat(s) / 16
        drawMark(ctx, CGRect(x: pad, y: pad, width: CGFloat(s) - 2 * pad, height: CGFloat(s) - 2 * pad), green)
    }
}

// Windows wizard: welcome/finish side image (164 x 314) and the header image (150 x 57)
render("wizard-side", 164, 314, alpha: false) { ctx in
    ctx.setFillColor(dark); ctx.fill(CGRect(x: 0, y: 0, width: 164, height: 314))
    drawMark(ctx, CGRect(x: 32, y: 70, width: 100, height: 108), green)
    text(ctx, "Alien", 24, rgb(0xd7d7d7), centreX: 82, y: 222)
    text(ctx, "for Grasshopper", 11, rgb(0x8c8c8c), centreX: 82, y: 242, bold: false)
}
render("wizard-header", 150, 57, alpha: false) { ctx in
    ctx.setFillColor(rgb(0xffffff)); ctx.fill(CGRect(x: 0, y: 0, width: 150, height: 57))
    drawMark(ctx, CGRect(x: 104, y: 9, width: 38, height: 40), green)
}

// macOS installer background: the mark faded into the lower left, for light and dark appearance
for (name, colour) in [("pkg-background", rgb(0x00c853, 0.16)), ("pkg-background-dark", rgb(0x00c853, 0.22))] {
    render(name, 620, 420) { ctx in drawMark(ctx, CGRect(x: -40, y: 250, width: 230, height: 250), colour) }
}
print("rendered into \(out.path)")
