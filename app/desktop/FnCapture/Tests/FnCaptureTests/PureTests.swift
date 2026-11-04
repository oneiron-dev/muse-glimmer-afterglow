import XCTest
@testable import FnCapture

final class PureTests: XCTestCase {
    func testWAVHeaderAndSamples() {
        let bytes = [UInt8](WAV.encode(samples: [0, -32768, 32767, -1]))
        XCTAssertEqual(bytes.count, 52)
        XCTAssertEqual(String(bytes: bytes[0..<4], encoding: .ascii), "RIFF")
        XCTAssertEqual(Array(bytes[4..<8]), [44, 0, 0, 0])
        XCTAssertEqual(String(bytes: bytes[8..<16], encoding: .ascii), "WAVEfmt ")
        XCTAssertEqual(Array(bytes[20..<24]), [1, 0, 1, 0])
        XCTAssertEqual(Array(bytes[24..<28]), [0x80, 0x3e, 0, 0])
        XCTAssertEqual(Array(bytes[28..<36]), [0, 0x7d, 0, 0, 2, 0, 16, 0])
        XCTAssertEqual(String(bytes: bytes[36..<40], encoding: .ascii), "data")
        XCTAssertEqual(Array(bytes[40..<44]), [8, 0, 0, 0])
        XCTAssertEqual(Array(bytes[44...]), [0, 0, 0, 128, 255, 127, 255, 255])
        XCTAssertEqual(WAV.encode(samples: []).count, 44)
    }
    func testGlobalCoordinatesAcrossDisplays() {
        let primary = Geometry.quartzRect(fromAppKit: CGRect(x: 10, y: 100, width: 40, height: 30), primaryScreenHeight: 900)
        XCTAssertEqual(primary, CGRect(x: 10, y: 770, width: 40, height: 30))
        let left = Geometry.quartzRect(fromAppKit: CGRect(x: -1500, y: 200, width: 200, height: 100), primaryScreenHeight: 900)
        XCTAssertEqual(left, CGRect(x: -1500, y: 600, width: 200, height: 100))
        let above = Geometry.quartzRect(fromAppKit: CGRect(x: 100, y: 1000, width: 50, height: 50), primaryScreenHeight: 900)
        XCTAssertEqual(above.minY, -150)
    }
    func testRetinaPixelBoundsAndVisionOrigin() {
        XCTAssertEqual(Geometry.pixelRect(fromPoints: CGRect(x: 2.25, y: 3.25, width: 10, height: 20), scale: 2),
                       CGRect(x: 4, y: 6, width: 21, height: 41))
        XCTAssertEqual(Geometry.pixelRect(fromPoints: CGRect(x: 2, y: 3, width: 10, height: 20), scale: 1),
                       CGRect(x: 2, y: 3, width: 10, height: 20))
        let box = Geometry.topLeftBox(fromVision: CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4))
        XCTAssertEqual(box.minY, 0.4, accuracy: 0.00001)
        XCTAssertEqual(box.minX, 0.1)
    }
    func testPanelClampsToNegativeOriginDisplay() {
        XCTAssertEqual(Geometry.clamped(CGRect(x: -10, y: -100, width: 360, height: 420),
                                        to: CGRect(x: -1440, y: 0, width: 1440, height: 900)),
                       CGRect(x: -360, y: 0, width: 360, height: 420))
    }
    @MainActor func testPasteboardRoundTripAndConcurrentCopy() async {
        let original = [PasteboardEntry(representations: ["public.utf8-plain-text": Data("old".utf8), "public.png": Data([1, 2, 3])]),
                        PasteboardEntry(representations: ["public.html": Data("<b>old</b>".utf8)])]
        let board = FakePasteboard(original)
        var restore: (@MainActor () -> Void)?
        var pasted = 0
        let paster = Paster(board: board, sendPaste: { pasted += 1 }, schedule: { delay, action in
            XCTAssertEqual(delay, 0.4); restore = action
        })
        paster.paste("new")
        XCTAssertEqual(pasted, 1)
        XCTAssertEqual(board.entries.first?.representations["public.utf8-plain-text"], Data("new".utf8))
        restore?()
        XCTAssertEqual(board.entries, original)
        paster.paste("next")
        let copied = [PasteboardEntry(representations: ["public.utf8-plain-text": Data("copied elsewhere".utf8)])]
        board.replace(with: copied)
        restore?()
        XCTAssertEqual(board.entries, copied)
    }
    @MainActor func testOverlappingPastesRestoreOriginal() async {
        let original = [PasteboardEntry(representations: ["custom": Data([1])])]
        let board = FakePasteboard(original)
        var callbacks: [@MainActor () -> Void] = []
        let paster = Paster(board: board, sendPaste: {}, schedule: { _, action in callbacks.append(action) })
        paster.paste("one"); paster.paste("two")
        callbacks[0]()
        XCTAssertEqual(board.entries.first?.representations["public.utf8-plain-text"], Data("two".utf8))
        callbacks[1]()
        XCTAssertEqual(board.entries, original)
    }
}

@MainActor private final class FakePasteboard: PasteboardAccess {
    var changeCount = 0
    var entries: [PasteboardEntry]
    init(_ entries: [PasteboardEntry]) { self.entries = entries }
    func snapshot() -> [PasteboardEntry] { entries }
    func replace(with entries: [PasteboardEntry]) { self.entries = entries; changeCount += 1 }
}
