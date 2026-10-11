// JSON stdin/stdout bridge. No permissions prompts unless explicitly run with --authorize.
// Compile only during development; running the helper accesses the user's EventKit store.
import Foundation
import EventKit
import Darwin

// A kernel alarm terminates even a blocked stdin/EventKit/commit call, without
// the bridge or a responsive dispatch queue. Only interactive authorization
// is exempt: it cannot write items and the operator may need time to respond.
if !CommandLine.arguments.contains("--authorize") {
    signal(SIGALRM, SIG_DFL)
    var alarms = sigset_t()
    sigemptyset(&alarms)
    sigaddset(&alarms, SIGALRM)
    sigprocmask(SIG_UNBLOCK, &alarms, nil)
    alarm(7)
}

struct BridgeError: Error { let message: String }
struct MutationFailure: Error { let outcome: String }

// All EventKit saves in prepare use commit:false. Only commit errors are uncertain.
func commitChanges(prepare: () throws -> Void, commit: () throws -> Void, reset: () -> Void) throws {
    do { try prepare() }
    catch { reset(); throw MutationFailure(outcome: "not_executed") }
    do { try commit() }
    catch { reset(); throw MutationFailure(outcome: "unknown") }
}

let null = NSNull()
let zone = TimeZone(identifier: "Asia/Shanghai")!
var calendar = Calendar(identifier: .gregorian)
calendar.timeZone = zone
let iso = ISO8601DateFormatter()
iso.timeZone = zone
let fractional = ISO8601DateFormatter()
fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

