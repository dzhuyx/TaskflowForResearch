import AppKit
import WebKit

/// One open tracker: a window with the board, backed by its own `taskflow.py --app` server.
/// The document never writes the file itself; the board's server does, with its conflict check and backups.
@objc(TrackerDocument)
final class TrackerDocument: NSDocument, WKNavigationDelegate {
    private var webView: WKWebView?
    private var server: BoardServer?
    private var bridge: ScriptBridge?
    private static var nextTopLeft: NSPoint?
    private var boardLoaded = false        // the page has reported "loaded" since it was last (re)loaded
    private var pendingItem: String?       // a task to select once the board has loaded (from the hub)

    override class var autosavesInPlace: Bool { false }
    override class var preservesVersions: Bool { false }
    override var isDocumentEdited: Bool { false }

    override func read(from url: URL, ofType typeName: String) throws {
        // Nothing to load here: the board's server reads the file.
    }

    override func data(ofType typeName: String) throws -> Data {
        throw CocoaError(.featureUnsupported)
    }

    override func makeWindowControllers() {
        guard let url = fileURL else { return }
        let config = WKWebViewConfiguration()
        let content = WKUserContentController()
        let bridge = ScriptBridge(self)
        content.add(bridge, name: "taskflow")
        content.addUserScript(WKUserScript(source: "window.TASKFLOW_NATIVE = true; window.TASKFLOW_POSITIONS = \(Positions.load(for: url));",
                                           injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController = content
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1400, height: 880), configuration: config)
        web.navigationDelegate = self
        if #available(macOS 13.3, *) { web.isInspectable = true }   // right-click › Inspect Element, for debugging

        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 880),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.contentView = web
        window.minSize = NSSize(width: 720, height: 460)
        window.tabbingIdentifier = "TaskflowTracker"
        let controller = NSWindowController(window: window)
        controller.shouldCascadeWindows = false
        addWindowController(controller)
        let frameName = "Taskflow " + url.path
        if !window.setFrameUsingName(frameName) {          // first time for this file: cascade from the last new window
            if let p = TrackerDocument.nextTopLeft {
                TrackerDocument.nextTopLeft = window.cascadeTopLeft(from: p)
            } else {
                window.center()
                TrackerDocument.nextTopLeft = window.cascadeTopLeft(from: NSPoint(x: window.frame.minX, y: window.frame.maxY))
            }
        }
        window.setFrameAutosaveName(frameName)

