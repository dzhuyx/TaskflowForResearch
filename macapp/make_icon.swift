// Draws the Taskflow app icon as a 1024 x 1024 PNG, in the board's own style: on a light
// tile, your ticked review (blue bar) and Claude's ticked task (clay bar) lead into a ready hand-off (purple, outlined).
// Usage: swift make_icon.swift <output.png>
import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon_1024.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
let gc = NSGraphicsContext(bitmapImageRep: rep)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = gc
gc.cgContext.translateBy(x: 0, y: size); gc.cgContext.scaleBy(x: 1, y: -1)   // y counts down, as on the page

func color(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255, blue: CGFloat(hex & 0xff) / 255, alpha: a)
}
// the board's colours: who acts (static/index.html), the tick, the ink, a card's title lines and the edges
let blue = color(0x2563eb), clay = color(0xc96442), purple = color(0xa21caf), green = color(0x1a7f4b), ink = color(0x1d1d1f)

func shadowed(_ blur: CGFloat, _ dy: CGFloat, _ alpha: CGFloat, _ draw: () -> Void) {
    NSGraphicsContext.saveGraphicsState()
    let s = NSShadow()
    s.shadowColor = color(0x000000, alpha); s.shadowBlurRadius = blur; s.shadowOffset = NSSize(width: 0, height: -dy)
    s.set(); draw()
    NSGraphicsContext.restoreGraphicsState()
}

// Body: macOS icon grid (824 pt square, 100 pt margin), white fading to a faint grey, with a hairline edge and a soft shadow.
let body = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185)
shadowed(28, 12, 0.22) { color(0xffffff).setFill(); body.fill() }
NSGradient(starting: color(0xffffff), ending: color(0xe4e4ea))!.draw(in: body, angle: -90)
body.lineWidth = 4; color(0x000000, 0.10).setStroke(); body.stroke()

// A card as the board draws one: white, a hairline border, its colour bar down the left, a tick box, an id line and a
// title line; a ready card is outlined in its colour, with a soft halo.
func card(_ r: NSRect, _ bar: NSColor, ticked: Bool, ready: Bool = false, barW: CGFloat = 40) {
    let radius: CGFloat = 40
    let p = NSBezierPath(roundedRect: r, xRadius: radius, yRadius: radius)
    if ready {
        bar.withAlphaComponent(0.20).setFill()
        NSBezierPath(roundedRect: r.insetBy(dx: -16, dy: -16), xRadius: radius + 16, yRadius: radius + 16).fill()
    }
    shadowed(18, 6, 0.10) { color(0xffffff).setFill(); p.fill() }
    NSGraphicsContext.saveGraphicsState(); p.addClip()
    bar.setFill(); NSRect(x: r.minX, y: r.minY, width: barW, height: r.height).fill()
    NSGraphicsContext.restoreGraphicsState()
    p.lineWidth = ready ? 14 : 6; (ready ? bar : color(0xbcbcc2)).setStroke(); p.stroke()

    let x0 = r.minX + barW + 30, bs = min(76, r.height * 0.36)
    let box = NSRect(x: x0, y: r.minY + 32, width: bs, height: bs)
    let b = NSBezierPath(roundedRect: box, xRadius: bs * 0.22, yRadius: bs * 0.22)
    if ticked {
        green.setFill(); b.fill()
        let tick = NSBezierPath()
        tick.lineWidth = bs * 0.15; tick.lineCapStyle = .round; tick.lineJoinStyle = .round
        tick.move(to: NSPoint(x: box.minX + bs * 0.24, y: box.midY + bs * 0.02))
        tick.line(to: NSPoint(x: box.minX + bs * 0.43, y: box.maxY - bs * 0.26))
        tick.line(to: NSPoint(x: box.maxX - bs * 0.22, y: box.minY + bs * 0.27))
        color(0xffffff).setStroke(); tick.stroke()
    } else {
        b.lineWidth = bs * 0.12; ink.setStroke(); b.stroke()
    }
    let lh = max(20, bs * 0.30), tx = box.maxX + 26
    ink.withAlphaComponent(0.85).setFill()
    NSBezierPath(roundedRect: NSRect(x: tx, y: box.midY - lh / 2, width: min(130, r.maxX - tx - 40), height: lh), xRadius: lh / 2, yRadius: lh / 2).fill()
    color(0xd1d1d6).setFill()
    NSBezierPath(roundedRect: NSRect(x: x0, y: box.maxY + 28, width: (r.maxX - 40 - x0) * 0.62, height: lh), xRadius: lh / 2, yRadius: lh / 2).fill()
}

// An edge as the board draws one: from a prerequisite's right side, bending halfway, into one arrowhead.
func edge(from: NSPoint, to: NSPoint) {
    let tip = NSPoint(x: to.x - 40, y: to.y), mid = from.x + (tip.x - from.x) * 0.5
    let p = NSBezierPath()
    p.lineWidth = 24; p.lineCapStyle = .round
    p.move(to: from)
    p.curve(to: tip, controlPoint1: NSPoint(x: mid, y: from.y), controlPoint2: NSPoint(x: mid, y: to.y))
    color(0x8e8e93).setStroke(); p.stroke()
    let head = NSBezierPath()
    head.move(to: to); head.line(to: NSPoint(x: to.x - 64, y: to.y - 42)); head.line(to: NSPoint(x: to.x - 64, y: to.y + 42)); head.close()
    color(0x8e8e93).setFill(); head.fill()
}

let review = NSRect(x: 160, y: 246, width: 296, height: 188), task = NSRect(x: 160, y: 590, width: 296, height: 188)
let handoff = NSRect(x: 592, y: 392, width: 284, height: 240)
edge(from: NSPoint(x: review.maxX + 4, y: review.midY), to: NSPoint(x: handoff.minX - 26, y: handoff.midY))
edge(from: NSPoint(x: task.maxX + 4, y: task.midY), to: NSPoint(x: handoff.minX - 26, y: handoff.midY))
card(review, blue, ticked: true)
card(task, clay, ticked: true)
card(handoff, purple, ticked: false, ready: true, barW: 36)

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
