import AppKit

/// The bar under the explanation's title: what you said, with Copy and Paste instead.
/// If the agent explained when you only meant to dictate, Paste instead puts your words where you were typing.
@MainActor final class YouSaidBar: NSTitlebarAccessoryViewController {
    private let words: String
    private let paste: () -> Void
    private var copyButton: NSButton!

    init(words: String, paste: @escaping () -> Void) {
        self.words = words
        self.paste = paste
        super.init(nibName: nil, bundle: nil)
        layoutAttribute = .bottom
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func loadView() {
        let bar = NSView(frame: CGRect(x: 0, y: 0, width: 400, height: 40))
        let label = NSTextField(labelWithString: "")
        let said = NSMutableAttributedString(string: "You said  ", attributes: [
            .font: NSFont.systemFont(ofSize: 11.5, weight: .medium), .foregroundColor: NSColor.secondaryLabelColor])
        said.append(NSAttributedString(string: "\u{201C}\(words)\u{201D}", attributes: [
            .font: NSFont.systemFont(ofSize: 12.5), .foregroundColor: NSColor.labelColor]))
        label.attributedStringValue = said
        label.lineBreakMode = .byTruncatingTail
        label.toolTip = words
        label.translatesAutoresizingMaskIntoConstraints = false
        label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        copyButton = NSButton(title: "Copy", target: self, action: #selector(copyWords))
        let pasteButton = NSButton(title: "Paste instead", target: self, action: #selector(pasteInstead))
        for b in [copyButton!, pasteButton] {
            b.bezelStyle = .rounded
            b.controlSize = .small
            b.font = .systemFont(ofSize: 11.5)
            b.translatesAutoresizingMaskIntoConstraints = false
            b.setContentHuggingPriority(.required, for: .horizontal)
            bar.addSubview(b)
        }
        bar.addSubview(label)
        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 16),
            label.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
            copyButton.leadingAnchor.constraint(greaterThanOrEqualTo: label.trailingAnchor, constant: 10),
            copyButton.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
            pasteButton.leadingAnchor.constraint(equalTo: copyButton.trailingAnchor, constant: 6),
            pasteButton.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -12),
            pasteButton.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
            bar.heightAnchor.constraint(equalToConstant: 40),
        ])
        view = bar
    }

    @objc private func copyWords() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(words, forType: .string)
        copyButton.title = "Copied"
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.copyButton.title = "Copy" }
    }

    @objc private func pasteInstead() { paste() }
}