        self.webView = web
        self.bridge = bridge
        showMessage("Starting the board for \(url.lastPathComponent)…")
        startServer()
    }

    override func close() {
        stopServer()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "taskflow")
        super.close()
    }

    // MARK: Server

    private func startServer() {
        guard let url = fileURL else { return }
        server?.stop()
        let s = BoardServer(tracker: url)
        s.onReady = { [weak self] boardURL in
            self?.boardLoaded = false
            self?.webView?.load(URLRequest(url: boardURL))
        }
        s.onExit = { [weak self] message in self?.showMessage(message, isError: true) }
        server = s
        s.start()
    }

    func stopServer() {
        server?.stop()
        server = nil
    }

    private func showMessage(_ text: String, isError: Bool = false) {
        boardLoaded = false
        webView?.loadHTMLString(TrackerDocument.messageHTML(text, isError: isError), baseURL: nil)
    }

    /// The page shown while a server starts, or after it stops (also used by the hub).
    static func messageHTML(_ text: String, isError: Bool, what: String = "board") -> String {
        let esc = text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        return """
        <!doctype html><meta charset="utf-8"><style>
        body { font: 14px -apple-system, Helvetica, sans-serif; color: \(isError ? "#a50e0e" : "#5f6368"); margin: 48px; }
        pre { white-space: pre-wrap; font: 12px ui-monospace, Menlo, monospace; color: #3c4043; }
        @media (prefers-color-scheme: dark) { body { background: #202124; color: \(isError ? "#f28b82" : "#bdc1c6"); } pre { color: #e8eaed; } }
        </style><body>\(isError ? "<h2>The \(what) stopped</h2>" : "")<pre>\(esc)</pre>\(isError ? "<p>View › Reload Board (⌘R) starts it again.</p>" : "")</body>
        """
    }

    /// Selects a task on the board (clicked in the hub), now or as soon as the board has loaded.
    func reveal(item: String) {
        if boardLoaded { goTo(item) } else { pendingItem = item }
    }

    private func goTo(_ item: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: [item]),
              let list = String(data: data, encoding: .utf8) else { return }
        run("window.taskflowGoTo && window.taskflowGoTo(\(list)[0])")   // the id is passed as JSON, never as code
    }

    // MARK: Messages from the page

    func receive(_ body: Any) {
        guard let msg = body as? [String: Any], let kind = msg["kind"] as? String, let url = fileURL else { return }
        switch kind {
        case "copy":
            if let text = msg["text"] as? String {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            }
        case "positions":
            if let value = msg["value"] { Positions.save(value, for: url) }
        case "loaded":
            BoardServer.log(url, "board loaded in the app: \(msg["items"] ?? "?") items, \(msg["sessions"] ?? "?") sessions, \(msg["savedPositions"] ?? 0) saved box positions")
            boardLoaded = true
            if let item = pendingItem {
                pendingItem = nil
                goTo(item)
            }
        case "error":
            BoardServer.log(url, "page error: \(msg["text"] ?? "?")")
        default:
            break
        }
    }

    // Only the board's own server may be shown; anything else is refused.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let u = action.request.url
        if u?.scheme == "about" || (u?.host == "127.0.0.1" && u?.port == server?.port) {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        boardLoaded = false
        webView.reload()
    }

    // MARK: Menu actions (reached through the responder chain)

    private func run(_ js: String) { webView?.evaluateJavaScript(js, completionHandler: nil) }

    @objc func reloadBoard(_ sender: Any?) {   // restarts the board's server too, so the page and the server always match
        boardLoaded = false
        startServer()
    }
    @objc func relayoutBoard(_ sender: Any?) { run("document.getElementById('b-layout') && document.getElementById('b-layout').click()") }
    @objc func fitBoard(_ sender: Any?) { run("document.getElementById('b-fit') && document.getElementById('b-fit').click()") }
    @objc func showActiveTasks(_ sender: Any?) { run("document.getElementById('b-active') && document.getElementById('b-active').click()") }
    @objc func nextActiveTask(_ sender: Any?) { run("document.getElementById('b-next') && document.getElementById('b-next').click()") }
    @objc func previousActiveTask(_ sender: Any?) { run("document.getElementById('b-prev') && document.getElementById('b-prev').click()") }
    @objc func focusSearch(_ sender: Any?) { run("var s = document.getElementById('f-search'); if (s) { s.focus(); s.select(); }") }
    @objc func addHookmarkLinks(_ sender: Any?) { run("document.getElementById('b-hooks') && document.getElementById('b-hooks').click()") }
    @objc func actualSize(_ sender: Any?) { webView?.pageZoom = 1.0 }
    @objc func zoomIn(_ sender: Any?) { if let w = webView { w.pageZoom = min(w.pageZoom * 1.15, 3.0) } }
    @objc func zoomOut(_ sender: Any?) { if let w = webView { w.pageZoom = max(w.pageZoom / 1.15, 0.4) } }

    @objc func openInDefaultApp(_ sender: Any?) {   // in TaskPaper, or, when no app opens .taskpaper files, in TextEdit
        guard let url = fileURL else { return }
        if NSWorkspace.shared.urlForApplication(toOpen: url) != nil {
            NSWorkspace.shared.open(url)
        } else if let editor = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.TextEdit") {
            NSWorkspace.shared.open([url], withApplicationAt: editor, configuration: NSWorkspace.OpenConfiguration())
        }
    }

    @objc func showInFinder(_ sender: Any?) {
        if let url = fileURL { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }

    @objc func checkTracker(_ sender: Any?) {
        guard let url = fileURL else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: Config.python)
            p.arguments = [Config.script, url.path, "--check"]
            let out = Pipe()
            p.standardOutput = out
            p.standardError = out
            var text = "", status: Int32 = -1
            do {
                try p.run()
                let data = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                text = String(decoding: data, as: UTF8.self)
                status = p.terminationStatus
            } catch {
                text = "Could not run the check: \(error.localizedDescription)"
            }
            DispatchQueue.main.async { self.showCheck(text, ok: status == 0) }
        }
    }

    private func showCheck(_ text: String, ok: Bool) {
        let alert = NSAlert()
        alert.messageText = ok ? "No problems found" : "The check found problems"
        alert.informativeText = fileURL?.lastPathComponent ?? ""
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 620, height: 300))
        scroll.hasVerticalScroller = true
        let tv = NSTextView(frame: scroll.bounds)
        tv.isEditable = false
        tv.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        tv.string = text
        tv.autoresizingMask = [.width]
        scroll.documentView = tv
        alert.accessoryView = scroll
        if let w = windowControllers.first?.window { alert.beginSheetModal(for: w) } else { alert.runModal() }
    }
}

/// Forwards page messages without the content controller keeping the document alive.
final class ScriptBridge: NSObject, WKScriptMessageHandler {
    weak var document: TrackerDocument?
    init(_ document: TrackerDocument) { self.document = document }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        document?.receive(message.body)
    }
}

