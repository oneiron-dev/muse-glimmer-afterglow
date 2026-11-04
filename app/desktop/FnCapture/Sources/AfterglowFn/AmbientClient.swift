import Foundation
import FnCapture

struct ScreenRect: Codable, Equatable {
    let x: Double
    let y: Double
    let w: Double
    let h: Double
    init(_ rect: CGRect) {
        x = rect.minX; y = rect.minY; w = rect.width; h = rect.height
    }
}

struct AmbientRequest: Encodable {
    let audioB64: String
    let mime = "audio/wav"
    let cropPNGB64: String?
    let ocr: [OCRText]?
    let ax: AXContext?
    let appName: String
    let screenRect: ScreenRect?

    init(audio: Data, crop: Data? = nil, ocr: [OCRText]? = nil,
         ax: AXContext? = nil, appName: String, screenRect: CGRect? = nil) {
        audioB64 = audio.base64EncodedString()
        cropPNGB64 = crop?.base64EncodedString()
        self.ocr = ocr; self.ax = ax; self.appName = appName
        self.screenRect = screenRect.map(ScreenRect.init)
    }
    enum CodingKeys: String, CodingKey {
        case audioB64 = "audio_b64", mime, cropPNGB64 = "crop_png_b64", ocr, ax
        case appName = "app_name", screenRect = "screen_rect"
    }
}

struct AmbientReply: Decodable {
    let mode: String
    let text: String?
    let url: String?
}

enum HelperError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let message): return message }
    }
}

struct ServerAddress {
    let base: URL
    init(arguments: [String], environment: [String: String]) throws {
        let value: String
        if let index = arguments.firstIndex(of: "--url") {
            guard arguments.indices.contains(index + 1) else {
                throw HelperError.message("Give --url a server address.")
            }
            value = arguments[index + 1]
        } else { value = environment["AFTERGLOW_URL"] ?? "http://127.0.0.1:8650" }
        guard let url = URL(string: value), ["http", "https"].contains(url.scheme),
              url.host != nil, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil else {
            throw HelperError.message("Use an http or https server address without credentials.")
        }
        base = url
    }
    var ambient: URL { base.appendingPathComponent("api/ambient") }
    func explanation(_ path: String) throws -> URL {
        guard path.hasPrefix("/"), !path.hasPrefix("//"),
              let result = URL(string: base.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path),
              result.scheme == base.scheme, result.host == base.host, result.port == base.port else {
            throw HelperError.message("The explanation link was not on your server.")
        }
        return result
    }
}

struct AmbientClient {
    let address: ServerAddress
    func send(_ payload: AmbientRequest) async throws -> AmbientReply {
        var request = URLRequest(url: address.ambient)
        request.httpMethod = "POST"
        // An explanation can take the model a while; the cursor note says it is working and esc stops it.
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(payload)
        let session = URLSession(configuration: .ephemeral)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            // Do not expose server response bodies, which may contain captured content.
            throw HelperError.message("Your agent could not process this recording.")
        }
        return try JSONDecoder().decode(AmbientReply.self, from: data)
    }
}
