import AppKit
import WebKit
// Loads a page offscreen at a given width, runs a script (e.g. a click), then saves a PNG snapshot.
let url = URL(string: CommandLine.arguments[1])!, js = CommandLine.arguments[2], out = CommandLine.arguments[3]
let width = Double(CommandLine.arguments[4]) ?? 1360
let app = NSApplication.shared
final class Snap: NSObject, WKNavigationDelegate {
    let web: WKWebView; let window: NSWindow
    init(url: URL) {
        let cfg = WKWebViewConfiguration(); cfg.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: 820), configuration: cfg)
        window = NSWindow(contentRect: NSRect(x: -4000, y: -4000, width: width, height: 820), styleMask: [.borderless], backing: .buffered, defer: false)
        super.init(); window.contentView = web; window.orderBack(nil); web.navigationDelegate = self; web.load(URLRequest(url: url))
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            webView.evaluateJavaScript(js) { _, _ in
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    webView.takeSnapshot(with: nil) { img, err in
                        guard let img = img, let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                              let png = rep.representation(using: .png, properties: [:]) else { print("snapshot failed: \(String(describing: err))"); exit(1) }
                        try! png.write(to: URL(fileURLWithPath: out)); print("saved \(out)"); exit(0)
                    }
                }
            }
        }
    }
}
let snap = Snap(url: url)
DispatchQueue.main.asyncAfter(deadline: .now() + 30) { print("timeout"); exit(2) }
app.run()
