import XCTest
import FnCapture
@testable import AfterglowFn

final class RequestTests: XCTestCase {
    func testAudioOnlyOmitsAmbientFields() throws {
        let payload = AmbientRequest(audio: Data([1, 2]), appName: "Editor")
        let data = try JSONEncoder().encode(payload)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["audio_b64"] as? String, "AQI=")
        XCTAssertEqual(json["mime"] as? String, "audio/wav")
        XCTAssertEqual(json["app_name"] as? String, "Editor")
        XCTAssertNil(json["crop_png_b64"])
        XCTAssertNil(json["ocr"])
        XCTAssertNil(json["ax"])
        XCTAssertNil(json["screen_rect"])
    }
    func testCropJSON() throws {
        let payload = AmbientRequest(audio: Data(), crop: Data([3]),
            ocr: [OCRText(text: "hello", x: 0.1, y: 0.2, w: 0.3, h: 0.4)],
            ax: AXContext(role: "AXTextField", title: "Title", value: "Value", appName: "Editor"),
            appName: "Editor", screenRect: CGRect(x: -100, y: 20, width: 200, height: 100))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any])
        XCTAssertEqual(json["crop_png_b64"] as? String, "Aw==")
        XCTAssertEqual((json["screen_rect"] as? [String: Double]), ["x": -100, "y": 20, "w": 200, "h": 100])
        XCTAssertEqual((json["ax"] as? [String: String])?["app_name"], "Editor")
        XCTAssertEqual((json["ocr"] as? [[String: Any]])?.first?["text"] as? String, "hello")
    }
    func testAddressPrecedenceAndSafeExplanation() throws {
        let address = try ServerAddress(arguments: ["helper", "--url", "http://localhost:9000"], environment: ["AFTERGLOW_URL": "http://localhost:8000"])
        XCTAssertEqual(address.ambient.absoluteString, "http://localhost:9000/api/ambient")
        XCTAssertEqual(try address.explanation("/explain/id").absoluteString, "http://localhost:9000/explain/id")
        XCTAssertThrowsError(try address.explanation("https://elsewhere.test/"))
        XCTAssertThrowsError(try address.explanation("//elsewhere.test/"))
        XCTAssertThrowsError(try ServerAddress(arguments: ["helper", "--url"], environment: [:]))
        XCTAssertEqual(try ServerAddress(arguments: [], environment: [:]).base.absoluteString, "http://127.0.0.1:8650")
        XCTAssertEqual(try ServerAddress(arguments: [], environment: ["AFTERGLOW_URL": "http://localhost:8765"]).base.port, 8765)
    }
    func testReplyModes() throws {
        let paste = try JSONDecoder().decode(AmbientReply.self, from: Data(#"{"mode":"paste","text":"Clean text"}"#.utf8))
        XCTAssertEqual(paste.text, "Clean text")
        let explain = try JSONDecoder().decode(AmbientReply.self, from: Data(#"{"mode":"explain","url":"/explain/one"}"#.utf8))
        XCTAssertEqual(explain.url, "/explain/one")
    }
}