func string(_ value: Any?) throws -> String {
    guard let result = value as? String, !result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
        throw BridgeError(message: "Invalid string")
    }
    return result
}
func parseDate(_ value: Any?) throws -> Date {
    let raw = try string(value)
    if let result = iso.date(from: raw) ?? fractional.date(from: raw) { return result }
    let parser = DateFormatter()
    parser.locale = Locale(identifier: "en_US_POSIX"); parser.timeZone = zone
    parser.dateFormat = "yyyy-MM-dd"; parser.isLenient = false
    guard raw.count == 10, let result = parser.date(from: raw), parser.string(from: result) == raw else {
        throw BridgeError(message: "Invalid date")
    }
    return result
}
func day(_ value: Date) -> String {
    let parser = DateFormatter()
    parser.locale = Locale(identifier: "en_US_POSIX"); parser.timeZone = zone; parser.dateFormat = "yyyy-MM-dd"
    return parser.string(from: value)
}
func components(_ value: Any?) throws -> DateComponents? {
    if value == nil || value is NSNull { return nil }
    let raw = try string(value), date = try parseDate(raw)
    var result = calendar.dateComponents(raw.count == 10 ? [.year, .month, .day] : [.year, .month, .day, .hour, .minute, .second], from: date)
    result.calendar = calendar; result.timeZone = zone
    return result
}
func matchesOriginal(_ old: [String: Any], _ live: [String: Any]) -> Bool {
    for (key, value) in old {
        guard let current = live[key] else { return false }
        if ["modified_at", "start", "end", "due"].contains(key),
           let left = value as? String, let right = current as? String,
           left.contains("T"), right.contains("T") {
            guard let a = try? parseDate(left), let b = try? parseDate(right), a == b else { return false }
        } else if !NSDictionary(dictionary: [key: value]).isEqual(to: [key: current]) { return false }
    }
    return true
}
func effectiveChange(_ change: [String: Any]) -> [String: Any] {
    change.filter { !($0.value is NSNull) }
}
func encodedJSON(_ value: Any) throws -> Data {
    try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
}
func emit(_ value: Any) {
    if let data = try? encodedJSON(value) {
        FileHandle.standardOutput.write(data)
    }
}
func publicItem(_ row: [String: Any]) -> [String: Any] {
    row.filter { ["id", "exists", "completed", "title", "list", "due", "modified_at"].contains($0.key) }
}
func generationOriginal(_ row: [String: Any]) -> [String: Any] {
    row.filter { ["source", "title", "list", "due", "completed", "modified_at", "start", "end"].contains($0.key) }
}
struct AppleNames {
    let lists: [String: String]
    let calendars: [String: String]
    init(_ value: Any? = nil) throws {
        let config: [String: Any]
        if let value {
            guard let object = value as? [String: Any], Set(object.keys).isSubset(of: ["lists", "calendars"]) else {
                throw BridgeError(message: "Invalid Apple mapping")
            }
            config = object
        } else { config = [:] }
        func mapping(_ key: String, _ logical: [String]) throws -> [String: String] {
            let overrides: [String: String]
            if let raw = config[key] {
                guard let values = raw as? [String: String], Set(values.keys).isSubset(of: Set(logical)) else {
                    throw BridgeError(message: "Invalid Apple mapping")
                }
                overrides = values
            } else { overrides = [:] }
            let result = Dictionary(uniqueKeysWithValues: logical.map { ($0, overrides[$0] ?? $0) })
            guard Set(result.values).count == result.count,
                  result.values.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                      && $0.unicodeScalars.count <= 128 && !$0.contains("\0") }) else {
                throw BridgeError(message: "Ambiguous Apple mapping")
            }
            return result
        }
        lists = try mapping("lists", ["收件箱", "工作", "生活", "家庭", "等待", "长期"])
        calendars = try mapping("calendars", ["工作", "生活", "家庭"])
    }
    func actual(_ logical: String, _ kind: EKEntityType) -> String {
        (kind == .reminder ? lists : calendars)[logical] ?? logical
    }
    func logical(_ actual: String, _ kind: EKEntityType) -> String {
        (kind == .reminder ? lists : calendars).first { $0.value == actual }?.key ?? actual
    }
    func snapshot(_ row: [String: Any]) -> [String: Any] {
        var result = row
        if let title = row["list"] as? String {
            result["list"] = logical(title, row["source"] as? String == "reminder" ? .reminder : .event)
        }
        return result
    }
}
func collectionDocument(_ rows: [[String: Any]], at: Date) throws -> [String: Any] {
    // Filter completion before both the limit and originals. Undated/overdue
    // reminders stay in the input; only calendar reads have a time window.
    let active = rows.filter { $0["source"] as? String == "event" || $0["completed"] as? Bool == false }
    guard active.count <= 500 else { throw BridgeError(message: "collection_limit_exceeded") }
    var originals = [String: [String: Any]](), ids = Set<String>()
    for row in active {
        let id = try string(row["id"])
        guard ids.insert(id).inserted else { throw BridgeError(message: "Ambiguous item identifier") }
        if row["modified_at"] != nil, !(row["modified_at"] is NSNull) { originals[id] = generationOriginal(row) }
    }
    return ["items": active, "originals": originals, "collected_at": fractional.string(from: at)]
}
func permitted(_ kind: EKEntityType) -> Bool {
    EKEventStore.authorizationStatus(for: kind) == .fullAccess
}
func captureFields(_ payload: [String: Any]) throws -> (title: String, notes: String, url: URL) {
    let id = try string(payload["request_id"])
    guard UUID(uuidString: id) != nil, let url = URL(string: "ollo://capture/" + id) else {
        throw BridgeError(message: "Invalid capture identity")
    }
    return (try string(payload["text"]),
            "记下时间：" + (try string(payload["created_at"])) + "\n来源：" + (try string(payload["source"])), url)
}
func captureLookupResult(_ target: String, rows: [(String?, String)], fullAccess: Bool,
                         complete: Bool, checkedAt: Date) -> [String: Any] {
    guard fullAccess, complete else { return ["status": "unavailable", "reason": "reminder_read_incomplete"] }
    let matches = rows.filter { $0.0 == target }
    guard matches.count <= 1 else { return ["status": "unavailable", "reason": "ambiguous_capture_url"] }
    if let match = matches.first {
        guard !match.1.isEmpty else { return ["status": "unavailable", "reason": "missing_item_id"] }
        return ["status": "found", "item_id": match.1]
    }
    return ["status": "absent", "checked_at": fractional.string(from: checkedAt)]
}
func authorize(_ store: EKEventStore) throws {
    for kind in [EKEntityType.reminder, .event] {
        let done = DispatchSemaphore(value: 0)
        var allowed = false
        let callback: EKEventStoreRequestAccessCompletionHandler = { granted, _ in allowed = granted; done.signal() }
        if kind == .reminder { store.requestFullAccessToReminders(completion: callback) }
        else { store.requestFullAccessToEvents(completion: callback) }
        guard done.wait(timeout: .now() + 60) == .success, allowed else { throw BridgeError(message: "Permission unavailable") }
    }
}

