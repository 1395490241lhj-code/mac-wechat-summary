import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

@MainActor
struct GeminiModelSelectionTests {
    // MARK: - Resolution and defaults

    @Test
    func missingPreferenceResolvesToProvisionalDefault() {
        // 3.7 Flash is what production already ships. Adding selection must not
        // move an existing user, so a fresh install resolves to it unchanged.
        #expect(GeminiModel.resolved(fromStoredID: nil) == .gemini37Flash)
        #expect(GeminiModel.provisionalDefault == .gemini37Flash)
        #expect(makeModel().selectedGeminiModel == .gemini37Flash)
    }

    @Test(arguments: [
        ("gemini-3.5-flash", GeminiModel.gemini35Flash),
        ("gemini-3.6-flash", GeminiModel.gemini36Flash),
        ("gemini-3.7-flash", GeminiModel.gemini37Flash),
    ])
    func storedPreferenceRestoresExactModel(id: String, expected: GeminiModel) {
        #expect(GeminiModel.resolved(fromStoredID: id) == expected)

        let defaults = scratchDefaults()
        defaults.set(id, forKey: AppModel.geminiModelKey)
        #expect(makeModel(defaults: defaults).selectedGeminiModel == expected)
    }

    @Test(arguments: ["gemini-flash-latest", "gemini-2.5-flash", "", "nonsense"])
    func invalidOrStalePreferenceFallsBackSafely(id: String) {
        #expect(GeminiModel.resolved(fromStoredID: id) == .gemini37Flash)

        let defaults = scratchDefaults()
        defaults.set(id, forKey: AppModel.geminiModelKey)
        #expect(makeModel(defaults: defaults).selectedGeminiModel == .gemini37Flash)
    }

    @Test
    func settingModelPersistsExactStableID() async {
        let defaults = scratchDefaults()
        let model = makeModel(defaults: defaults)

        await model.setGeminiModel(.gemini36Flash)

        #expect(model.selectedGeminiModel == .gemini36Flash)
        #expect(defaults.string(forKey: AppModel.geminiModelKey) == "gemini-3.6-flash")
    }

    // MARK: - Changing model preserves everything else

