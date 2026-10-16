// JSON stdin/stdout bridge. Moves may prompt for Reminders Automation permission.
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
struct MutationContext {
    let op: String
    let fields: [String]
    init(_ changes: [[String: Any]]) {
        let allowed: [String: Set<String>] = [
            "update": ["title", "list", "due", "waiting_for", "follow_up"], "delete": [], "complete": [],
            "merge": ["title"], "to_event": ["calendar", "start", "minutes"],
            "update_event": ["start", "minutes"], "create_reminder": ["title", "list", "due", "alarm"],
            "capture": ["title", "list", "notes", "url"]
        ]
        let ops = Set(changes.map { change in
            let op = change["op"] as? String ?? "unknown"
            return allowed[op] == nil ? "unknown" : op
        })
        op = ops.count == 1 ? ops.first! : (ops.isEmpty ? "unknown" : "batch")
        fields = Set(changes.flatMap { change -> [String] in
            let keys = allowed[change["op"] as? String ?? ""] ?? []
            return effectiveChange(change).keys.filter { keys.contains($0) }
        }).sorted()
    }
}
struct MutationFailure: Error {
    let outcome: String
    let diagnostic: [String: Any]
    let reasonCode: String?
    init(_ error: Error, phase: String, context: MutationContext,
         outcome: String? = nil, reasonCode: String? = nil) {
        let error = error as NSError
        self.outcome = outcome ?? (phase == "prepare" ? "not_executed" : "unknown")
        self.reasonCode = reasonCode
        var data: [String: Any] = ["phase": phase, "domain": error.domain, "code": error.code,
                                   "op": context.op, "fields": context.fields]
        // userInfo/description can embed reminder content. Only the immediate
        // underlying identity is useful here; never serialize or recurse into it.
        if let underlying = error.userInfo[NSUnderlyingErrorKey] as? NSError {
            data["underlyingError"] = ["domain": underlying.domain, "code": underlying.code]
        }
        diagnostic = data
    }
    var response: [String: Any] {
        var value: [String: Any] = ["error": "Apple operation unavailable", "outcome": outcome, "diagnostic": diagnostic]
        if let reasonCode { value["reason_code"] = reasonCode }
        return value
    }
}

// All EventKit saves in prepare use commit:false. Only commit errors are uncertain.
func commitChanges(context: () -> MutationContext = { MutationContext([]) },
                   prepare: () throws -> Void, commit: () throws -> Void, reset: () -> Void) throws {
    do { try prepare() }
    catch let failure as MutationFailure { reset(); throw failure }
    catch {
        let failure = MutationFailure(error, phase: "prepare", context: context())
        reset(); throw failure
    }
    do { try commit() }
    catch {
        let failure = MutationFailure(error, phase: "commit", context: context())
        reset(); throw failure
    }
}

// In-process AppleScript keeps cancellation under the helper's kernel watchdog:
// no osascript child can outlive HelperProcesses.kill/reap. Script parameters
// are descriptors, never interpolated source, shell arguments, or log fields.
protocol ReminderMoveRunner {
    func move(reminderID: String, listID: String) throws
}
func checkedAppleID(_ value: String) throws -> String {
    guard value.count == 36, UUID(uuidString: value) != nil else {
        throw BridgeError(message: "Unsupported Apple identifier")
    }
    return value
}
struct AppleScriptMoveRunner: ReminderMoveRunner {
    static let source = """
    on moveReminder(reminderIdentifier, listIdentifier)
        with timeout of 12 seconds
            tell application "Reminders"
                set selectedReminder to reminder id reminderIdentifier
                move selectedReminder to list id listIdentifier
            end tell
        end timeout
        return true
    end moveReminder
    """
    func move(reminderID: String, listID: String) throws {
        let reminderID = try checkedAppleID(reminderID), listID = try checkedAppleID(listID)
        guard let script = NSAppleScript(source: Self.source) else {
            throw NSError(domain: "OlloAppleScript", code: -1)
        }
        // 'ascr'/'psbr': call a handler in this script, which sends the move.
        let event = NSAppleEventDescriptor(eventClass: 0x61736372, eventID: 0x70736272,
            targetDescriptor: nil, returnID: -1, transactionID: 0)
        event.setParam(NSAppleEventDescriptor(string: "movereminder"), forKeyword: 0x736E616D)
        let parameters = NSAppleEventDescriptor.list()
        parameters.insert(NSAppleEventDescriptor(string: "x-apple-reminder://" + reminderID), at: 1)
        parameters.insert(NSAppleEventDescriptor(string: listID), at: 2)
        event.setParam(parameters, forKeyword: 0x2D2D2D2D)
        var error: NSDictionary?
        _ = script.executeAppleEvent(event, error: &error)
        if let error {
            // Discard the localized error, offending object and script text here.
            throw NSError(domain: "OlloAppleScript", code: (error[NSAppleScript.errorNumber] as? Int) ?? -1)
        }
    }
}

struct ReminderMove {
    let reference: String
    let identifier: String
    let sourceList: String
    let targetList: String
    let title: String
    let created: Date
    let original: [String: Any]
}
struct MoveCandidate {
    let identifier: String
    let list: String
    let title: String
    let created: Date?
    let completed: Bool
    func matches(_ move: ReminderMove) -> Bool {
        list == move.targetList && title == move.title && created == move.created && !completed
    }
}
struct MoveObservation {
    let oldExists: Bool
    let oldUnchanged: Bool
    let candidates: [MoveCandidate]
    func replacement(_ move: ReminderMove) -> String? {
        let matches = candidates.filter { $0.matches(move) }
        guard !oldExists, matches.count == 1, matches[0].identifier != move.identifier else { return nil }
        return matches[0].identifier
    }
    func unchanged(_ move: ReminderMove) -> Bool {
        oldExists && oldUnchanged && !candidates.contains { $0.matches(move) }
    }
}
func moveReason(_ error: Error) -> String {
    switch (error as NSError).code {
    case -1743: return "move_permission"
    case -1712: return "move_timeout"
    case -600: return "move_app_unavailable"
    case -609: return "move_connection_lost"
    default: return "move_failed"
    }
}