/// Box positions per tracker, kept in the app's defaults (the board's port, and so its browser storage, changes each launch).
enum Positions {
    private static func key(_ url: URL) -> String { "positions " + url.path }

    static func load(for url: URL) -> String {
        guard let data = UserDefaults.standard.data(forKey: key(url)),
              (try? JSONSerialization.jsonObject(with: data)) != nil,
              let s = String(data: data, encoding: .utf8) else { return "null" }
        return s
    }

    static func save(_ value: Any, for url: URL) {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value) else { return }
        UserDefaults.standard.set(data, forKey: key(url))
    }
}

/// Runs `python3 taskflow.py <tracker> --app` (or, for the hub, `taskflow.py --hub --app`) and reports its URL. Closing
/// its standard input stops it cleanly (the server waits for any write in progress); if the app dies, the pipe closes
/// and the server stops too.
final class BoardServer {
    let tracker: URL                       // names the log file; for the hub, /Hub
    private let arguments: [String]
    var onReady: ((URL) -> Void)?
    var onExit: ((String) -> Void)?
    private(set) var port: Int?
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private var buffer = Data()
    private var ready = false
    private var stopping = false

    var isRunning: Bool { process.isRunning }

    init(tracker: URL, arguments: [String]? = nil) {
        self.tracker = tracker
        self.arguments = arguments ?? [tracker.path, "--app"]
    }

    static func logURL(_ tracker: URL) -> URL {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Taskflow")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent(tracker.deletingPathExtension().lastPathComponent + ".log")
    }

    static func log(_ tracker: URL, _ line: String) {
        let url = logURL(tracker)
        let stamp = ISO8601DateFormatter.string(from: Date(), timeZone: .current, formatOptions: [.withInternetDateTime])
        let data = Data("\(stamp) \(line)\n".utf8)
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile(); h.write(data); try? h.close()
        } else {
            try? data.write(to: url)
        }
    }

    private func logTail() -> String {
        guard let text = try? String(contentsOf: BoardServer.logURL(tracker), encoding: .utf8) else { return "" }
        return text.split(separator: "\n", omittingEmptySubsequences: false).suffix(15).joined(separator: "\n")
    }

    func start() {
        if let problem = Config.problem { onExit?(problem); return }
        let logURL = BoardServer.logURL(tracker)
        if let size = (try? FileManager.default.attributesOfItem(atPath: logURL.path))?[.size] as? Int, size > 2_000_000 {
            try? FileManager.default.removeItem(at: logURL)
        }
        BoardServer.log(tracker, "starting the board server for \(tracker.path)")
        process.executableURL = URL(fileURLWithPath: Config.python)
        process.arguments = ["-u", Config.script] + arguments
        process.currentDirectoryURL = Config.folder
        process.standardInput = input
        process.standardOutput = output
        if let h = try? FileHandle(forWritingTo: logURL) { h.seekToEndOfFile(); process.standardError = h }
        output.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            DispatchQueue.main.async { self?.consume(d) }
        }
        process.terminationHandler = { [weak self] p in
            let status = p.terminationStatus
            DispatchQueue.main.async { self?.ended(status) }
        }
        do {
            try process.run()
        } catch {
            onExit?("Could not start Python (\(Config.python)): \(error.localizedDescription)")
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
            guard let self = self, !self.ready, !self.stopping else { return }
            self.onExit?("The board's server did not start within 20 seconds.\nLog: \(logURL.path)\n\n\(self.logTail())")
            self.stop()
        }
    }

    private func consume(_ d: Data) {
        if d.isEmpty { return }
        buffer.append(d)
        while let nl = buffer.firstIndex(of: 0x0a) {
            let line = String(decoding: buffer[buffer.startIndex..<nl], as: UTF8.self)
            buffer.removeSubrange(buffer.startIndex...nl)
            if !ready, line.hasPrefix("TASKFLOW_URL "), let url = URL(string: String(line.dropFirst(13)).trimmingCharacters(in: .whitespaces)) {
                ready = true
                port = url.port
                BoardServer.log(tracker, "board server ready at \(url.absoluteString) (pid \(process.processIdentifier))")
                onReady?(url)
            }
        }
    }

    private func ended(_ status: Int32) {
        output.fileHandleForReading.readabilityHandler = nil
        if stopping { return }
        BoardServer.log(tracker, "board server stopped unexpectedly (exit status \(status))")
        onExit?("The board's server stopped (exit status \(status)).\nLog: \(BoardServer.logURL(tracker).path)\n\n\(logTail())")
    }

    func stop() {
        if stopping { return }
        stopping = true
        try? input.fileHandleForWriting.close()
        let p = process
        DispatchQueue.global().asyncAfter(deadline: .now() + 3) {
            if p.isRunning { p.terminate() }
        }
    }
}
