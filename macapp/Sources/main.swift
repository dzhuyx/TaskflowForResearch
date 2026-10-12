// Taskflow.app: opens TaskPaper trackers as dependency-graph boards, one window (or tab) per file, and the hub,
// which lists every tracker (one per project) and all of the user's active tasks.
//
// Each window shows the existing taskflow board (static/index.html) in a WKWebView, served by its own
// `taskflow.py <file> --app` process; the hub (static/hub.html) is served by `taskflow.py --hub --app`. All parsing,
// editing, conflict checks, backups, Hookmark links and job sweeps stay in taskflow.py; this app only adds windows,
// menus and file handling.

import AppKit
import WebKit

enum Config {
    static let info = Bundle.main.infoDictionary ?? [:]
    static var python: String { info["TaskflowPython"] as? String ?? "" }
    static var script: String { info["TaskflowScript"] as? String ?? "" }
    static var folder: URL { URL(fileURLWithPath: script).deletingLastPathComponent() }
    static let trackerType = "com.hogbaysoftware.taskpaper"

    /// A description of what is missing, or nil if Python and taskflow.py are where Info.plist says.
    static var problem: String? {
        if !FileManager.default.isExecutableFile(atPath: python) {
            return "Python was not found at \(python). Taskflow needs it to run the board. Reinstall it there, or rebuild the app with macapp/build_app.sh, which picks a Python (or set PYTHON=/path/to/python3)."
        }
        if !FileManager.default.fileExists(atPath: script) {
            return "taskflow.py was not found at \(script). Rebuild the app with build_app.sh from the taskflow folder's new location."
        }
        return nil
    }
}

/// Opens every .taskpaper file as a tracker, whatever type the system assigns to it.
final class TaskflowDocumentController: NSDocumentController {
    override func typeForContents(of url: URL) throws -> String {
        if url.pathExtension.lowercased() == "taskpaper" { return Config.trackerType }
        return try super.typeForContents(of: url)
    }

    override func documentClass(forType typeName: String) -> AnyClass? {
        typeName == Config.trackerType ? TrackerDocument.self : super.documentClass(forType: typeName)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let recentMenu = NSMenu(title: "Open Recent")

    func applicationWillFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = buildMainMenu()
        NSAppleEventManager.shared().setEventHandler(self, andSelector: #selector(handleURLEvent(_:withReply:)),
                                                     forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    /// Opens taskflow:///absolute/path/to/tracker.taskpaper links (written like file:// links: spaces as %20).
    @objc func handleURLEvent(_ event: NSAppleEventDescriptor, withReply reply: NSAppleEventDescriptor) {
        openTaskflowLink(event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue ?? "")
    }

    /// Opens a tracker's board from a taskflow:// link; with ?item=<id> (the hub's task links) it selects that task.
    func openTaskflowLink(_ text: String) {
        guard let url = URL(string: text), url.scheme?.lowercased() == "taskflow", (url.host ?? "").isEmpty,
              url.path.lowercased().hasSuffix(".taskpaper"), FileManager.default.fileExists(atPath: url.path) else {
            let alert = NSAlert()
            alert.messageText = "Taskflow cannot open this link"
            alert.informativeText = "\(text)\n\nA Taskflow link names an existing tracker, like taskflow:///Users/name/folder/effort_tracker.taskpaper (spaces written as %20)."
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
            return
        }
        let item = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "item" })?.value
        NSApp.activate(ignoringOtherApps: true)
        NSDocumentController.shared.openDocument(withContentsOf: URL(fileURLWithPath: url.path), display: true) { doc, _, error in
            if let error = error { NSApp.presentError(error) }
            doc?.showWindows()
            if let item = item, !item.isEmpty { (doc as? TrackerDocument)?.reveal(item: item) }
        }
    }