final class Bridge {
    let store: EKEventStore
    let names: AppleNames
    init(_ store: EKEventStore, names: AppleNames) { self.store = store; self.names = names }
    func lookup(_ id: String) throws -> EKCalendarItem? {
        let matches = store.calendarItems(withExternalIdentifier: id)
        guard matches.count <= 1 else { throw BridgeError(message: "Ambiguous external identifier") }
        return matches.first ?? store.calendarItem(withIdentifier: id)
    }
    func stableID(_ item: EKCalendarItem) -> String {
        let external = item.calendarItemExternalIdentifier ?? ""
        return external.isEmpty ? item.calendarItemIdentifier : external
    }
    func snapshot(_ id: String, _ item: EKCalendarItem?) -> [String: Any] {
        guard let item else {
            return ["id": id, "exists": false, "completed": false, "title": null, "list": null, "due": null, "modified_at": null]
        }
        var result: [String: Any] = ["id": id, "exists": true, "completed": false, "title": item.title ?? "",
                                    "list": item.calendar.title, "due": null, "modified_at": item.lastModifiedDate.map { fractional.string(from: $0) } ?? null as Any,
                                    "notes": item.notes ?? "", "source": item is EKReminder ? "reminder" : "event"]
        if let reminder = item as? EKReminder {
            result["completed"] = reminder.isCompleted
            if let dc = reminder.dueDateComponents, let date = dc.date ?? calendar.date(from: dc) {
                result["due"] = dc.hour == nil ? day(date) : iso.string(from: date)
            }
        }
        if let event = item as? EKEvent {
            result["start"] = iso.string(from: event.startDate); result["end"] = iso.string(from: event.endDate)
        }
        return names.snapshot(result)
    }
    func destination(_ title: Any?, _ kind: EKEntityType) throws -> EKCalendar {
        let title = names.actual(try string(title), kind)
        let matches = store.calendars(for: kind).filter { $0.title == title && $0.allowsContentModifications }
        guard matches.count == 1 else { throw BridgeError(message: "Missing or ambiguous destination") }
        return matches[0]
    }
    func reminder(_ item: EKCalendarItem?) throws -> EKReminder {
        guard let item = item as? EKReminder, !item.isCompleted, !item.hasRecurrenceRules else {
            throw BridgeError(message: "Unsupported or completed reminder")
        }
        return item
    }
    func event(_ item: EKCalendarItem?) throws -> EKEvent {
        guard let item = item as? EKEvent, !item.hasRecurrenceRules else { throw BridgeError(message: "Unsupported event") }
        return item
    }
    func addReminder(_ change: [String: Any], notes: String = "", url: URL? = nil) throws -> EKReminder {
        let item = EKReminder(eventStore: store)
        item.title = try string(change["title"]); item.calendar = try destination(change["list"], .reminder)
        item.notes = notes
        item.url = url
        item.dueDateComponents = try components(change["due"])
        if let alarm = change["alarm"], !(alarm is NSNull) { item.addAlarm(EKAlarm(absoluteDate: try parseDate(alarm))) }
        try store.save(item, commit: false)
        return item
    }
    func update(_ change: [String: Any], _ item: EKCalendarItem?) throws {
        let item = try reminder(item)
        if let title = change["title"] { item.title = try string(title) }
        if let list = change["list"] { item.calendar = try destination(list, .reminder) }
        if let due = change["due"] { item.dueDateComponents = try components(due) }
        if let person = change["waiting_for"] {
            var lines = (item.notes ?? "").components(separatedBy: "\n").filter { !$0.hasPrefix("[Ollo 等待] ") }
            if !(person is NSNull) { lines.append("[Ollo 等待] " + (try string(person))) }
            item.notes = lines.joined(separator: "\n")
        }
        if let follow = change["follow_up"] {
            // Only explicit follow_up controls the reminder alarm, never invent a deadline.
            item.alarms = follow is NSNull ? [] : [EKAlarm(absoluteDate: try parseDate(follow))]
        }
        try store.save(item, commit: false)
    }
    func remove(_ item: EKCalendarItem?) throws {
        if item is EKReminder { try store.remove(try reminder(item), commit: false) }
        else { try store.remove(try event(item), span: .thisEvent, commit: false) }
    }
    func toEvent(_ change: [String: Any], _ source: EKCalendarItem?) throws {
        let source = try reminder(source)
        let target = EKEvent(eventStore: store)
        target.title = source.title; target.notes = source.notes
        target.calendar = try destination(change["calendar"], .event)
        target.startDate = try parseDate(change["start"])
        target.endDate = target.startDate.addingTimeInterval(Double(change["minutes"] as? Int ?? 0) * 60)
        guard target.endDate > target.startDate else { throw BridgeError(message: "Invalid duration") }
        try store.save(target, span: .thisEvent, commit: false)
        try store.remove(source, commit: false)
    }
    func merge(_ change: [String: Any], _ source: EKCalendarItem?, _ destination: EKCalendarItem?) throws {
        let source = try reminder(source), target = try reminder(destination)
        guard source.calendarItemIdentifier != target.calendarItemIdentifier else { throw BridgeError(message: "Self merge") }
        if let title = change["title"] { target.title = try string(title) }
        target.notes = [target.notes ?? "", source.title ?? "", source.notes ?? ""].filter { !$0.isEmpty }.joined(separator: "\n")
        try store.save(target, commit: false); try store.remove(source, commit: false)
    }
    func updateEvent(_ change: [String: Any], _ item: EKCalendarItem?) throws {
        let item = try event(item)
        let seconds = (change["minutes"] as? Int).map { Double($0) * 60 } ?? item.endDate.timeIntervalSince(item.startDate)
        guard seconds > 0 else { throw BridgeError(message: "Invalid duration") }
        item.startDate = try parseDate(change["start"]); item.endDate = item.startDate.addingTimeInterval(seconds)
        try store.save(item, span: .thisEvent, commit: false)
    }
    func apply(_ request: [String: Any]) throws -> [String: Any] {
        guard let changes = request["changes"] as? [[String: Any]], !changes.isEmpty, changes.count <= 100,
              let expected = request["expected"] as? [String: [String: Any]] else {
            throw BridgeError(message: "Invalid apply request")
        }
        let ids = Set(changes.flatMap { [$0["item"], $0["into"]].compactMap { $0 as? String } })
        var resolved: [String: EKCalendarItem] = [:]
        for id in ids {
            guard let old = expected[id], let item = try lookup(id) else {
                return ["status": "skipped", "reason": "确认前已删除或缺少报告快照"]
            }
            let live = snapshot(id, item)
            guard old["completed"] as? Bool == false, live["completed"] as? Bool == false else {
                return ["status": "skipped", "reason": "确认前已完成或不存在"]
            }
            guard matchesOriginal(old, live) else {
                return ["status": "skipped", "reason": "确认前事项已变化"]
            }
            resolved[id] = item
        }
        try commitChanges(prepare: {
            for raw in changes {
                // v1: optional null means unchanged, never an implicit field clear.
                let change = effectiveChange(raw)
                try self.stage(change, resolved)
                if ["delete", "merge", "to_event"].contains(change["op"] as? String ?? ""),
                   let id = change["item"] as? String { resolved.removeValue(forKey: id) }
            }
        }, commit: { try self.store.commit() }, reset: { self.store.reset() })
        return ["status": "done"]
    }
    func stage(_ change: [String: Any], _ resolved: [String: EKCalendarItem]) throws {
        let op = try string(change["op"])
        let item = (change["item"] as? String).flatMap { resolved[$0] }
        let target = (change["into"] as? String).flatMap { resolved[$0] }
        let operations: [String: () throws -> Void] = [
            "update": { try self.update(change, item) }, "delete": { try self.remove(item) },
            "to_event": { try self.toEvent(change, item) }, "merge": { try self.merge(change, item, target) },
            "update_event": { try self.updateEvent(change, item) },
            "create_reminder": { _ = try self.addReminder(change) }
        ]
        guard let operation = operations[op] else { throw BridgeError(message: "Unknown operation") }
        try operation()
    }
    // One read pass supplies both presentation data and immutable generation originals.
    // This method is for an operator-configured cron pre-run collector, never HTTP mutation.
    func collect(_ request: [String: Any]) throws -> [String: Any] {
        guard let logicalLists = request["lists"] as? [String], !logicalLists.isEmpty,
              Set(logicalLists).count == logicalLists.count, Set(logicalLists).isSubset(of: Set(names.lists.keys)),
              let logicalCalendars = request["calendars"] as? [String],
              Set(logicalCalendars).count == logicalCalendars.count,
              Set(logicalCalendars).isSubset(of: Set(names.calendars.keys)) else {
            throw BridgeError(message: "Invalid collection scope")
        }
        let lists = logicalLists.map { names.actual($0, .reminder) }
        let eventTitles = logicalCalendars.map { names.actual($0, .event) }
        let start = try parseDate(request["start"]), end = try parseDate(request["end"])
        guard end > start, end.timeIntervalSince(start) <= 31 * 86400 else {
            throw BridgeError(message: "Invalid collection window")
        }
        let calendars = store.calendars(for: .reminder).filter { lists.contains($0.title) }
        guard Set(calendars.map { $0.title }) == Set(lists), calendars.count == Set(lists).count else {
            throw BridgeError(message: "Missing or ambiguous reminder lists")
        }
        var reminders: [EKReminder]?
        let done = DispatchSemaphore(value: 0)
        let token = store.fetchReminders(matching: store.predicateForReminders(in: calendars)) { values in
            reminders = values; done.signal()
        }
        guard done.wait(timeout: .now() + 5) == .success, let reminders = reminders else {
            store.cancelFetchRequest(token)
            throw BridgeError(message: "Reminder read unavailable")
        }
        var events: [EKEvent] = []
        if !eventTitles.isEmpty {
            let eventCalendars = store.calendars(for: .event).filter { eventTitles.contains($0.title) }
            guard Set(eventCalendars.map { $0.title }) == Set(eventTitles), eventCalendars.count == Set(eventTitles).count else {
                throw BridgeError(message: "Missing or ambiguous event calendars")
            }
            events = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: eventCalendars))
        }
        guard permitted(.reminder), eventTitles.isEmpty || permitted(.event) else {
            throw BridgeError(message: "Permission unavailable")
        }
        let items: [EKCalendarItem] = reminders + events
        let rows = items.map { item in
            var row = snapshot(stableID(item), item)
            row["created_at"] = item.creationDate.map { fractional.string(from: $0) } ?? null as Any
            return row
        }
        return try collectionDocument(rows, at: Date())
    }

    func handle(_ request: [String: Any]) throws -> [String: Any] {
        let method = try string(request["method"])
        if method == "items" {
            guard let ids = request["ids"] as? [String], ids.count <= 100 else { throw BridgeError(message: "Invalid ids") }
            // Refresh sends only public fields. Private notes must not consume
            // the items response budget; collect still preserves the full row.
            return ["items": try ids.map { publicItem(snapshot($0, try lookup($0))) }]
        }
        if method == "capture" {
            guard let payload = request["request"] as? [String: Any] else { throw BridgeError(message: "Invalid capture") }
            let fields = try captureFields(payload)
            var item: EKReminder!
            try commitChanges(prepare: {
                item = try self.addReminder(["title": fields.title, "list": "收件箱"], notes: fields.notes, url: fields.url)
            }, commit: { try self.store.commit() }, reset: { self.store.reset() })
            return ["id": stableID(item)]
        }
        if method == "capture_lookup" { return try captureLookup(request) }
        if method == "apply" { return try apply(request) }
        if method == "collect" { return try collect(request) }
        throw BridgeError(message: "Unknown method")
    }

    func captureLookup(_ request: [String: Any]) throws -> [String: Any] {
        let id = try string(request["request_id"])
        guard UUID(uuidString: id) != nil else { throw BridgeError(message: "Invalid capture identity") }
        guard permitted(.reminder) else { return ["status": "unavailable", "reason": "reminder_permission"] }
        let calendars = Set(store.calendars(for: .reminder).map { $0.calendarIdentifier })
        var rows: [EKReminder]?
        let done = DispatchSemaphore(value: 0)
        // No date/completion predicate and nil calendars: all lists, including completed reminders.
        let token = store.fetchReminders(matching: store.predicateForReminders(in: nil)) { values in
            rows = values; done.signal()
        }
        guard done.wait(timeout: .now() + 5) == .success, let rows else {
            store.cancelFetchRequest(token)
            return ["status": "unavailable", "reason": "reminder_read_timeout"]
        }
        let unchanged = calendars == Set(store.calendars(for: .reminder).map { $0.calendarIdentifier })
        return captureLookupResult("ollo://capture/" + id, rows: rows.map { ($0.url?.absoluteString, stableID($0)) },
                                   fullAccess: permitted(.reminder), complete: unchanged, checkedAt: Date())
    }
}

