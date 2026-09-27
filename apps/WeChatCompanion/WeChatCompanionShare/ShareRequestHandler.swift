import Foundation
import UniformTypeIdentifiers

final class ShareRequestHandler: NSObject, NSExtensionRequestHandling, @unchecked Sendable {
    private enum Failure {
        static let domain = "com.lianghongjing.WeChatCompanion.Share"
        static let unsupportedInput = 1
        static let inboxUnavailable = 2
        static let handoffFailed = 3
    }

    func beginRequest(with context: NSExtensionContext) {
        let contextBox = ExtensionContextBox(context)
        let providers = context.inputItems
            .compactMap { $0 as? NSExtensionItem }
            .flatMap { $0.attachments ?? [] }

        guard providers.count == 1,
              let provider = providers.first,
              let typeIdentifier = archiveTypeIdentifier(for: provider)
        else {
            cancel(context, code: Failure.unsupportedInput)
            return
        }

        provider.loadFileRepresentation(forTypeIdentifier: typeIdentifier) {
            [weak self] url, error in
            guard let self else { return }
            guard error == nil, let url else {
                self.cancel(contextBox.value, code: Failure.handoffFailed)
                return
            }
            guard let inbox = WeChatShareInbox.appGroup() else {
                self.cancel(contextBox.value, code: Failure.inboxUnavailable)
                return
            }

            do {
                // Provider filenames are deliberately not treated as WeChat
                // conversation identity. The main app derives identity only
                // from evidence inside the validated archive.
                try inbox.enqueueCopy(
                    from: url,
                    suggestedConversationName: nil
                )
            } catch {
                self.cancel(contextBox.value, code: Failure.handoffFailed)
                return
            }

            contextBox.value.open(WeChatShareInbox.appURL) { _ in
                contextBox.value.completeRequest(returningItems: nil)
            }
        }
    }

    private func archiveTypeIdentifier(for provider: NSItemProvider) -> String? {
        if provider.hasItemConformingToTypeIdentifier(UTType.zip.identifier) {
            return UTType.zip.identifier
        }
        return provider.registeredTypeIdentifiers.first { identifier in
            guard let type = UTType(identifier) else { return false }
            return type.conforms(to: .zip)
        }
    }

    private func cancel(_ context: NSExtensionContext, code: Int) {
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