// List assignment is planned separately. Collapsing repeated destinations for
// one reminder preserves the final result; merge/to_event do not read its list.
func moveTargets(_ changes: [[String: Any]]) -> [(String, String)] {
    var order: [String] = [], targets: [String: String] = [:]
    for change in changes where change["op"] as? String == "update" {
        if let reference = change["item"] as? String, let list = change["list"] as? String {
            if targets[reference] == nil { order.append(reference) }
            targets[reference] = list
        }
    }
    return order.map { ($0, targets[$0]!) }
}
func withoutList(_ change: [String: Any]) -> [String: Any] {
    var change = effectiveChange(change)
    if change["op"] as? String == "update" { change.removeValue(forKey: "list") }
    return change
}
func resolveGroup<Item>(_ references: [String], replacements: [String: String],
                        lookup: (String) throws -> Item?) throws -> [String: Item] {
    var resolved: [String: Item] = [:]
    for reference in references {
        guard let item = try lookup(replacements[reference] ?? reference) else {
            throw BridgeError(message: "Item changed during move")
        }
        resolved[reference] = item
    }
    return resolved
}
func stageGroup<Item>(_ changes: [[String: Any]], resolved: [String: Item],
                      stage: ([String: Any], [String: Item]) throws -> Void) throws {
    var resolved = resolved
    for change in changes {
        do { try stage(withoutList(change), resolved) }
        catch { throw MutationFailure(error, phase: "prepare", context: MutationContext([change])) }
        if ["delete", "merge", "to_event"].contains(change["op"] as? String ?? ""),
           let id = change["item"] as? String { resolved.removeValue(forKey: id) }
    }
}
func validCompletionOriginals(_ changes: [[String: Any]], _ originals: [String: [String: Any]]) -> Bool {
    changes.filter { $0["op"] as? String == "complete" }.allSatisfy { change in
        guard let id = change["item"] as? String, let original = originals[id] else { return false }
        return original["source"] as? String == "reminder" && original["completed"] as? Bool == false
    }
}
protocol CompletionTarget: AnyObject {
    var isCompleted: Bool { get set }
    var hasRecurrenceRules: Bool { get }
}
extension EKReminder: CompletionTarget {}
func stageCompletion<Item: CompletionTarget>(_ item: Item, save: (Item) throws -> Void) throws {
    guard !item.isCompleted, !item.hasRecurrenceRules else {
        throw BridgeError(message: "Unsupported or completed reminder")
    }
    item.isCompleted = true
    try save(item)
}
func hasListUpdate(_ changes: [[String: Any]]) -> Bool { !moveTargets(changes).isEmpty }
func helperWatchdog(_ request: [String: Any]) -> UInt32 {
    request["method"] as? String == "apply" && hasListUpdate(request["changes"] as? [[String: Any]] ?? []) ? 55 : 7
}

// Poll only reads, never re-send move. An error reply can arrive after Reminders
// performed the move; success needs a unique new ID AND absence of the old ID.
func reconcileMove(_ move: ReminderMove, error: Error?, context: MutationContext,
                   observe: (ReminderMove, Double) throws -> MoveObservation,
                   now: () -> Double, pause: (Double) -> Void) throws -> String {
    let deadline = now() + 5
    var last: MoveObservation?
    repeat {
        do {
            let reading = try observe(move, max(0.01, deadline - now()))
            last = reading
            if let replacement = reading.replacement(move) { return replacement }
        } catch {
            last = nil // A failed latest read cannot establish absence.
        }
        let remaining = deadline - now()
        if remaining > 0 { pause(min(0.2, remaining)) }
    } while now() < deadline
    let unchanged = error != nil && last?.unchanged(move) == true
    // Apple explicitly does not cancel an application command on timeout.
    // Even a readable old object cannot prove that a queued move will not land.
    let pending = error.map { [-1712, -609].contains(($0 as NSError).code) } ?? false
    throw MutationFailure(error ?? NSError(domain: "OlloMoveReconciliation", code: 1),
        phase: "reconcile", context: context, outcome: unchanged && !pending ? "not_executed" : "unknown",
        reasonCode: unchanged ? moveReason(error!) : "move_unresolved")
}