func testAppleMappingAndCollection() throws {
    let defaults = try AppleNames()
    assert(defaults.actual("收件箱", .reminder) == "收件箱")
    let mapped = try AppleNames([
        "lists": Dictionary(uniqueKeysWithValues: defaults.lists.keys.map { ($0, "Ollo 测试·" + $0) }),
        "calendars": Dictionary(uniqueKeysWithValues: defaults.calendars.keys.map { ($0, "Ollo 测试·" + $0) })])
    assert(mapped.actual("收件箱", .reminder) == "Ollo 测试·收件箱")
    assert(mapped.actual("长期", .reminder) == "Ollo 测试·长期")
    assert(mapped.actual("家庭", .event) == "Ollo 测试·家庭")
    assert(mapped.logical("未映射清单", .reminder) == "未映射清单")
    for raw in [["lists": ["工作": "生活"]], ["calendars": ["工作": "生活"]],
                ["lists": ["不存在": "测试"]], ["lists": ["工作": " "]]] {
        do { _ = try AppleNames(raw); assertionFailure("Invalid mapping accepted") }
        catch is BridgeError {}
    }
    let row: [String: Any] = ["id": "a", "source": "reminder", "exists": true, "completed": false,
        "title": "保留完整标题", "list": "Ollo 测试·工作", "due": null, "modified_at": "2026-10-11T08:00:00+08:00"]
    let logical = mapped.snapshot(row)
    assert(publicItem(logical)["list"] as? String == "工作")
    assert(generationOriginal(logical)["list"] as? String == "工作")
    assert(matchesOriginal(generationOriginal(logical), mapped.snapshot(row)))
    var moved = row; moved["list"] = "Ollo 测试·家庭"
    assert(!matchesOriginal(generationOriginal(logical), mapped.snapshot(moved)))
    var event = row; event["source"] = "event"; event["list"] = "Ollo 测试·生活"
    assert(mapped.snapshot(event)["list"] as? String == "生活")
    var completed = logical; completed["completed"] = true
    let rows = (0..<500).map { i -> [String: Any] in var r = logical; r["id"] = "item-\(i)"; return r }
    let collected = try collectionDocument(rows + [completed], at: Date())
    assert((collected["items"] as! [[String: Any]]).count == 500)
    assert((collected["originals"] as! [String: [String: Any]]).count == 500)
    let empty = try collectionDocument([completed], at: Date())
    assert((empty["items"] as! [[String: Any]]).isEmpty)
    do { _ = try collectionDocument(rows + [logical], at: Date()); assertionFailure("501 items accepted") }
    catch let error as BridgeError { assert(error.message == "collection_limit_exceeded") }
    do { _ = try collectionDocument([logical, logical], at: Date()); assertionFailure("Duplicate ID accepted") }
    catch is BridgeError {}
}

