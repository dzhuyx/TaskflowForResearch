// The calendar of deadlines' helper: the hub's server runs it to read and write, through EventKit, the events it
// keeps in that calendar (deadline_calendar.py). It is built into Taskflow.app
// (macapp/build_app.sh), so macOS asks once whether Taskflow may use the calendars. One JSON request on stdin, one JSON
// answer on stdout:
//   {"op": "read", "calendar": {"id": ID} or {"titles": [NAME, …], "source": ACCOUNT}, "from": DAY, "to": DAY, "marker": TEXT}
//     → {"calendar": {"id", "title", "source"}, "events": [{"id", "title", "date", "allDay", "notes", "alarms"}]}: the
//       calendar's events from DAY to DAY whose notes hold the marker; a calendar given by titles (and the name of its
//       account, such as Google, when given) must be the only one with such a name there, in any case
//   {"op": "apply", "calendar": {"id": ID}, "marker": TEXT, "create": [EVENT], "update": [EVENT with "id"], "delete": [ID]}
//     → {"created": n, "updated": n, "deleted": n}; an event to update or delete must be in that calendar and hold the
//       marker, else nothing is written
//   EVENT: {"title", "date": DAY, "notes", "alarms": [seconds from the day's start]}, written all day and free; DAY is YYYY-MM-DD.
// Anything wrong → {"error": TEXT, "code": "access" | "calendar" | "request" | "save"}, exit status 1, nothing written.
import EventKit
import Foundation

struct Failure: Error {
    let code: String
    let message: String
}

let store = EKEventStore()
let days = Calendar.current

func reply(_ object: [String: Any], status: Int32 = 0) -> Never {
    let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data + Data("\n".utf8))
    exit(status)
}

func day(_ text: Any?) throws -> Date {
    let parts = ((text as? String) ?? "").split(separator: "-").compactMap { Int($0) }
    guard parts.count == 3, let date = days.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2])) else {
        throw Failure(code: "request", message: "Not a date written YYYY-MM-DD: \(text ?? "nothing")")
    }
    return date
}

func dayText(_ date: Date) -> String {
    let c = days.dateComponents([.year, .month, .day], from: date)
    return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
}

/// Full access to the calendars; the first time, macOS asks (for Taskflow) and this waits for the answer.
func ensureAccess() throws {
    switch EKEventStore.authorizationStatus(for: .event) {
    case .fullAccess:
        return
    case .notDetermined:
        var answer: (Bool, String?)?
        store.requestFullAccessToEvents { granted, error in
            DispatchQueue.main.async { answer = (granted, error?.localizedDescription) }
        }
        while answer == nil { _ = RunLoop.main.run(mode: .default, before: Date(timeIntervalSinceNow: 0.2)) }
        if answer!.0 { return }
        throw Failure(code: "access", message: "Taskflow was not allowed to use the calendars" + (answer!.1.map { " (\($0))" } ?? ""))
    case .writeOnly:
        throw Failure(code: "access", message: "Taskflow may only add events to the calendars, and it needs full access to find its own")
    default:
        throw Failure(code: "access", message: "Taskflow is not allowed to use the calendars")
    }
}

func calendar(_ spec: Any?) throws -> EKCalendar {
    let spec = spec as? [String: Any] ?? [:]
    let found: EKCalendar
    if let id = spec["id"] as? String {
        guard let c = store.calendar(withIdentifier: id) else {
            throw Failure(code: "calendar", message: "The calendar Taskflow uses is no longer among this Mac's calendars")
        }
        found = c
    } else {
        let titles = Set((spec["titles"] as? [String] ?? []).map { $0.lowercased() })
        let source = (spec["source"] as? String)?.lowercased()
        let named = store.calendars(for: .event).filter {
            titles.contains($0.title.lowercased()) && (source == nil || $0.source.title.lowercased() == source)
        }
        guard named.count == 1 else {
            let all = store.calendars(for: .event).map { "\($0.title) (\($0.source.title))" }.sorted()
            throw Failure(code: "calendar", message: named.isEmpty
                ? "No calendar on this Mac is named \(titles.sorted().joined(separator: " or "))\(source.map { " in \($0)" } ?? ""); its calendars are: \(all.joined(separator: ", "))"
                : "More than one calendar is named so: \(named.map { "\($0.title) (\($0.source.title))" }.sorted().joined(separator: ", "))")
        }
        found = named[0]
    }
    guard found.allowsContentModifications else {
        throw Failure(code: "calendar", message: "The calendar \(found.title) (\(found.source.title)) cannot be written to")
    }
    return found
}

