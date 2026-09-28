import AppKit
import Foundation
import UniformTypeIdentifiers

/// ShareKit requires a real view-controller principal class even though our
/// handoff has no visible UI.
@objc(WeChatCompanionShareViewController)
final class ShareViewController: NSViewController {
    private var hasStarted = false

    override func loadView() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 1, height: 1))
        view.alphaValue = 0
        self.view = view
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        guard !hasStarted, let context = extensionContext else { return }
        hasStarted = true
        ShareRequestProcessor.begin(with: context)
    }
}

private enum ShareRequestProcessor {
    private enum Failure {
        static let domain = "com.lianghongjing.WeChatCompanion.Share"
        static let unsupportedInput = 1
        static let inboxUnavailable = 2
        static let handoffFailed = 3
    }

    static func begin(with context: NSExtensionContext) {
        let contextBox = ExtensionContextBox(context)
        let providers = context.inputItems
            .compactMap { $0 as? NSExtensionItem }
            .flatMap { $0.attachments ?? [] }

        let supported = providers.compactMap { provider -> (NSItemProvider, String)? in
            guard let identifier = archiveTypeIdentifier(for: provider) else { return nil }
            return (provider, identifier)
        }

        // WeChat merged-forward currently produces one archive. If a future
        // host sends multiple archive payloads, refuse rather than guessing
        // which conversation should be imported.
        guard supported.count == 1, let item = supported.first else {
            cancel(contextBox.value, code: Failure.unsupportedInput)
            return
        }

        item.0.loadFileRepresentation(forTypeIdentifier: item.1) { url, error in
            guard error == nil, let url else {
                cancel(contextBox.value, code: Failure.handoffFailed)
                return
            }
            guard let inbox = WeChatShareInbox.appGroup() else {
                cancel(contextBox.value, code: Failure.inboxUnavailable)
                return
            }

            do {
                // Provider filenames are deliberately not treated as WeChat
                // conversation identity. The main app derives identity only
                // from evidence inside the validated archive.
                try inbox.enqueueCopy(from: url)
            } catch {
                cancel(contextBox.value, code: Failure.handoffFailed)
                return
            }

            contextBox.value.open(WeChatShareInbox.appURL) { _ in
                contextBox.value.completeRequest(returningItems: nil)
            }
        }
    }

    private static func archiveTypeIdentifier(for provider: NSItemProvider) -> String? {
        if provider.hasItemConformingToTypeIdentifier(UTType.zip.identifier) {
            return UTType.zip.identifier
        }
        return provider.registeredTypeIdentifiers.first { identifier in
            guard let type = UTType(identifier) else { return false }
            return type.conforms(to: .zip)
        }
    }

    private static func cancel(_ context: NSExtensionContext, code: Int) {
        context.cancelRequest(withError: NSError(
            domain: Failure.domain,
            code: code,
            userInfo: nil
        ))
    }
}

private final class ExtensionContextBox: @unchecked Sendable {
    let value: NSExtensionContext

    init(_ value: NSExtensionContext) {
        self.value = value
    }
}
