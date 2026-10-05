import Foundation
import Testing
@testable import WeChatCompanion

struct StatusModelTests {
    @Test @MainActor
    func searchKeepsItsEntryParentAndReturnRoute() {
        let app = AppModel(messageHistory: makeTestMessageHistory(), shareInbox: nil)
        app.selectedDestination = .chats
        app.beginConsumerSearch("fixture")
        #expect(app.primaryNavigationDestination == .chats)
        #expect(app.consumeArchiveSearchRequest()?.filter == .all)
        app.beginConsumerSearch("second fixture")
        #expect(app.primaryNavigationDestination == .chats)
        app.selectedDestination = app.primaryNavigationDestination
        #expect(app.selectedDestination == .chats)
        app.selectedDestination = .overview
        app.beginConsumerSearch("home fixture")
        #expect(app.primaryNavigationDestination == .overview)
        app.selectedDestination = .settings
        app.beginArchiveSearch("archive fixture")
        #expect(app.primaryNavigationDestination == .settings)
        #expect(app.consumeArchiveSearchRequest()?.filter == .archive)
    }
    @Test
    func incompleteAttachmentsRemainActionable() {
        #expect(ArchiveAttachmentImportStatus.unavailable.needsAttention)
        #expect(ArchiveAttachmentImportStatus.inserted(attachmentCount: 3, materializedCount: 1).needsAttention)
        #expect(ArchiveAttachmentImportStatus.alreadyPersisted(attachmentCount: 3, materializedCount: 1).needsAttention)
        #expect(!ArchiveAttachmentImportStatus.inserted(attachmentCount: 3, materializedCount: 3).needsAttention)
        #expect(!ArchiveAttachmentImportStatus.none.needsAttention)
    }

    @Test
    func consumerFeedbackAndMemoryViewportSurvivePresentationRouting() throws {
        let file = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("WeChatCompanion/ContentView.swift")
        let source = try String(contentsOf: file, encoding: .utf8)
        let chats = try #require(source.components(separatedBy: "private struct ChatsView: View {").last?
            .components(separatedBy: "private struct AdvancedChatsView:").first)
        let feedback = try #require(chats.range(of: "ImportAttentionView(model: model)"))
        let branch = try #require(chats.range(of: "if model.selectedVisualConversationID"))
        #expect(feedback.lowerBound < branch.lowerBound)
        let advanced = try #require(source.components(separatedBy: "private struct AdvancedSettingsView: View {").last?
            .components(separatedBy: "/// Memory status").first)
        #expect(!advanced.contains("ScrollView"))
        #expect(!advanced.contains("consumeMemorySettingsRequest"))
        let settings = try #require(source.components(separatedBy: "private struct SettingsView: View {").last?
            .components(separatedBy: "#if DEBUG").first)
        #expect(settings.contains("ScrollViewReader { proxy in"))
        #expect(settings.contains("advancedExpanded = true"))
        #expect(settings.contains("preparationExpanded = true"))
        #expect(settings.contains("proxy.scrollTo(\"memory\", anchor: .top)"))
    }

    @Test
    func secondaryRoutesRemainReachableUnderConsumerShell() {
        #expect(Destination.primary == [.overview, .chats, .settings])
        #expect(Destination.search.primaryDestination == .overview)
        #expect(Destination.dailySummary.primaryDestination == .overview)
        #expect(Destination.reminders.primaryDestination == .overview)
        #expect(Destination.agents.primaryDestination == .settings)
        #expect(Destination.diagnostics.primaryDestination == .settings)
    }

    @Test
    func normalImportStatusIsQuietButFailuresRemainVisible() {
        #expect(!ArchiveImportStatus.idle.needsAttention)
        #expect(!ArchiveImportStatus.imported(recordCount: 3, transcriptShape: "attributed").needsAttention)
        #expect(!ArchiveImportStatus.alreadyImported.needsAttention)
        #expect(ArchiveImportStatus.importing.needsAttention)
        #expect(ArchiveImportStatus.invalidArchive.needsAttention)
        #expect(ArchiveImportStatus.localPersistenceConsentRequired.needsAttention)
        #expect(ArchiveImportStatus.localStoreUnavailable.needsAttention)
    }

    @Test
    func launchDestinationUsesConsumerLanguage() {
        #expect(Destination.overview.rawValue == "Home")
    }

    @Test
    func diagnosticStatusUsesSafeFailureStates() {
        #expect(DiagnosticResult().status == .screenRecordingPermissionRequired)

        var result = DiagnosticResult()
        result.screenRecordingGranted = true
        #expect(result.status == .wechatNotRunning)

        result.wechatRunning = true
        #expect(result.status == .windowNotFound)

        result.wechatWindowFound = true
        result.waitingForVisibleWeChat = true
        #expect(result.status == .waitingForVisibleWeChat)

        result.waitingForVisibleWeChat = false
        #expect(result.status == .captureFailed)

        result.captureSucceeded = true
        #expect(result.status == .ocrFailed)

        result.ocrSucceeded = true
        #expect(result.status == .succeeded)
        #expect(result.uiInteractionPerformed == false)
        #expect(result.privateContentPersisted == false)
    }

    @Test
    func systemStatusRetainsPassiveCaptureCapabilities() {
        let status = SystemStatus(
            wechatInstalled: true,
            wechatRunning: false,
            screenRecordingGranted: true
        )

        #expect(status.wechatInstalled)
        #expect(!status.wechatRunning)
        #expect(status.screenRecordingGranted)
    }
}