func read(_ c: EKCalendar, from: Date, to: Date, marker: String) -> [[String: Any]] {
    var seen = Set<String>(), out: [[String: Any]] = []
    var start = from
    while start < to {     // a year at a time: EventKit searches at most four years at once
        let end = min(days.date(byAdding: .year, value: 1, to: start)!, to)
        for e in store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: [c])) {
            guard let notes = e.notes, notes.contains(marker), seen.insert(e.calendarItemIdentifier).inserted else { continue }
            out.append(["id": e.calendarItemIdentifier, "title": e.title ?? "", "date": dayText(e.startDate), "allDay": e.isAllDay,
                        "notes": notes, "alarms": (e.alarms ?? []).filter { $0.absoluteDate == nil }.map { $0.relativeOffset }])
        }
        start = end
    }
    return out
}

func fill(_ e: EKEvent, _ spec: [String: Any], in c: EKCalendar) throws {
    guard let title = spec["title"] as? String, let notes = spec["notes"] as? String, let alarms = spec["alarms"] as? [Double] else {
        throw Failure(code: "request", message: "An event needs a title, notes and alarms")
    }
    let date = try day(spec["date"])
    e.calendar = c
    e.title = title
    e.isAllDay = true
    e.startDate = date
    e.endDate = date
    e.notes = notes
    if c.supportedEventAvailabilities.contains(.free) { e.availability = .free }
    e.alarms = alarms.map { EKAlarm(relativeOffset: $0) }
}

/// The hub's own event: in this calendar, with the marker in its notes.
func own(_ id: Any?, in c: EKCalendar, marker: String) throws -> EKEvent {
    guard let id = id as? String, let e = store.calendarItem(withIdentifier: id) as? EKEvent,
          e.calendar.calendarIdentifier == c.calendarIdentifier, (e.notes ?? "").contains(marker) else {
        throw Failure(code: "request", message: "Not one of Taskflow's events in \(c.title): \(id ?? "no id")")
    }
    return e
}

do {
    guard let request = try? JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as? [String: Any],
          let op = request["op"] as? String, let marker = request["marker"] as? String, !marker.isEmpty else {
        throw Failure(code: "request", message: "The request is not JSON with an op and a marker")
    }
    try ensureAccess()
    let c = try calendar(request["calendar"])
    let info = ["id": c.calendarIdentifier, "title": c.title, "source": c.source.title]
    switch op {
    case "read":
        reply(["calendar": info, "events": read(c, from: try day(request["from"]), to: try day(request["to"]), marker: marker)])
    case "apply":
        let create = request["create"] as? [[String: Any]] ?? [], update = request["update"] as? [[String: Any]] ?? []
        let delete = request["delete"] as? [Any] ?? []
        do {    // checked first, then written, then committed together
            let made = try create.map { spec -> EKEvent in let e = EKEvent(eventStore: store); try fill(e, spec, in: c); return e }
            let changed = try update.map { spec -> EKEvent in let e = try own(spec["id"], in: c, marker: marker); try fill(e, spec, in: c); return e }
            let gone = try delete.map { try own($0, in: c, marker: marker) }
            for e in made + changed { try store.save(e, span: .thisEvent, commit: false) }
            for e in gone { try store.remove(e, span: .thisEvent, commit: false) }
            try store.commit()
        } catch let f as Failure {
            store.reset()
            throw f
        } catch {
            store.reset()
            throw Failure(code: "save", message: "The calendar could not be written: \(error.localizedDescription)")
        }
        reply(["calendar": info, "created": create.count, "updated": update.count, "deleted": delete.count])
    default:
        throw Failure(code: "request", message: "Unknown op: \(op)")
    }
} catch let f as Failure {
    reply(["error": f.message, "code": f.code], status: 1)
} catch {
    reply(["error": error.localizedDescription, "code": "request"], status: 1)
}
