import AppKit

/// A pasteboard item preserving every data representation.
public struct PasteboardEntry: Equatable {
    public var representations: [String: Data]
    public init(representations: [String: Data]) { self.representations = representations }
}

/// Injectable pasteboard access for tests without touching the user's clipboard.
@MainActor public protocol PasteboardAccess: AnyObject {
    var changeCount: Int { get }
    func snapshot() -> [PasteboardEntry]
    func replace(with entries: [PasteboardEntry])
}

/// The system general pasteboard adapter.
@MainActor public final class GeneralPasteboard: PasteboardAccess {
    private let board = NSPasteboard.general
    public init() {}
    public var changeCount: Int { board.changeCount }
    public func snapshot() -> [PasteboardEntry] {
        (board.pasteboardItems ?? []).map { item in
            var values: [String: Data] = [:]
            for type in item.types { if let data = item.data(forType: type) { values[type.rawValue] = data } }
            return PasteboardEntry(representations: values)
        }
    }
    public func replace(with entries: [PasteboardEntry]) {
        board.clearContents()
        let items = entries.map { entry -> NSPasteboardItem in
            let item = NSPasteboardItem()
            for (type, data) in entry.representations { item.setData(data, forType: .init(type)) }
            return item
        }
        if !items.isEmpty { board.writeObjects(items) }
    }
}

/// Pastes and restores after 400 ms, unless another app has changed the clipboard.
@MainActor public final class Paster {
    private let board: PasteboardAccess
    private let sendPaste: () -> Void
    private let schedule: (TimeInterval, @escaping @MainActor () -> Void) -> Void
    private var pendingRestore: (() -> Void)?
    private var generation = 0
    public init(board: PasteboardAccess,
                sendPaste: @escaping () -> Void,
                schedule: @escaping (TimeInterval, @escaping @MainActor () -> Void) -> Void) {
        self.board = board; self.sendPaste = sendPaste; self.schedule = schedule
    }
    public convenience init() {
        self.init(board: GeneralPasteboard(), sendPaste: Paster.postCommandV,
                  schedule: { delay, action in
                      DispatchQueue.main.asyncAfter(deadline: .now() + delay) { action() }
                  })
    }
    public func paste(_ text: String) {
        pendingRestore?()
        generation += 1
        let current = generation
        let previous = board.snapshot()
        board.replace(with: [PasteboardEntry(representations: [NSPasteboard.PasteboardType.string.rawValue: Data(text.utf8)])])
        let written = board.changeCount
        let restore = { [board] in
            if board.changeCount == written { board.replace(with: previous) }
        }
        pendingRestore = restore
        sendPaste()
        schedule(0.4) { [weak self] in
            guard let self, self.generation == current else { return }
            restore()
            self.pendingRestore = nil
        }
    }
    /// Sends Command-V to the current foreground app without activating this process.
    public static func postCommandV() {
        let source = CGEventSource(stateID: .combinedSessionState)
        let down = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: true)
        let up = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: false)
        down?.flags = .maskCommand; up?.flags = .maskCommand
        down?.post(tap: .cghidEventTap); up?.post(tap: .cghidEventTap)
    }
}
