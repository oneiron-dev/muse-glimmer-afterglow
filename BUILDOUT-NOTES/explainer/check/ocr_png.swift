// The fn helper's OCR (TextRecognizer.swift) for a check without the helper: accurate Vision OCR, ja-JP then en-US (en-US first reads Japanese as Latin junk),
// top-left fraction boxes, sorted top to bottom then left to right. Prints {"<file>": [{text, x, y, w, h}]}.
import Foundation
import Vision
import ImageIO

var out: [String: [[String: Any]]] = [:]
for path in CommandLine.arguments.dropFirst() {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else { continue }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ja-JP", "en-US"]
    request.usesLanguageCorrection = true
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    var lines: [[String: Any]] = []
    for o in request.results ?? [] {
        guard let s = o.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { continue }
        let b = o.boundingBox
        lines.append(["text": s, "x": Double(b.minX), "y": Double(1 - b.maxY), "w": Double(b.width), "h": Double(b.height)])
    }
    lines.sort { a, b in
        let ay = a["y"] as! Double, by = b["y"] as! Double
        if abs(ay - by) > 0.01 { return ay < by }
        return (a["x"] as! Double) < (b["x"] as! Double)
    }
    out[(path as NSString).lastPathComponent] = lines
}
let data = try JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
