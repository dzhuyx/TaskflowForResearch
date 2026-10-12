import AppKit
import WebKit

/// The hub window: every tracker in the hub's list (one per project), the unblocked tasks grouped by who acts, and
/// the details of the task clicked; served by `taskflow.py --hub --app` (static/hub.html). A project's name, or a
/// task's "Open on its board", opens the board through the app's own taskflow:// handling, which selects the task.
/// One hub window at a time.
final class HubController: NSWindowController, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    static var shared: HubController?
    static let logName = URL(fileURLWithPath: "/Hub")      // its log: ~/Library/Logs/Taskflow/Hub.log
    private let web: WKWebView
    private var server: BoardServer?

    static func show() {
        let hub = shared ?? HubController()
        shared = hub
        hub.showWindow(nil)
        hub.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private init() {
        let config = WKWebViewConfiguration()
        let content = WKUserContentController()
        content.addUserScript(WKUserScript(source: "window.TASKFLOW_NATIVE = true;", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController = content
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1360, height: 820), configuration: config)
        if #available(macOS 13.3, *) { web.isInspectable = true }   // right-click › Inspect Element, for debugging
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 820),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Taskflow Hub"
        window.contentView = web
        window.minSize = NSSize(width: 640, height: 420)
        window.isReleasedWhenClosed = false
        // On every desktop, so the hub is wherever the user is (2026-10-11); the boards stay on the desktop each was put on.
        window.collectionBehavior = [.canJoinAllSpaces]
        super.init(window: window)
        content.add(self, name: "taskflow")                     // removed when the window closes, ending the retain cycle
        web.navigationDelegate = self
        window.delegate = self
        if !window.setFrameUsingName("Taskflow Hub") { window.center() }
        window.setFrameAutosaveName("Taskflow Hub")
        startServer()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    // MARK: Server

    private func startServer() {
        server?.stop()
        showMessage("Starting the hub…")
        let s = BoardServer(tracker: HubController.logName, arguments: ["--hub", "--app"])
        s.onReady = { [weak self] url in self?.web.load(URLRequest(url: url)) }
        s.onExit = { [weak self] message in self?.showMessage(message, isError: true) }
        server = s
        s.start()
    }

    func stopServer() {
        server?.stop()
        server = nil
    }

    private func showMessage(_ text: String, isError: Bool = false) {
        web.loadHTMLString(TrackerDocument.messageHTML(text, isError: isError, what: "hub"), baseURL: nil)
    }

    func windowWillClose(_ notification: Notification) {
        stopServer()
        web.configuration.userContentController.removeScriptMessageHandler(forName: "taskflow")
        HubController.shared = nil
    }

    // MARK: Page

    // The hub's own server may be shown; taskflow:// links open boards in the app; anything else is refused.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let u = action.request.url
        if u?.scheme?.lowercased() == "taskflow", let link = u?.absoluteString {
            decisionHandler(.cancel)
            DispatchQueue.main.async { (NSApp.delegate as? AppDelegate)?.openTaskflowLink(link) }
            return
        }
        if u?.scheme == "about" || (u?.host == "127.0.0.1" && u?.port == server?.port) {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let msg = message.body as? [String: Any], let kind = msg["kind"] as? String else { return }
        switch kind {
        case "copy":                                             // the task details' Copy prompt / resume command / id
            if let text = msg["text"] as? String {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            }
        case "loaded":
            BoardServer.log(HubController.logName, "hub loaded in the app: \(msg["projects"] ?? "?") projects; unblocked tasks: "
                            + "\(msg["me"] ?? "?") for you, \(msg["handoffs"] ?? "?") hand-offs, \(msg["claude"] ?? "?") for Claude, \(msg["other"] ?? "?") other")
        case "error":
            BoardServer.log(HubController.logName, "page error: \(msg["text"] ?? "?")")
        default:
            break
        }
    }

    // MARK: Menu actions (reached through the responder chain while the hub is the key window)

    @objc func reloadBoard(_ sender: Any?) {   // restarts the hub's server too, so the page and the server always match
        startServer()
    }
    @objc func actualSize(_ sender: Any?) { web.pageZoom = 1.0 }
    @objc func zoomIn(_ sender: Any?) { web.pageZoom = min(web.pageZoom * 1.15, 3.0) }
    @objc func zoomOut(_ sender: Any?) { web.pageZoom = max(web.pageZoom / 1.15, 0.4) }
}