    @Test
    func changingModelPreservesConsentCredentialAndMetrics() async {
        let defaults = scratchDefaults()
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let credentials = CountingCredentialStore()
        try? credentials.save("test-key", account: GeminiFrameExtractor.credentialAccount)

        let transport = CountingTransport()
        let coordinator = ExtractionCoordinator(
            extractor: GeminiFrameExtractor(credentials: credentials, transport: transport),
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
        // The same transport is injected into AppModel, so it stays attached
        // when a setting change rebuilds the extractor. Without that the
        // request assertions below could not fail.
        let model = makeModel(coordinator: coordinator, credentials: credentials,
                              transport: transport, defaults: defaults)
        #expect(model.allowsRemoteProcessing)

        // Accumulate some metrics first.
        await coordinator.submit(.selectionTestFrame())
        await coordinator.waitUntilIdle()
        await model.refreshExtractionState()
        let before = model.extractionMetrics
        #expect(before.framesReceived == 1)
        let requestsBefore = await transport.requestCount

        // Away from the default on purpose: switching *to* the current value
        // returns early, and the test would then assert nothing.
        await model.setGeminiModel(.gemini35Flash)
        await model.refreshExtractionState()

        #expect(model.selectedGeminiModel == .gemini35Flash)
        #expect(model.allowsRemoteProcessing, "consent must be untouched")
        #expect(defaults.bool(forKey: AppModel.remoteConsentKey))
        #expect(credentials.removeCount == 0, "credential must be untouched")
        #expect(try! credentials.secret(
            account: GeminiFrameExtractor.credentialAccount) == "test-key")
        #expect(model.extractionMetrics.framesReceived == before.framesReceived)
        #expect(model.extractionMetrics.extractionsStarted == before.extractionsStarted)
        // The selector itself must never talk to the network.
        #expect(await transport.requestCount == requestsBefore)
    }

    @Test
    func changingModelMakesNoNetworkCallAndUploadsNoFrame() async {
        let transport = CountingTransport()
        let credentials = CountingCredentialStore()
        try? credentials.save("test-key", account: GeminiFrameExtractor.credentialAccount)
        let coordinator = ExtractionCoordinator(
            extractor: GeminiFrameExtractor(credentials: credentials, transport: transport),
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
        let model = makeModel(coordinator: coordinator, credentials: credentials,
                              transport: transport)

        for target in GeminiModel.allCases {
            await model.setGeminiModel(target)
        }

        #expect(await transport.requestCount == 0)
        #expect(await coordinator.snapshot().framesReceived == 0)
        #expect(await coordinator.snapshot().extractionsStarted == 0)
    }

    // MARK: - Endpoint

    @Test(arguments: GeminiModel.allCases)
    func extractorBuildsEndpointWithSelectedExactModel(model: GeminiModel) async throws {
        let credentials = CountingCredentialStore()
        try credentials.save("test-key", account: GeminiFrameExtractor.credentialAccount)
        let transport = CountingTransport()
        let extractor = GeminiFrameExtractor(
            credentials: credentials, transport: transport, model: model
        )

        _ = try? await extractor.extract(from: .selectionTestFrame())

        let url = try #require(await transport.lastURL)
        #expect(url.contains("/models/\(model.modelID):generateContent"))
        #expect(!url.contains("latest"))
    }

    // MARK: - End to end through AppModel

    /// The one seam the provider-level tests cannot reach: does a model chosen
    /// in Settings actually reach the request the coordinator sends?
    ///
    /// Everything here is injected -- transport, credential store, defaults --
    /// so no real Google endpoint is contacted. `CountingTransport` is the only
    /// `GeminiTransporting` in play and it answers locally; if the wiring ever
    /// fell back to `URLSessionGeminiTransport`, `lastURL` would stay nil and
    /// this test would fail rather than quietly reach the network.
    @Test
    func selectedModelReachesTheRequestTheCoordinatorSends() async throws {
        let defaults = scratchDefaults()
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let credentials = CountingCredentialStore()
        try credentials.save("test-key", account: GeminiFrameExtractor.credentialAccount)
        let transport = CountingTransport()
        let coordinator = ExtractionCoordinator()
        let model = makeModel(coordinator: coordinator, credentials: credentials,
                              transport: transport, defaults: defaults)

        // Fresh install still resolves to what production ships.
        #expect(model.selectedGeminiModel == .gemini37Flash)

        // Choose a different model, then let a real extraction run.
        await model.setGeminiModel(.gemini36Flash)
        await coordinator.submit(.selectionTestFrame())
        await coordinator.waitUntilIdle()

        let url = try #require(
            await transport.lastURL,
            "no request was observed -- the injected transport was detached"
        )
        #expect(url.contains("/models/gemini-3.6-flash:generateContent"))
        #expect(!url.contains("gemini-3.7-flash"))
        #expect(!url.contains("latest"))
        #expect(await transport.requestCount == 1)
        // Only ever the stub: the real endpoint is never reachable from here.
        #expect(url.hasPrefix("https://generativelanguage.googleapis.com/"))
    }

    // MARK: - Closed set

    @Test
    func modelSetIsClosedAndHasNoLatestAlias() {
        #expect(GeminiModel.allCases.map(\.modelID) == [
            "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash",
        ])
        #expect(GeminiModel.allCases.map(\.label) == [
            "Gemini 3.5 Flash", "Gemini 3.6 Flash", "Gemini 3.7 Flash",
        ])
        #expect(GeminiModel(rawValue: "gemini-flash-latest") == nil)
    }

    @Test
    func productionSourceContainsNoLatestAlias() throws {
        let directory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("WeChatCompanion")
        let files = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: nil)?
            .compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" } ?? []
        // Scan code only: doc comments legitimately name the alias we forbid.
        let source = try files.map { try String(contentsOf: $0, encoding: .utf8) }
            .joined(separator: "\n")
            .split(separator: "\n", omittingEmptySubsequences: false)
            .filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("//") }
            .joined(separator: "\n")
        #expect(!source.contains("gemini-flash-latest"))
        #expect(!source.contains("gemini-2.5-flash"))
    }

    // MARK: - Picker availability

    @Test
    func pickerIsDisabledOnlyWhileProcessing() async {
        let model = makeModel()
        #expect(!model.isExtractionProcessing)

        model.extractionMetrics.status = .processing
        #expect(model.isExtractionProcessing)

        model.extractionMetrics.status = .ready
        #expect(!model.isExtractionProcessing)
    }

    // MARK: - Privacy unchanged

    @Test
    func modelPreferenceStoresOnlyTheIDAndNoSecret() async {
        let defaults = scratchDefaults()
        let credentials = CountingCredentialStore()
        try? credentials.save("test-key", account: GeminiFrameExtractor.credentialAccount)
        let model = makeModel(credentials: credentials, defaults: defaults)

        await model.setGeminiModel(.gemini36Flash)

        let stored = defaults.dictionaryRepresentation()
        let encoded = stored.map { "\($0.key)=\($0.value)" }.joined(separator: "\n")
        #expect(!encoded.contains("test-key"))
        #expect(defaults.string(forKey: AppModel.geminiModelKey) == "gemini-3.6-flash")
        // Extracted content remains non-persistable by construction.
        #expect(!(ExtractedConversationFrame.self is any Encodable.Type))
    }

    // MARK: - Helpers

    private func scratchDefaults() -> UserDefaults {
        UserDefaults(suiteName: "GeminiModelSelectionTests-\(UUID())")!
    }

    private func makeModel(
        coordinator: ExtractionCoordinator = ExtractionCoordinator(),
        credentials: any CredentialStoring = CountingCredentialStore(),
        transport: any GeminiTransporting = CountingTransport(),
        defaults: UserDefaults? = nil
    ) -> AppModel {
        AppModel(
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(), credentials: credentials,
            geminiTransport: transport,
            consentDefaults: defaults ?? scratchDefaults()
        )
    }
}

// MARK: - Test doubles

private actor CountingTransport: GeminiTransporting {
    private(set) var requestCount = 0
    private(set) var lastURL: String?

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requestCount += 1
        lastURL = request.url?.absoluteString
        let response = HTTPURLResponse(
            url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil
        )!
        return (Data(#"{"candidates": []}"#.utf8), response)
    }
}

private final class CountingCredentialStore: CredentialStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: String] = [:]
    private(set) var removeCount = 0

    func save(_ secret: String, account: String) throws {
        lock.withLock { storage[account] = secret }
    }
    func secret(account: String) throws -> String? { lock.withLock { storage[account] } }
    func remove(account: String) throws {
        lock.withLock { removeCount += 1; storage.removeValue(forKey: account) }
    }
}

private extension ObservedFrame {
    /// Synthetic frame. No real WeChat imagery in any test.
    static func selectionTestFrame() -> ObservedFrame {
        let side = 32
        let ctx = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8,
            bytesPerRow: side, space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        )!
        ctx.setFillColor(gray: 0.5, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: side, height: side))
        let image = ctx.makeImage()!
        return ObservedFrame(
            image: image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
            captureMode: .systemSelectedWindow,
            fingerprint: FrameFingerprint(image: image)!
        )
    }
}