// This branch is safe for development: it creates no EKEventStore and calls no permission APIs.
if CommandLine.arguments.contains("--self-test") {
    do {
        try testAppleMappingAndCollection()
        let dateOnly = try components("2026-10-05")
        let timed = try components("2026-10-05T21:00:00+08:00")
        let cleared = try components(null)
        assert(dateOnly?.hour == nil && timed?.hour == 21 && cleared == nil)
        let old: [String: Any] = ["title": "原始标题", "due": null, "completed": false,
                                  "modified_at": "2026-10-05T08:00:00+08:00"]
        var live = old
        live["notes"] = "extra backend field"; live["modified_at"] = "2026-10-05T00:00:00.000Z"
        assert(matchesOriginal(old, live))
        live["due"] = "2026-10-09"
        assert(!matchesOriginal(old, live))
        live["due"] = null; live["modified_at"] = "2026-10-05T00:00:00.001Z"
        assert(!matchesOriginal(old, live))
        let normalized = effectiveChange(["op": "update", "item": "fixture", "due": null, "title": "新标题"])
        assert(normalized["due"] == nil && normalized["title"] as? String == "新标题")
        var staged = [String](), committed = [String](), commits = 0
        try commitChanges(prepare: {
            staged.append("merge")
            staged.append("follow_up")
        }, commit: { committed = staged; commits += 1 }, reset: { staged = [] })
        assert(committed == ["merge", "follow_up"] && commits == 1)
        do {
            try commitChanges(prepare: {
                staged = ["partial"]
                throw BridgeError(message: "Invalid second operation")
            }, commit: { commits += 1 }, reset: { staged = [] })
            assertionFailure("Preparation failure must throw")
        } catch let failure as MutationFailure {
            assert(failure.outcome == "not_executed" && staged.isEmpty && commits == 1)
        }
        do {
            try commitChanges(prepare: { staged = ["pending"] }, commit: {
                throw BridgeError(message: "Commit outcome unknown")
            }, reset: { staged = [] })
            assertionFailure("Commit failure must throw")
        } catch let failure as MutationFailure { assert(failure.outcome == "unknown") }
        let captureID = "50000000-0000-4000-8000-000000000001"
        let fields = try captureFields(["request_id": captureID, "text": "原文", "created_at": "2026-10-11T08:00:00+08:00", "source": "app"])
        assert(fields.title == "原文" && !fields.notes.contains(captureID))
        assert(fields.url.absoluteString == "ollo://capture/" + captureID)
        let longText = String(repeating: "中", count: 1365) + "a"
        assert(longText.utf8.count == 4096)
        let longFields = try captureFields(["request_id": captureID, "text": longText,
                                           "created_at": "2026-10-11T08:00:00+08:00", "source": "app"])
        assert(longFields.title == longText && longFields.url == fields.url)
        for title in [String(repeating: "a", count: 513), longText, String(repeating: "😀", count: 1024)] {
            let row: [String: Any] = ["id": "fixture", "source": "reminder", "title": title,
                                      "list": "收件箱", "completed": false, "notes": "private"]
            let original = generationOriginal(row)
            assert(original["title"] as? String == title && matchesOriginal(original, row))
            var changed = row; changed["title"] = title + "!"
            assert(!matchesOriginal(original, changed))
            let item = publicItem(row)
            assert(item["title"] as? String == title && item["notes"] == nil)
            let wire = try encodedJSON(item)
            assert(String(data: wire, encoding: .utf8)!.contains(title))
            let decoded = try JSONSerialization.jsonObject(with: wire) as! [String: Any]
            assert(decoded["title"] as? String == title)
        }
        let target = fields.url.absoluteString
        func lookupResult(_ rows: [(String?, String)], full: Bool = true, complete: Bool = true) -> [String: Any] {
            captureLookupResult(target, rows: rows, fullAccess: full, complete: complete, checkedAt: Date())
        }
        assert(lookupResult([(target, "found")])["item_id"] as? String == "found")
        assert(lookupResult([(target + "-suffix", "other")])["status"] as? String == "absent")
        assert(lookupResult([])["checked_at"] is String)
        assert(lookupResult([], full: false)["status"] as? String == "unavailable")
        assert(lookupResult([], complete: false)["status"] as? String == "unavailable")
        assert(lookupResult([(target, "a"), (target, "b")])["status"] as? String == "unavailable")
        emit(["ok": true])
    } catch { emit(["error": "Date self-test failed"]); exit(1) }
} else {
    do {
        if CommandLine.arguments.contains("--authorize") {
            try authorize(EKEventStore()); emit(["authorized": true])
        } else {
            let data = FileHandle.standardInput.readDataToEndOfFile()
            guard data.count <= 256000, let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw BridgeError(message: "Invalid request")
            }
            let method = request["method"] as? String
            let inboxOnly = method == "collect" && (request["calendars"] as? [String])?.isEmpty == true
            guard method == "capture_lookup" || (permitted(.reminder) && (method == "capture" || inboxOnly || permitted(.event))) else {
                throw BridgeError(message: "Permission unavailable")
            }
            let names = try AppleNames(request["apple"])
            emit(try Bridge(EKEventStore(), names: names).handle(request))
        }
    } catch let failure as MutationFailure {
        emit(["error": "EventKit operation unavailable", "outcome": failure.outcome]); exit(1)
    } catch let failure as BridgeError {
        emit(["error": failure.message == "collection_limit_exceeded" ? failure.message : "EventKit operation unavailable",
              "outcome": "not_executed"]); exit(1)
    } catch {
        emit(["error": "EventKit operation unavailable", "outcome": "not_executed"]); exit(1)
    }
}