// The current contract has no partial group state. Once a move succeeds, every
// subsequent failure is a retained unknown receipt, never retryable rollback.
func applyWithMoves(_ moves: [ReminderMove], runner: ReminderMoveRunner, context: MutationContext,
                    observe: (ReminderMove, Double) throws -> MoveObservation,
                    prepare: ([String: String]) throws -> Void,
                    commit: () throws -> Void, reset: () -> Void,
                    now: () -> Double = { ProcessInfo.processInfo.systemUptime },
                    pause: (Double) -> Void = { Thread.sleep(forTimeInterval: $0) }) throws -> [String: String] {
    if moves.isEmpty {
        try commitChanges(context: { context }, prepare: { try prepare([:]) }, commit: commit, reset: reset)
        return [:]
    }
    let deadline = now() + 48
    var replacements: [String: String] = [:]
    var phase = "prepare"
    do {
        // Validate the entire dependency group using commit:false, then discard
        // it. Nothing staged may survive a reset/re-read during external moves.
        try prepare([:]); reset()
        for move in moves {
            phase = "move"
            guard deadline - now() >= 27 else {
                throw MutationFailure(NSError(domain: "OlloMoveBudget", code: 1), phase: phase,
                    context: context, outcome: "not_executed", reasonCode: "move_budget")
            }
            let baseline = try observe(move, 5)
            guard baseline.unchanged(move) else {
                throw MutationFailure(NSError(domain: "OlloMoveReconciliation", code: 2), phase: "reconcile",
                    context: context, outcome: "not_executed", reasonCode: "move_precondition")
            }
            var scriptError: Error?
            do { try runner.move(reminderID: move.identifier, listID: move.targetList) }
            catch { scriptError = error }
            // Permission denial is a definite rejection before the move event.
            if let error = scriptError, (error as NSError).code == -1743 {
                throw MutationFailure(error, phase: phase, context: context,
                    outcome: "not_executed", reasonCode: "move_permission")
            }
            phase = "reconcile"
            replacements[move.reference] = try reconcileMove(move, error: scriptError, context: context,
                                                            observe: observe, now: now, pause: pause)
        }
        phase = "prepare"
        try prepare(replacements)
        phase = "commit"
        try commit()
        return replacements
    } catch let failure as MutationFailure {
        reset()
        if replacements.isEmpty { throw failure }
        throw MutationFailure(NSError(domain: failure.diagnostic["domain"] as? String ?? "OlloMove", code: failure.diagnostic["code"] as? Int ?? 1),
            phase: failure.diagnostic["phase"] as? String ?? phase, context: context,
            outcome: "unknown", reasonCode: "move_partial")
    } catch {
        reset()
        throw MutationFailure(error, phase: phase, context: context,
            outcome: replacements.isEmpty && phase != "commit" ? "not_executed" : "unknown",
            reasonCode: replacements.isEmpty ? "move_failed" : "move_partial")
    }
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
    // A6 accepts minute precision; Foundation's InternetDateTime parser requires
    // seconds. Expand only that input shape, leaving all output formatters intact.
    let timestamp = raw.range(of: #"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})\z"#,
                              options: .regularExpression) != nil
        ? String(raw.prefix(16)) + ":00" + String(raw.dropFirst(16)) : raw
    if let result = iso.date(from: timestamp) ?? fractional.date(from: timestamp) { return result }
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
func collectionDocument(_ rows: [[String: Any]], at: Date, includeCompleted: Bool = false,
                        planIDs: Set<String> = []) throws -> [String: Any] {
    let active = rows.filter { includeCompleted || $0["source"] as? String == "event" || $0["completed"] as? Bool == false }
    guard active.count <= 500 else { throw BridgeError(message: "collection_limit_exceeded") }
    var originals = [String: [String: Any]](), ids = Set<String>()
    for row in active {
        let id = try string(row["id"])
        guard ids.insert(id).inserted else { throw BridgeError(message: "Ambiguous item identifier") }
        if row["modified_at"] != nil, !(row["modified_at"] is NSNull) { originals[id] = generationOriginal(row) }
    }
    return ["items": active, "originals": originals, "collected_at": fractional.string(from: at),
            "missing_plan_item_ids": Array(planIDs.subtracting(ids)).sorted()]
}

func includeReminder(id: String, list: String, completed: Bool, completedAt: Date?,
                     lists: Set<String>, planIDs: Set<String>, completedToday: Bool,
                     start: Date, end: Date) -> Bool {
    if planIDs.contains(id) { return true }
    if !completed { return lists.contains(list) }
    guard completedToday, let completedAt else { return false }
    return completedAt >= start && completedAt < end
}

func eventReadIDs(_ available: [(id: String, title: String, writable: Bool)],
                  destinations: Set<String>, all: Bool, selected: Set<String>) throws -> Set<String> {
    // EventKit only exposes readable calendars here. Read-only/subscribed
    // calendars must not be filtered by allowsContentModifications.
    let targets = available.filter { destinations.contains($0.title) }
    guard targets.count == destinations.count, Set(targets.map { $0.title }) == destinations,
          targets.allSatisfy({ $0.writable }) else {
        throw BridgeError(message: "Missing or ambiguous event calendars")
    }
    return Set(available.filter { all || selected.contains($0.title) }.map { $0.id })
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

// This projection has no store or mutation methods. Raw values are reduced
// immediately; callers cannot accidentally serialize titles, URLs or identifiers.
struct ReminderStructure {
    let fields: [String: Any]
    init(hasNotes: Bool, hasAlarms: Bool, hasRecurrenceRules: Bool, hasAttendees: Bool,
         url: URL?, sourceType: Int?, allowsContentModifications: Bool?,
         externalIdentifier: String?, identifier: String, title: String) {
        fields = ["exists": true, "hasNotes": hasNotes, "hasAlarms": hasAlarms,
                  "hasRecurrenceRules": hasRecurrenceRules, "hasAttendees": hasAttendees,
                  "urlIsEmpty": url?.absoluteString.isEmpty ?? true,
                  "calendar": ["sourceType": sourceType.map { $0 as Any } ?? null,
                               "allowsContentModifications": allowsContentModifications.map { $0 as Any } ?? null],
                  "identifiersEqual": externalIdentifier == identifier,
                  "titleLength": title.unicodeScalars.count,
                  "titleHasNewline": title.rangeOfCharacter(from: .newlines) != nil]
    }
    init(_ item: EKReminder) {
        self.init(hasNotes: item.hasNotes, hasAlarms: item.hasAlarms,
                  hasRecurrenceRules: item.hasRecurrenceRules, hasAttendees: item.hasAttendees,
                  url: item.url, sourceType: item.calendar?.source?.sourceType.rawValue,
                  allowsContentModifications: item.calendar?.allowsContentModifications,
                  externalIdentifier: item.calendarItemExternalIdentifier,
                  identifier: item.calendarItemIdentifier, title: item.title ?? "")
    }
}
func inspectReminders(_ request: [String: Any], lookup: (String) throws -> ReminderStructure?) throws -> [String: Any] {
    guard Set(request.keys) == Set(["method", "ids"]), request["method"] as? String == "inspect",
          let ids = request["ids"] as? [String], !ids.isEmpty, ids.count <= 100,
          ids.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else {
        throw BridgeError(message: "Invalid inspection request")
    }
    // Positional correspondence avoids echoing any identifier in stdout.
    return ["items": try ids.map { try lookup($0)?.fields ?? ["exists": false] }]
}

final class Bridge {
    let store: EKEventStore
    let names: AppleNames
    let moveRunner: ReminderMoveRunner
    init(_ store: EKEventStore, names: AppleNames, moveRunner: ReminderMoveRunner = AppleScriptMoveRunner()) {
        self.store = store; self.names = names; self.moveRunner = moveRunner
    }
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
            result["completed_at"] = reminder.completionDate.map { fractional.string(from: $0) } ?? null as Any
            if let dc = reminder.dueDateComponents, let date = dc.date ?? calendar.date(from: dc) {
                result["due"] = dc.hour == nil ? day(date) : iso.string(from: date)
            }
        }
        if let event = item as? EKEvent {
            result["start"] = iso.string(from: event.startDate); result["end"] = iso.string(from: event.endDate)
            result["calendar"] = event.calendar.title
            result["location"] = event.location ?? ""
            result["all_day"] = event.isAllDay
            result["subscribed"] = event.calendar.type == .subscription
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
        guard let item = item as? EKEvent, !item.hasRecurrenceRules,
              names.calendars.values.contains(item.calendar.title), item.calendar.allowsContentModifications else {
            throw BridgeError(message: "Unsupported event")
        }
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
        if let list = change["list"], try destination(list, .reminder).calendarIdentifier != item.calendar.calendarIdentifier {
            throw BridgeError(message: "List change requires move")
        }
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
    func complete(_ item: EKCalendarItem?) throws {
        guard let item = item as? EKReminder else { throw BridgeError(message: "Completion requires reminder") }
        try stageCompletion(item) { try self.store.save($0, commit: false) }
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
    func planMoves(_ changes: [[String: Any]], _ resolved: [String: EKCalendarItem]) throws -> [ReminderMove] {
        try moveTargets(changes).compactMap { reference, list in
            let item = try reminder(resolved[reference]), target = try destination(list, .reminder)
            guard item.calendar.calendarIdentifier != target.calendarIdentifier else { return nil }
            // Only the identifier relationship verified with Reminders is supported.
            guard stableID(item) == item.calendarItemIdentifier, let created = item.creationDate else {
                throw BridgeError(message: "Unsupported move identity")
            }
            return ReminderMove(reference: reference, identifier: try checkedAppleID(item.calendarItemIdentifier),
                sourceList: item.calendar.calendarIdentifier, targetList: try checkedAppleID(target.calendarIdentifier),
                title: item.title ?? "", created: created, original: snapshot(reference, item))
        }
    }
    func observeMove(_ move: ReminderMove, timeout: Double) throws -> MoveObservation {
        store.reset()
        guard permitted(.reminder), let target = store.calendar(withIdentifier: move.targetList) else {
            throw BridgeError(message: "Move read unavailable")
        }
        var rows: [EKReminder]?
        let done = DispatchSemaphore(value: 0)
        let token = store.fetchReminders(matching: store.predicateForReminders(in: [target])) { values in
            rows = values; done.signal()
        }
        guard done.wait(timeout: .now() + timeout) == .success, let rows, permitted(.reminder) else {
            store.cancelFetchRequest(token)
            throw BridgeError(message: "Move read unavailable")
        }
        let old = store.calendarItem(withIdentifier: move.identifier)
        return MoveObservation(oldExists: old != nil,
            oldUnchanged: old != nil && old?.calendar.calendarIdentifier == move.sourceList
                && old?.creationDate == move.created
                && matchesOriginal(move.original, snapshot(move.reference, old)),
            candidates: rows.map { MoveCandidate(identifier: $0.calendarItemIdentifier, list: $0.calendar.calendarIdentifier,
                                                title: $0.title ?? "", created: $0.creationDate, completed: $0.isCompleted) })
    }
    func prepareMovedGroup(_ changes: [[String: Any]], originals: [String: [String: Any]],
                           moves: [ReminderMove], replacements: [String: String]) throws {
        let resolved = try resolveGroup(Array(originals.keys), replacements: replacements, lookup: lookup)
        for (reference, item) in resolved {
            let original = originals[reference]!
            var expected = original, live = snapshot(reference, item)
            if let replacement = replacements[reference] {
                guard let move = moves.first(where: { $0.reference == reference }),
                      item.calendarItemIdentifier == replacement,
                      item.calendar.calendarIdentifier == move.targetList, item.creationDate == move.created else {
                    throw BridgeError(message: "Moved identity changed")
                }
                // List/modified_at are necessarily changed by move. All other
                // snapshotted fields must still match before staging the group.
                for key in ["list", "modified_at"] { expected.removeValue(forKey: key); live.removeValue(forKey: key) }
            }
            guard matchesOriginal(expected, live) else { throw BridgeError(message: "Item changed during move") }
        }
        try stageGroup(changes, resolved: resolved, stage: stage)
    }
    func apply(_ request: [String: Any]) throws -> [String: Any] {
        guard let changes = request["changes"] as? [[String: Any]], !changes.isEmpty, changes.count <= 100,
              let expected = request["expected"] as? [String: [String: Any]] else {
            throw BridgeError(message: "Invalid apply request")
        }
        guard validCompletionOriginals(changes, expected) else {
            return ["status": "skipped", "reason": "完成操作仅支持原值未完成的提醒事项"]
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
        let groupContext = MutationContext(changes)
        let moves = try planMoves(changes, resolved)
        // Keep full live snapshots (including notes) across external writes.
        let originals = Dictionary(uniqueKeysWithValues: resolved.map { ($0.key, snapshot($0.key, $0.value)) })
        _ = try applyWithMoves(moves, runner: moveRunner, context: groupContext,
            observe: { try self.observeMove($0, timeout: $1) },
            prepare: { try self.prepareMovedGroup(changes, originals: originals, moves: moves, replacements: $0) },
            commit: { try self.store.commit() }, reset: { self.store.reset() })
        return ["status": "done"]
    }

    func stage(_ change: [String: Any], _ resolved: [String: EKCalendarItem]) throws {
        let op = try string(change["op"])
        let item = (change["item"] as? String).flatMap { resolved[$0] }
        let target = (change["into"] as? String).flatMap { resolved[$0] }
        let operations: [String: () throws -> Void] = [
            "update": { try self.update(change, item) }, "delete": { try self.remove(item) },
            "complete": { try self.complete(item) },
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
        let allEvents = request["calendars"] as? String == "all"
        let logicalCalendars = allEvents ? [] : request["calendars"] as? [String]
        guard let logicalLists = request["lists"] as? [String], !logicalLists.isEmpty,
              Set(logicalLists).count == logicalLists.count, Set(logicalLists).isSubset(of: Set(names.lists.keys)),
              let logicalCalendars,
              Set(logicalCalendars).count == logicalCalendars.count,
              Set(logicalCalendars).isSubset(of: Set(names.calendars.keys)),
              let completedToday = request["include_completed_today"] as? Bool,
              let planItems = request["plan_item_ids"] as? [String], planItems.count <= 24,
              planItems.allSatisfy({ !$0.isEmpty }), Set(planItems).count == planItems.count else {
            throw BridgeError(message: "Invalid collection scope")
        }
        let lists = logicalLists.map { names.actual($0, .reminder) }
        let eventTitles = logicalCalendars.map { names.actual($0, .event) }
        let start = try parseDate(request["start"]), end = try parseDate(request["end"])
        guard end > start, end.timeIntervalSince(start) <= 31 * 86400 else {
            throw BridgeError(message: "Invalid collection window")
        }
        let availableLists = store.calendars(for: .reminder)
        let calendars = availableLists.filter { lists.contains($0.title) }
        guard Set(calendars.map { $0.title }) == Set(lists), calendars.count == Set(lists).count else {
            throw BridgeError(message: "Missing or ambiguous reminder lists")
        }
        var reminders: [EKReminder]?
        let done = DispatchSemaphore(value: 0)
        let planIDs = Set(planItems)
        let token = store.fetchReminders(matching: store.predicateForReminders(in: completedToday || !planIDs.isEmpty ? nil : calendars)) { values in
            reminders = values; done.signal()
        }
        guard done.wait(timeout: .now() + 5) == .success, let reminders = reminders else {
            store.cancelFetchRequest(token)
            throw BridgeError(message: "Reminder read unavailable")
        }
        var events: [EKEvent] = []
        if allEvents || !eventTitles.isEmpty {
            guard permitted(.event) else { throw BridgeError(message: "Permission unavailable") }
            let available = store.calendars(for: .event)
            let ids = try eventReadIDs(available.map { ($0.calendarIdentifier, $0.title, $0.allowsContentModifications) },
                                      destinations: Set(names.calendars.values), all: allEvents, selected: Set(eventTitles))
            let eventCalendars = available.filter { ids.contains($0.calendarIdentifier) }
            events = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: eventCalendars))
        }
        guard permitted(.reminder), (!allEvents && eventTitles.isEmpty) || permitted(.event),
              Set(availableLists.map { $0.calendarIdentifier }) == Set(store.calendars(for: .reminder).map { $0.calendarIdentifier }) else {
            throw BridgeError(message: "Permission unavailable")
        }
        let selected = reminders.filter {
            includeReminder(id: stableID($0), list: $0.calendar.title, completed: $0.isCompleted,
                            completedAt: $0.completionDate, lists: Set(lists), planIDs: planIDs,
                            completedToday: completedToday, start: start, end: end)
        }
        let items: [EKCalendarItem] = selected + events
        let rows = items.map { item in
            var row = snapshot(stableID(item), item)
            row["created_at"] = item.creationDate.map { fractional.string(from: $0) } ?? null as Any
            return row
        }
        return try collectionDocument(rows, at: Date(), includeCompleted: true, planIDs: planIDs)
    }

    func handle(_ request: [String: Any]) throws -> [String: Any] {
        let method = try string(request["method"])
        if method == "inspect" {
            return try inspectReminders(request) { id in
                guard let item = try self.lookup(id) else { return nil }
                guard let reminder = item as? EKReminder else { throw BridgeError(message: "Expected reminder") }
                return ReminderStructure(reminder)
            }
        }
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
            let context = MutationContext([["op": "capture", "title": fields.title, "list": "收件箱",
                                             "notes": fields.notes, "url": fields.url]])
            try commitChanges(context: { context }, prepare: {
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

// This fixture has no EventKit objects, permission calls, Apple events or sleeps.
final class MoveTestRunner: ReminderMoveRunner {
    var calls: [(String, String)] = []
    var action: (String, String) throws -> Void = { _, _ in }
    func move(reminderID: String, listID: String) throws {
        calls.append((reminderID, listID)); try action(reminderID, listID)
    }
}
final class MoveFixture {
    let runner = MoveTestRunner()
    let move = ReminderMove(reference: "old", identifier: "old", sourceList: "source", targetList: "target",
                            title: "PRIVATE_TITLE", created: Date(timeIntervalSince1970: 100), original: [:])
    var time = 0.0, committed = false, moved = false, ambiguous = false, readFails = false
    var prepared: [[String: String]] = [], pendingDue: String?, due: String?, resets = 0
    var secondPrepareFails = false, commitFails = false
    func observe(_ move: ReminderMove, _ timeout: Double) throws -> MoveObservation {
        if readFails { throw BridgeError(message: "PRIVATE_READ_ERROR") }
        let candidate = MoveCandidate(identifier: "new", list: move.targetList, title: move.title,
                                      created: move.created, completed: false)
        return MoveObservation(oldExists: !moved, oldUnchanged: !moved,
                               candidates: moved ? (ambiguous ? [candidate, candidate] : [candidate]) : [])
    }
    func run(moves: [ReminderMove]? = nil) throws -> [String: String] {
        try applyWithMoves(moves ?? [move], runner: runner, context: MutationContext([["op": "update", "list": "工作", "due": "2030-01-01"]]),
            observe: observe, prepare: { aliases in
                self.prepared.append(aliases)
                if self.prepared.count > 1 {
                    precondition(self.moved && aliases["old"] == "new" && self.due == nil)
                    if self.secondPrepareFails { throw BridgeError(message: "PRIVATE_STAGE_ERROR") }
                }
                self.pendingDue = "2030-01-01"
            }, commit: {
                if self.commitFails { throw NSError(domain: "EKErrorDomain", code: 1) }
                self.due = self.pendingDue; self.committed = true
            }, reset: { self.resets += 1; self.pendingDue = nil },
            now: { self.time }, pause: { self.time += $0 })
    }
}
func testReminderMoves() throws {
    let successful = MoveFixture()
    successful.runner.action = { _, _ in successful.moved = true }
    let aliases = try successful.run()
    precondition(aliases == ["old": "new"] && successful.due == "2030-01-01")
    precondition(successful.prepared == [[:], ["old": "new"]] && successful.resets == 1)
    precondition(successful.runner.calls.count == 1 && successful.runner.calls[0].1 == "target")

    for code in [-1743, -1712, -600, -609] {
        let fixture = MoveFixture()
        fixture.runner.action = { _, _ in throw NSError(domain: "OlloAppleScript", code: code,
                                                       userInfo: [NSLocalizedDescriptionKey: "PRIVATE_ERROR"]) }
        do { _ = try fixture.run(); preconditionFailure("Move error must throw") }
        catch let failure as MutationFailure {
            precondition(failure.outcome == ([-1712, -609].contains(code) ? "unknown" : "not_executed"))
            precondition(!fixture.moved && fixture.due == nil && !fixture.committed)
            precondition(failure.reasonCode == moveReason(NSError(domain: "OlloAppleScript", code: code)))
            let wire = String(data: try encodedJSON(failure.response), encoding: .utf8)!
            precondition(!wire.contains("PRIVATE") && !wire.contains("old") && !wire.contains("target"))
        }
    }
    // A timeout/connection error after mutation is recovered, not retried.
    for code in [-1712, -609] {
        let fixture = MoveFixture()
        fixture.runner.action = { _, _ in
            fixture.moved = true; throw NSError(domain: "OlloAppleScript", code: code)
        }
        _ = try fixture.run()
        precondition(fixture.committed && fixture.runner.calls.count == 1 && fixture.due == "2030-01-01")
    }
    for mode in ["ambiguous", "read", "stage", "commit"] {
        let fixture = MoveFixture()
        fixture.runner.action = { _, _ in
            fixture.moved = true
            fixture.ambiguous = mode == "ambiguous"; fixture.readFails = mode == "read"
        }
        fixture.secondPrepareFails = mode == "stage"; fixture.commitFails = mode == "commit"
        do { _ = try fixture.run(); preconditionFailure("Uncertain/partial move must throw") }
        catch let failure as MutationFailure {
            precondition(failure.outcome == "unknown" && fixture.due == nil && !fixture.committed)
            precondition(failure.reasonCode == (["stage", "commit"].contains(mode) ? "move_partial" : "move_unresolved"))
        }
    }
    // Re-read races: old still exists, wrong title/date/list/completion, or no
    // candidate cannot identify a moved object, even after a success reply.
    let sample = successful.move
    let valid = MoveCandidate(identifier: "new", list: "target", title: sample.title, created: sample.created, completed: false)
    precondition(MoveObservation(oldExists: true, oldUnchanged: true, candidates: [valid]).replacement(sample) == nil)
    for row in [MoveCandidate(identifier: "new", list: "elsewhere", title: sample.title, created: sample.created, completed: false),
                MoveCandidate(identifier: "new", list: "target", title: "other", created: sample.created, completed: false),
                MoveCandidate(identifier: "new", list: "target", title: sample.title, created: nil, completed: false),
                MoveCandidate(identifier: "new", list: "target", title: sample.title, created: sample.created, completed: true)] {
        precondition(MoveObservation(oldExists: false, oldUnchanged: false, candidates: [row]).replacement(sample) == nil)
    }
    for changes: [[String: Any]] in [
        [["op": "update", "item": "old", "due": "2030-01-01"]],
        [["op": "delete", "item": "old"]],
        [["op": "to_event", "item": "old", "calendar": "工作"]]
    ] {
        precondition(moveTargets(changes).isEmpty)
        let fixture = MoveFixture()
        _ = try fixture.run(moves: [])
        precondition(fixture.runner.calls.isEmpty && fixture.committed && fixture.prepared.count == 1)
        precondition(helperWatchdog(["method": "apply", "changes": changes]) == 7)
    }
    let mergeChanges: [[String: Any]] = [["op": "merge", "item": "source", "into": "old"],
                                         ["op": "update", "item": "old", "list": "工作"]]
    precondition(moveTargets(mergeChanges).map { $0.0 } == ["old"])
    precondition(withoutList(mergeChanges[0])["into"] as? String == "old")
    precondition(withoutList(mergeChanges[1])["list"] == nil)
    precondition(helperWatchdog(["method": "apply", "changes": mergeChanges]) == 55)
    try testMultipleMoves()
    try testMovedMergeTarget()
    for identifier in ["", "abc", "00000000-0000-0000-0000-000000000000\"", "\n00000000-0000-0000-0000-000000000000"] {
        do { _ = try checkedAppleID(identifier); preconditionFailure("Unsafe identity accepted") }
        catch is BridgeError {}
    }
    let safeID = try checkedAppleID("00000000-0000-0000-0000-000000000000")
    precondition(safeID == "00000000-0000-0000-0000-000000000000")
}
func testMultipleMoves() throws {
    let moves = ["a", "b"].map { ReminderMove(reference: $0, identifier: $0, sourceList: "source", targetList: "target",
                                             title: $0, created: Date(timeIntervalSince1970: 100), original: [:]) }
    for mode in ["success", "second_denied", "budget", "preflight"] {
        let runner = MoveTestRunner()
        var moved = Set<String>(), committed = false, time = 0.0, preparations = 0
        runner.action = { id, _ in
            if id == "b" && mode == "second_denied" { throw NSError(domain: "OlloAppleScript", code: -1743) }
            moved.insert(id)
            if mode == "budget" { time += 25 }
        }
        do {
            let aliases = try applyWithMoves(moves, runner: runner, context: MutationContext([]), observe: { move, _ in
                let done = moved.contains(move.identifier)
                return MoveObservation(oldExists: !done, oldUnchanged: !done, candidates: done ? [
                    MoveCandidate(identifier: move.identifier + "-new", list: move.targetList, title: move.title,
                                  created: move.created, completed: false)] : [])
            }, prepare: { aliases in
                preparations += 1
                if mode == "preflight" { throw BridgeError(message: "Invalid later operation") }
                if preparations == 2 { precondition(aliases == ["a": "a-new", "b": "b-new"]) }
            }, commit: { committed = true }, reset: {}, now: { time }, pause: { time += $0 })
            precondition(mode == "success" && aliases.count == 2 && committed)
        } catch let failure as MutationFailure {
            precondition(!committed && failure.outcome == (mode == "preflight" ? "not_executed" : "unknown"))
            precondition(mode == "preflight" ? runner.calls.isEmpty : runner.calls.count == (mode == "budget" ? 1 : 2))
        }
    }
}

func testMovedMergeTarget() throws {
    let changes: [[String: Any]] = [
        ["op": "merge", "item": "source", "into": "target"],
        ["op": "update", "item": "target", "list": "工作", "due": "2030-01-01"]]
    let move = ReminderMove(reference: "target", identifier: "target", sourceList: "inbox", targetList: "work",
                            title: "target title", created: Date(timeIntervalSince1970: 100), original: [:])
    let runner = MoveTestRunner()
    var live = ["source": "source object", "target": "target object"], pending: [String] = [], committed: [String] = []
    runner.action = { id, _ in live["new"] = live.removeValue(forKey: id) }
    _ = try applyWithMoves([move], runner: runner, context: MutationContext(changes), observe: { move, _ in
        MoveObservation(oldExists: live["target"] != nil, oldUnchanged: live["target"] != nil,
            candidates: live["new"] == nil ? [] : [MoveCandidate(identifier: "new", list: "work", title: move.title,
                                                               created: move.created, completed: false)])
    }, prepare: { aliases in
        let objects = try resolveGroup(["source", "target"], replacements: aliases, lookup: { live[$0] })
        try stageGroup(changes, resolved: objects) { change, resolved in
            if change["op"] as? String == "merge" {
                precondition(resolved["source"] == "source object" && resolved["target"] == "target object")
                pending.append("merged into " + (aliases["target"] ?? "target"))
            } else {
                precondition(resolved["source"] == nil && resolved["target"] == "target object" && change["list"] == nil)
                pending.append("due on " + (aliases["target"] ?? "target"))
            }
        }
    }, commit: { committed = pending }, reset: { pending = [] })
    precondition(committed == ["merged into new", "due on new"] && runner.calls.count == 1)
}

final class CompletionFixture: CompletionTarget {
    var isCompleted = false, hasRecurrenceRules = false
    var title = "before"
}
func testCompletion() throws {
    let changes: [[String: Any]] = [["op": "update", "item": "reminder", "title": "after", "by": "user"],
                                   ["op": "complete", "item": "reminder", "by": "user"]]
    precondition(validCompletionOriginals(changes, ["reminder": ["source": "reminder", "completed": false]]))
    for invalid: [String: Any] in [[:], ["source": "event", "completed": false],
                                    ["source": "reminder", "completed": true], ["source": "reminder"]] {
        precondition(!validCompletionOriginals(changes, ["reminder": invalid]))
    }
    precondition(!validCompletionOriginals(changes, [:]))
    for failure in ["none", "prepare", "commit"] {
        let item = CompletionFixture(), runner = MoveTestRunner()
        var saves = 0, commits = 0, resets = 0, stored = false
        do {
            _ = try applyWithMoves([], runner: runner, context: MutationContext(changes),
                observe: { _, _ in preconditionFailure("Completion must not move") },
                prepare: { _ in
                    try stageGroup(changes, resolved: ["reminder": item]) { change, resolved in
                        let target = resolved["reminder"]!
                        if change["op"] as? String == "update" { target.title = "after"; saves += 1 }
                        else {
                            try stageCompletion(target) { value in
                                precondition(value.isCompleted && value.title == "after" && commits == 0)
                                saves += 1
                            }
                        }
                    }
                    if failure == "prepare" { throw BridgeError(message: "synthetic staging failure") }
                }, commit: {
                    commits += 1
                    if failure == "commit" { throw BridgeError(message: "synthetic commit failure") }
                    stored = item.isCompleted
                }, reset: { resets += 1; item.isCompleted = false; item.title = "before" })
            precondition(failure == "none" && stored && commits == 1 && resets == 0)
        } catch let error as MutationFailure {
            precondition(failure != "none" && !stored && !item.isCompleted && item.title == "before")
            precondition(error.outcome == (failure == "prepare" ? "not_executed" : "unknown"))
            precondition(commits == (failure == "prepare" ? 0 : 1) && resets == 1)
        }
        precondition(saves == 2 && runner.calls.isEmpty)
    }
    for completed in [true, false] {
        let item = CompletionFixture()
        item.isCompleted = completed; item.hasRecurrenceRules = !completed
        do {
            try stageCompletion(item) { _ in preconditionFailure("Unsupported completion saved") }
            preconditionFailure("Unsupported completion accepted")
        } catch is BridgeError {}
    }
    let context = MutationContext([["op": "complete", "item": "PRIVATE_ID", "by": "user"]])
    precondition(context.op == "complete" && context.fields.isEmpty)
}

func testFailureDiagnostics() throws {
    let privateValues = ["PRIVATE_TITLE", "PRIVATE_NOTES", "PRIVATE_LIST", "PRIVATE_ID"]
    let privateText = privateValues.joined(separator: " ")
    let underlying = NSError(domain: "NSCocoaErrorDomain", code: 42,
                             userInfo: [NSLocalizedDescriptionKey: privateText, "notes": privateText])
    let error = NSError(domain: "EKErrorDomain", code: 17,
                        userInfo: [NSLocalizedDescriptionKey: privateText, NSUnderlyingErrorKey: underlying,
                                   "title": privateText, "calendar": privateText, "item": privateText])
    let change: [String: Any] = ["op": "update", "item": privateValues[3], "title": privateValues[0],
                               "list": privateValues[2], "notes": privateValues[1], "due": null,
                               privateText: privateText]
    let context = MutationContext([change])
    precondition(context.op == "update" && context.fields == ["list", "title"])
    for phase in ["prepare", "commit"] {
        var commits = 0, resets = 0
        do {
            try commitChanges(context: { context }, prepare: {
                if phase == "prepare" { throw error }
            }, commit: {
                commits += 1; throw error
            }, reset: { resets += 1 })
            preconditionFailure("Failure must throw")
        } catch let failure as MutationFailure {
            precondition(failure.outcome == (phase == "prepare" ? "not_executed" : "unknown"))
            precondition(commits == (phase == "prepare" ? 0 : 1) && resets == 1)
            let expected: [String: Any] = ["phase": phase, "domain": "EKErrorDomain", "code": 17,
                "op": "update", "fields": ["list", "title"],
                "underlyingError": ["domain": "NSCocoaErrorDomain", "code": 42]]
            precondition(NSDictionary(dictionary: failure.diagnostic).isEqual(to: expected))
            let wire = String(data: try encodedJSON(failure.response), encoding: .utf8)!
            precondition(privateValues.allSatisfy { !wire.contains($0) })
        }
    }
    let batch = MutationContext([change, ["op": "delete", "item": privateText]])
    precondition(batch.op == "batch" && batch.fields == ["list", "title"])
    let unknown = MutationContext([["op": privateText, privateText: privateText]])
    precondition(unknown.op == "unknown" && unknown.fields.isEmpty)
    let capture = MutationContext([["op": "capture", "title": privateText, "list": privateText,
                                    "notes": privateText, "url": privateText]])
    precondition(capture.fields == ["list", "notes", "title", "url"])
}

func testReadOnlyInspection() throws {
    let title = "PRIVATE_TITLE\n😀e\u{301}"
    let fixture = ReminderStructure(hasNotes: true, hasAlarms: true, hasRecurrenceRules: true, hasAttendees: true,
        url: URL(string: "https://example.invalid/PRIVATE_URL"), sourceType: 2, allowsContentModifications: false,
        externalIdentifier: "PRIVATE_EXTERNAL_ID", identifier: "PRIVATE_LOCAL_ID", title: title)
    let empty = ReminderStructure(hasNotes: false, hasAlarms: false, hasRecurrenceRules: false, hasAttendees: false,
        url: nil, sourceType: nil, allowsContentModifications: nil,
        externalIdentifier: "PRIVATE_SAME_ID", identifier: "PRIVATE_SAME_ID", title: "plain")
    let before = try encodedJSON([fixture.fields, empty.fields])
    var lookups = [String]()
    let result = try inspectReminders(["method": "inspect", "ids": ["PRIVATE_ID", "missing", "empty"]]) { id in
        lookups.append(id)
        return id == "missing" ? nil : (id == "empty" ? empty : fixture)
    }
    precondition(lookups == ["PRIVATE_ID", "missing", "empty"])
    let rows = result["items"] as! [[String: Any]]
    let expected: [String: Any] = ["exists": true, "hasNotes": true, "hasAlarms": true,
        "hasRecurrenceRules": true, "hasAttendees": true, "urlIsEmpty": false,
        "calendar": ["sourceType": 2, "allowsContentModifications": false], "identifiersEqual": false,
        "titleLength": title.unicodeScalars.count, "titleHasNewline": true]
    precondition(NSDictionary(dictionary: rows[0]).isEqual(to: expected))
    precondition(NSDictionary(dictionary: rows[1]).isEqual(to: ["exists": false]))
    precondition(rows[2]["urlIsEmpty"] as? Bool == true && rows[2]["identifiersEqual"] as? Bool == true)
    precondition(rows[2]["titleHasNewline"] as? Bool == false)
    let after = try encodedJSON([fixture.fields, empty.fields])
    precondition(before == after)
    let wire = String(data: try encodedJSON(result), encoding: .utf8)!
    precondition(!wire.contains("PRIVATE_") && !wire.contains("https://"))
    // A mutation-shaped request fails before even the read callback is invoked.
    for request: [String: Any] in [
        ["method": "inspect", "ids": ["PRIVATE_ID"], "changes": [["op": "delete"]]],
        ["method": "apply", "ids": ["PRIVATE_ID"]], ["method": "inspect", "ids": []],
        ["method": "inspect", "ids": [" "]], ["method": "inspect", "ids": Array(repeating: "id", count: 101)]
    ] {
        do {
            _ = try inspectReminders(request) { _ in preconditionFailure("Unexpected read") }
            preconditionFailure("Invalid inspection accepted")
        } catch is BridgeError {}
    }
}

func testMinutePrecisionDates() throws {
    let expected = try parseDate("2026-10-12T10:00:00+08:00")
    for raw in ["2026-10-12T10:00+08:00", "2026-10-12T02:00Z", "2026-10-11T22:00-04:00"] {
        let parsed = try parseDate(raw)
        let parts = try components(raw)
        assert(parsed == expected)
        assert(parts?.day == 12 && parts?.hour == 10 && parts?.minute == 0 && parts?.second == 0)
        // Existing output keeps seconds (and milliseconds for collection stamps).
        assert(iso.string(from: parsed) == "2026-10-12T10:00:00+08:00")
        assert(fractional.string(from: parsed) == "2026-10-12T02:00:00.000Z")
        for key in ["due", "modified_at", "start", "end"] {
            assert(matchesOriginal([key: raw], [key: "2026-10-12T02:00:00.000Z"]))
            assert(!matchesOriginal([key: raw], [key: "2026-10-12T02:01:00Z"]))
        }
    }
    let dateOnly = try components("2026-10-12")
    assert(dateOnly?.day == 12 && dateOnly?.hour == nil)
    let fraction = try parseDate("2026-10-12T02:00:00.125Z")
    assert(abs(fraction.timeIntervalSince(expected) - 0.125) < 0.001)
    for raw in ["2026-10-12T10:00", "2026-10-12T10:00:00"] {
        do { _ = try parseDate(raw); assertionFailure("Missing timezone accepted") }
        catch is BridgeError {}
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

func testCollectionV2() throws {
    let start = try parseDate("2026-10-16T00:00:00+08:00"), end = start.addingTimeInterval(86400)
    let calendars = [("work", "工作", true), ("life", "生活", true), ("family", "家庭", true),
                     ("holiday", "节假日", false), ("shared", "团队", false), ("other", "其他", true)]
    let ids = try eventReadIDs(calendars, destinations: ["工作", "生活", "家庭"], all: true, selected: [])
    precondition(ids == Set(calendars.map { $0.0 }))
    do {
        _ = try eventReadIDs(calendars + [("duplicate", "工作", false)],
                            destinations: ["工作", "生活", "家庭"], all: true, selected: [])
        preconditionFailure("Ambiguous write calendar accepted")
    } catch is BridgeError {}
    func includes(_ id: String, _ list: String, _ done: Bool, _ date: Date?) -> Bool {
        includeReminder(id: id, list: list, completed: done, completedAt: date,
                        lists: ["收件箱"], planIDs: ["planned"], completedToday: true, start: start, end: end)
    }
    precondition(includes("inbox", "收件箱", false, nil))
    precondition(includes("planned", "其他清单", true, start.addingTimeInterval(-86400)))
    precondition(includes("done", "其他清单", true, start))
    precondition(!includes("tomorrow", "其他清单", true, end))
    precondition(!includes("old", "其他清单", true, start.addingTimeInterval(-1)))
    precondition(!includes("unrelated", "其他清单", false, nil))
    let completed: [String: Any] = ["id": "done", "source": "reminder", "exists": true, "completed": true,
        "title": "合成完成项", "list": "其他清单", "due": null, "modified_at": iso.string(from: start)]
    let event: [String: Any] = ["id": "event", "source": "event", "exists": true, "completed": false,
        "title": "合成节日", "list": "节假日", "calendar": "节假日", "location": "", "all_day": true,
        "subscribed": true, "start": iso.string(from: start), "end": iso.string(from: end),
        "due": null, "modified_at": iso.string(from: start)]
    let doc = try collectionDocument([completed, event], at: start, includeCompleted: true, planIDs: ["done", "missing"])
    let rows = doc["items"] as! [[String: Any]], originals = doc["originals"] as! [String: [String: Any]]
    precondition(rows.count == 2 && originals["done"]?["completed"] as? Bool == true)
    precondition(rows[1]["calendar"] as? String == "节假日" && rows[1]["all_day"] as? Bool == true)
    precondition(originals["event"]?["start"] as? String == iso.string(from: start))
    precondition(originals["event"]?["location"] == nil)
    precondition(doc["missing_plan_item_ids"] as? [String] == ["missing"])
}

// This branch is safe for development: it creates no EKEventStore and calls no permission APIs.
if CommandLine.arguments.contains("--self-test") {
    do {
        try testCollectionV2()
        try testReminderMoves()
        try testFailureDiagnostics()
        try testCompletion()
        try testReadOnlyInspection()
        try testMinutePrecisionDates()
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
    } catch { emit(["error": "Bridge self-test failed"]); exit(1) }
} else {
    do {
        if CommandLine.arguments.contains("--authorize") {
            try authorize(EKEventStore()); emit(["authorized": true])
        } else {
            let data = FileHandle.standardInput.readDataToEndOfFile()
            guard data.count <= 256000, let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw BridgeError(message: "Invalid request")
            }
            // Preserve capture's seven seconds from process start. Only list
            // updates may extend the alarm after stdin has been parsed.
            let watchdog = helperWatchdog(request)
            if watchdog > 7 { alarm(watchdog) }
            let method = request["method"] as? String
            let inboxOnly = method == "collect" && (request["calendars"] as? [String])?.isEmpty == true
            guard method == "capture_lookup" || (permitted(.reminder) && (method == "capture" || method == "inspect" || inboxOnly || permitted(.event))) else {
                throw BridgeError(message: "Permission unavailable")
            }
            let names = try AppleNames(request["apple"])
            emit(try Bridge(EKEventStore(), names: names).handle(request))
        }
    } catch let failure as MutationFailure {
        emit(failure.response); exit(1)
    } catch let failure as BridgeError {
        emit(["error": failure.message == "collection_limit_exceeded" ? failure.message : "EventKit operation unavailable",
              "outcome": "not_executed"]); exit(1)
    } catch {
        emit(["error": "EventKit operation unavailable", "outcome": "not_executed"]); exit(1)
    }
}
