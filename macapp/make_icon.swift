// Draws the Taskflow app icon (a small dependency graph with a ticked box) as a 1024 x 1024 PNG.
// Usage: swift make_icon.swift <output.png>
import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon_1024.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

func color(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255, blue: CGFloat(hex & 0xff) / 255, alpha: a)
}

// Body: macOS icon grid (824 pt square, 100 pt margin), rounded, with a soft shadow.
let body = NSRect(x: 100, y: 100, width: 824, height: 824)
let shape = NSBezierPath(roundedRect: body, xRadius: 185, yRadius: 185)
NSGraphicsContext.current!.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowColor = color(0x000000, 0.28)
shadow.shadowBlurRadius = 24
shadow.shadowOffset = NSSize(width: 0, height: -10)
shadow.set()
color(0x4f5bd5).setFill()
shape.fill()
NSGraphicsContext.current!.restoreGraphicsState()
NSGradient(starting: color(0x6a78f2), ending: color(0x8a4fe0))!.draw(in: shape, angle: -60)

// Nodes: two prerequisites on the left, one item on the right.
let nodes = [NSRect(x: 190, y: 590, width: 270, height: 150),
             NSRect(x: 190, y: 290, width: 270, height: 150),
             NSRect(x: 570, y: 440, width: 270, height: 150)]
let edge = NSBezierPath()
edge.lineWidth = 26
edge.lineCapStyle = .round
for src in nodes[0...1] {
    edge.move(to: NSPoint(x: src.maxX, y: src.midY))
    edge.curve(to: NSPoint(x: nodes[2].minX - 34, y: nodes[2].midY),
               controlPoint1: NSPoint(x: src.maxX + 80, y: src.midY),
               controlPoint2: NSPoint(x: nodes[2].minX - 110, y: nodes[2].midY))
}
color(0xffffff, 0.85).setStroke()
edge.stroke()
let head = NSBezierPath()   // arrow head into the right node
head.move(to: NSPoint(x: nodes[2].minX - 6, y: nodes[2].midY))
head.line(to: NSPoint(x: nodes[2].minX - 58, y: nodes[2].midY + 36))
head.line(to: NSPoint(x: nodes[2].minX - 58, y: nodes[2].midY - 36))
head.close()
color(0xffffff, 0.85).setFill()
head.fill()

for (i, r) in nodes.enumerated() {
    let p = NSBezierPath(roundedRect: r, xRadius: 34, yRadius: 34)
    (i == 2 ? color(0xffffff) : color(0xeef0ff)).setFill()
    p.fill()
    // a checkbox in each node's corner; the first one is ticked
    let box = NSRect(x: r.minX + 28, y: r.maxY - 28 - 58, width: 58, height: 58)
    let b = NSBezierPath(roundedRect: box, xRadius: 12, yRadius: 12)
    if i == 0 {
        color(0x188038).setFill(); b.fill()
        let tick = NSBezierPath()
        tick.lineWidth = 11; tick.lineCapStyle = .round; tick.lineJoinStyle = .round
        tick.move(to: NSPoint(x: box.minX + 13, y: box.midY + 1))
        tick.line(to: NSPoint(x: box.minX + 25, y: box.minY + 15))
        tick.line(to: NSPoint(x: box.maxX - 12, y: box.maxY - 14))
        color(0xffffff).setStroke(); tick.stroke()
    } else {
        b.lineWidth = 7; color(0x5f6368).setStroke(); b.stroke()
    }
    // two text lines
    color(0x9aa0f0, i == 2 ? 0.9 : 0.75).setFill()
    NSBezierPath(roundedRect: NSRect(x: r.minX + 110, y: r.maxY - 64, width: r.width - 145, height: 20), xRadius: 10, yRadius: 10).fill()
    NSBezierPath(roundedRect: NSRect(x: r.minX + 28, y: r.minY + 30, width: r.width - 90, height: 20), xRadius: 10, yRadius: 10).fill()
}

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
