import AppKit
import WebKit

// Loads the board in an offscreen WKWebView and runs the gesture checks in tests.js; prints the JSON result.
let url = URL(string: CommandLine.arguments[1])!
let js = try! String(contentsOfFile: CommandLine.arguments[2], encoding: .utf8)
let app = NSApplication.shared
final class Probe: NSObject, WKNavigationDelegate {
    let web: WKWebView
    let window: NSWindow
    init(url: URL) {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1400, height: 880), configuration: cfg)
        window = NSWindow(contentRect: NSRect(x: -3000, y: -3000, width: 1400, height: 880), styleMask: [.borderless], backing: .buffered, defer: false)
        super.init()
        window.contentView = web
        window.orderBack(nil)
        web.navigationDelegate = self
        web.load(URLRequest(url: url))
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            webView.callAsyncJavaScript(js, arguments: [:], in: nil, in: .page) { result in
                switch result {
                case .success(let v): print(v ?? "nil")
                case .failure(let e): print("JS error: \(e)")
                }
                exit(0)
            }
        }
    }
}
let probe = Probe(url: url)
DispatchQueue.main.asyncAfter(deadline: .now() + 180) { print("timeout"); exit(2) }
app.run()