    @objc func showHub(_ sender: Any?) { HubController.show() }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let problem = Config.problem {
            let alert = NSAlert()
            alert.messageText = "Taskflow cannot start"
            alert.informativeText = problem
            alert.runModal()
            NSApp.terminate(nil)
            return
        }
        // Show the hub if nothing was opened from Finder or a link, or restored.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            if NSDocumentController.shared.documents.isEmpty {
                HubController.show()
            }
        }
    }

    func applicationShouldOpenUntitledFile(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { HubController.show() }
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        for doc in NSDocumentController.shared.documents {
            (doc as? TrackerDocument)?.stopServer()
        }
        HubController.shared?.stopServer()
    }

    // MARK: Open Recent

    func menuNeedsUpdate(_ menu: NSMenu) {
        guard menu === recentMenu else { return }
        menu.removeAllItems()
        let urls = NSDocumentController.shared.recentDocumentURLs
        for url in urls {
            let item = NSMenuItem(title: url.lastPathComponent, action: #selector(openRecent(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = url
            item.toolTip = url.path
            item.image = NSWorkspace.shared.icon(forFile: url.path)
            item.image?.size = NSSize(width: 16, height: 16)
            menu.addItem(item)
        }
        if !urls.isEmpty { menu.addItem(.separator()) }
        let clear = NSMenuItem(title: "Clear Menu", action: #selector(clearRecent(_:)), keyEquivalent: "")
        clear.target = self
        clear.isEnabled = !urls.isEmpty
        menu.addItem(clear)
    }

    @objc func openRecent(_ sender: NSMenuItem) {
        guard let url = sender.representedObject as? URL else { return }
        NSDocumentController.shared.openDocument(withContentsOf: url, display: true) { _, _, error in
            if let error = error { NSApp.presentError(error) }
        }
    }

    @objc func clearRecent(_ sender: Any?) {
        NSDocumentController.shared.clearRecentDocuments(sender)
    }

    @objc func openReadme(_ sender: Any?) {
        NSWorkspace.shared.open(Config.folder.appendingPathComponent("README.md"))
    }

    @objc func openTaskflowFolder(_ sender: Any?) {
        NSWorkspace.shared.open(Config.folder)
    }

    // MARK: Menus

    private func item(_ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = [.command],
                      target: AnyObject? = nil) -> NSMenuItem {
        let i = NSMenuItem(title: title, action: action, keyEquivalent: key)
        i.keyEquivalentModifierMask = mods
        i.target = target
        return i
    }

    private func buildMainMenu() -> NSMenu {
        let main = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(item("About Taskflow", #selector(NSApplication.orderFrontStandardAboutPanel(_:))))
        appMenu.addItem(.separator())
        let services = NSMenu()
        let servicesItem = item("Services", nil)
        servicesItem.submenu = services
        NSApp.servicesMenu = services
        appMenu.addItem(servicesItem)
        appMenu.addItem(.separator())
        appMenu.addItem(item("Hide Taskflow", #selector(NSApplication.hide(_:)), "h"))
        appMenu.addItem(item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]))
        appMenu.addItem(item("Show All", #selector(NSApplication.unhideAllApplications(_:))))
        appMenu.addItem(.separator())
        appMenu.addItem(item("Quit Taskflow", #selector(NSApplication.terminate(_:)), "q"))

        let file = NSMenu(title: "File")
        file.addItem(item("Open…", #selector(NSDocumentController.openDocument(_:)), "o"))
        let recentItem = item("Open Recent", nil)
        recentMenu.delegate = self
        recentItem.submenu = recentMenu
        file.addItem(recentItem)
        file.addItem(.separator())
        file.addItem(item("Close", #selector(NSWindow.performClose(_:)), "w"))
        file.addItem(.separator())
        file.addItem(item("Open Tracker File", #selector(TrackerDocument.openInDefaultApp(_:)), "o", [.command, .shift]))
        file.addItem(item("Show in Finder", #selector(TrackerDocument.showInFinder(_:)), "r", [.command, .shift]))
        file.addItem(item("Check Tracker", #selector(TrackerDocument.checkTracker(_:)), "k"))
        file.addItem(item("Add Hookmark Links", #selector(TrackerDocument.addHookmarkLinks(_:))))

        let edit = NSMenu(title: "Edit")
        edit.addItem(item("Undo", Selector(("undo:")), "z"))
        edit.addItem(item("Redo", Selector(("redo:")), "z", [.command, .shift]))
        edit.addItem(.separator())
        edit.addItem(item("Cut", #selector(NSText.cut(_:)), "x"))
        edit.addItem(item("Copy", #selector(NSText.copy(_:)), "c"))
        edit.addItem(item("Paste", #selector(NSText.paste(_:)), "v"))
        edit.addItem(item("Select All", #selector(NSText.selectAll(_:)), "a"))
        edit.addItem(.separator())
        edit.addItem(item("Find…", #selector(TrackerDocument.focusSearch(_:)), "f"))

        let view = NSMenu(title: "View")
        view.addItem(item("Reload Board", #selector(TrackerDocument.reloadBoard(_:)), "r"))
        view.addItem(item("Re-layout", #selector(TrackerDocument.relayoutBoard(_:)), "l", [.command, .option]))
        view.addItem(item("Fit Board to Window", #selector(TrackerDocument.fitBoard(_:)), "f", [.command, .option]))
        view.addItem(.separator())
        view.addItem(item("Show Active Tasks", #selector(TrackerDocument.showActiveTasks(_:)), "a", [.command, .option]))
        view.addItem(item("Next Active Task", #selector(TrackerDocument.nextActiveTask(_:)), "]"))
        view.addItem(item("Previous Active Task", #selector(TrackerDocument.previousActiveTask(_:)), "["))
        view.addItem(.separator())
        view.addItem(item("Actual Size", #selector(TrackerDocument.actualSize(_:)), "0"))
        view.addItem(item("Zoom In", #selector(TrackerDocument.zoomIn(_:)), "="))
        view.addItem(item("Zoom Out", #selector(TrackerDocument.zoomOut(_:)), "-"))
        view.addItem(.separator())
        view.addItem(item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]))

        let window = NSMenu(title: "Window")
        window.addItem(item("Hub", #selector(showHub(_:)), "h", [.command, .shift], target: self))
        window.addItem(.separator())
        window.addItem(item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"))
        window.addItem(item("Zoom", #selector(NSWindow.performZoom(_:))))
        window.addItem(.separator())
        window.addItem(item("Bring All to Front", #selector(NSApplication.arrangeInFront(_:))))
        NSApp.windowsMenu = window

        let help = NSMenu(title: "Help")
        help.addItem(item("Taskflow README", #selector(openReadme(_:)), target: self))
        help.addItem(item("Show Taskflow Folder", #selector(openTaskflowFolder(_:)), target: self))
        NSApp.helpMenu = help

        for (title, menu) in [("Taskflow", appMenu), ("File", file), ("Edit", edit), ("View", view), ("Window", window), ("Help", help)] {
            let top = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            top.submenu = menu
            main.addItem(top)
        }
        return main
    }
}

let application = NSApplication.shared
let documentController = TaskflowDocumentController()   // the first one created becomes NSDocumentController.shared
let appDelegate = AppDelegate()
application.delegate = appDelegate
application.setActivationPolicy(.regular)
application.run()
