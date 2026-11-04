import CoreGraphics
import Foundation
import ImageIO
import Vision

/// Errors thrown when recognising text in an image.
public enum TextRecognizerError: Error, Sendable {
    /// The input data is not a readable image.
    case invalidImage
    /// Vision failed to recognise text.
    case recognitionFailed(String)
}

/// Recognises English and Japanese text with accurate Vision OCR.
///
/// Boxes are normalised to the image with a top-left origin, converted with
/// the shared `Geometry.topLeftBox(fromVision:)` helper. Results are sorted
/// top to bottom, then left to right. Empty strings are skipped.
public enum TextRecognizer {
    /// Recognition languages used for every request. Japanese comes first: with
    /// English first, Vision reads Japanese as Latin junk and drops lines; English
    /// text reads the same either way.
    public static let recognitionLanguages = ["ja-JP", "en-US"]

    /// Recognises text in a `CGImage`.
    ///
    /// - Parameter image: The image to read.
    /// - Returns: Recognised lines as `OCRText` with normalised top-left boxes.
    public static func recognize(in image: CGImage) async throws -> [OCRText] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = recognitionLanguages
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
        } catch {
            throw TextRecognizerError.recognitionFailed(error.localizedDescription)
        }
        guard let observations = request.results else {
            return []
        }
        var outputs: [OCRText] = []
        outputs.reserveCapacity(observations.count)
        for observation in observations {
            guard let string = observation.topCandidates(1).first?.string else { continue }
            let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            let box = Geometry.topLeftBox(fromVision: observation.boundingBox)
            let clamped = CGRect(
                x: min(1, max(0, box.minX)),
                y: min(1, max(0, box.minY)),
                width: min(1, max(0, box.width)),
                height: min(1, max(0, box.height))
            )
            outputs.append(OCRText(
                text: trimmed,
                x: Double(clamped.minX),
                y: Double(clamped.minY),
                w: Double(clamped.width),
                h: Double(clamped.height)
            ))
        }
        outputs.sort {
            if abs($0.y - $1.y) > 0.01 { return $0.y < $1.y }
            return $0.x < $1.x
        }
        return outputs
    }

    /// Recognises text in PNG data.
    ///
    /// - Parameter data: PNG-encoded image data.
    /// - Returns: Recognised lines as `OCRText` with normalised top-left boxes.
    public static func recognize(png data: Data) async throws -> [OCRText] {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw TextRecognizerError.invalidImage
        }
        return try await recognize(in: image)
    }
}
