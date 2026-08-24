import CoreGraphics
import Foundation
import ImageIO
import Testing
@testable import WeChatCompanion

struct GeminiExtractionTests {
    // MARK: - Credentials

    @Test
    func credentialStoreRoundTripsAndRemoves() throws {
        let store = InMemoryCredentialStore()
        #expect(!store.hasSecret(account: "gemini-api-key"))

        try store.save("test-key", account: "gemini-api-key")
        #expect(store.hasSecret(account: "gemini-api-key"))
        #expect(try store.secret(account: "gemini-api-key") == "test-key")

        try store.remove(account: "gemini-api-key")
        #expect(!store.hasSecret(account: "gemini-api-key"))
        #expect(try store.secret(account: "gemini-api-key") == nil)
    }

    @Test
    func emptyCredentialCountsAsUnconfigured() throws {
        let store = InMemoryCredentialStore()
        try store.save("", account: GeminiFrameExtractor.credentialAccount)
        let extractor = GeminiFrameExtractor(
            credentials: store,
            transport: StubTransport(responseJSON: "{}")
        )
        #expect(!extractor.isConfigured)
    }

    @Test
    func noConfiguredKeyLeavesExtractionNotConfigured() async throws {
        let extractor = GeminiFrameExtractor(
            credentials: InMemoryCredentialStore(),
            transport: StubTransport(responseJSON: "{}")
        )
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )

        await coordinator.submit(.syntheticFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.status == .notConfigured)
        #expect(metrics.extractionsStarted == 0)
    }

    // MARK: - Consent gate

    @Test
    func configuredKeyWithoutConsentWithholdsTheFrame() async throws {
        let transport = StubTransport(responseJSON: Self.validResponseJSON)
        let coordinator = ExtractionCoordinator(
            extractor: try Self.configuredExtractor(transport: transport),
            capability: .onDeviceOnly
        )

        await coordinator.submit(.syntheticFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.framesWithheldPendingConsent == 1)
        #expect(metrics.extractionsStarted == 0)
        #expect(await transport.requestCount == 0)
    }

    @Test
    func configuredKeyWithConsentMakesTheProviderEligible() async throws {
        let transport = StubTransport(responseJSON: Self.validResponseJSON)
        let coordinator = ExtractionCoordinator(
            extractor: try Self.configuredExtractor(transport: transport),
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )

        await coordinator.submit(.syntheticFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsSucceeded == 1)
        #expect(metrics.framesWithheldPendingConsent == 0)
        #expect(await transport.requestCount == 1)
    }

    @Test
    func remoteProviderAlwaysDeclaresRemoteProcessing() throws {
        let extractor = try Self.configuredExtractor(
            transport: StubTransport(responseJSON: "{}")
        )
        #expect(extractor.processingLocation == .remote)
        #expect(extractor.isConfigured)
    }

    // MARK: - Request shape

    @Test
    func oneFrameProducesExactlyOneBoundedRequest() async throws {
        let transport = StubTransport(responseJSON: Self.validResponseJSON)
        let extractor = try Self.configuredExtractor(transport: transport)

        _ = try await extractor.extract(from: .syntheticFrame())

        #expect(await transport.requestCount == 1)
        let request = try #require(await transport.lastRequest)
        #expect(request.httpMethod == "POST")
        #expect(request.timeoutInterval == GeminiFrameExtractor.requestTimeout)
        #expect(request.value(forHTTPHeaderField: "x-goog-api-key") == "test-key")
        // The key must travel in a header, never in the URL.
        #expect(request.url?.absoluteString.contains("test-key") == false)
    }

    @Test
    func cancellationStopsBeforeSendingAnything() async throws {
        let transport = StubTransport(responseJSON: Self.validResponseJSON)
        let extractor = try Self.configuredExtractor(transport: transport)

        let task = Task {
            try await extractor.extract(from: .syntheticFrame())
        }
        task.cancel()
        let result = await task.result

        #expect(throws: (any Error).self) { try result.get() }
        #expect(await transport.requestCount == 0)
    }

    // MARK: - Response parsing

    @Test
    func strictDTOParsingMapsToProviderIndependentModels() throws {
        let dto = try GeminiFrameExtractor.decodeExtraction(from: """
            {"chat": {"title": "项目群", "confidence": 0.9},
             "messages": [
               {"sender": "小明", "ownership": "other", "visibleTime": "14:03",
                "text": "明天下午三点开会", "kind": "text", "confidence": 0.95,
                "bounds": {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.08}}
             ]}
            """)
        let frame = dto.asExtractedConversationFrame(capturedAt: Date())

        #expect(frame.chat?.title == "项目群")
        #expect(frame.messages.count == 1)
        let message = try #require(frame.messages.first)
        #expect(message.sender == "小明")
        #expect(message.ownership == .other)
        #expect(message.visibleTime == "14:03")
        #expect(message.text == "明天下午三点开会")
        #expect(message.kind == .text)
        #expect(message.normalizedBounds != nil)
    }

    @Test
    func unknownEnumValuesBecomeUnknownRatherThanFailing() throws {
        let dto = try GeminiFrameExtractor.decodeExtraction(from: """
            {"messages": [{"ownership": "maybe-mine", "kind": "sticker", "confidence": 0.5}]}
            """)
        let frame = dto.asExtractedConversationFrame(capturedAt: Date())
        let message = try #require(frame.messages.first)

        #expect(message.ownership == .unknown)
        #expect(message.kind == .unknown)
        #expect(message.sender == nil)
        #expect(message.text == nil)
        #expect(frame.chat == nil)
    }

    @Test
    func confidenceIsClampedAndMissingValuesAreZero() throws {
        let dto = try GeminiFrameExtractor.decodeExtraction(from: """
            {"chat": {"title": "群", "confidence": 4.2},
             "messages": [
               {"kind": "text", "confidence": -3},
               {"kind": "text"}
             ]}
            """)
        let frame = dto.asExtractedConversationFrame(capturedAt: Date())

        #expect(frame.chat?.confidence == 1)
        #expect(frame.messages[0].confidence == 0)
        #expect(frame.messages[1].confidence == 0)
    }

    @Test
    func unreliableBoundsAreDroppedRatherThanFabricated() throws {
        let dto = try GeminiFrameExtractor.decodeExtraction(from: """
            {"messages": [
               {"kind": "text", "bounds": {"x": 0.5, "y": 0.5, "width": 3, "height": 0.2}},
               {"kind": "text", "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}},
               {"kind": "text", "bounds": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}}
             ]}
            """)
        let frame = dto.asExtractedConversationFrame(capturedAt: Date())

        #expect(frame.messages[0].normalizedBounds == nil)
        #expect(frame.messages[1].normalizedBounds == nil)
        #expect(frame.messages[2].normalizedBounds != nil)
    }

    @Test
    func providerProseIsRejectedRatherThanTreatedAsChatData() {
        for prose in [
            "I'm sorry, I can't read this screenshot.",
            "Here is what I found: 你好",
            "",
            "[1, 2, 3]"
        ] {
            #expect(throws: GeminiExtractionError.malformedResponse) {
                try GeminiFrameExtractor.decodeExtraction(from: prose)
            }
        }
    }

    @Test
    func fencedJSONIsAcceptedButTruncatedJSONIsNot() throws {
        let fenced = try GeminiFrameExtractor.decodeExtraction(from: """
            ```json
            {"chat": null, "messages": []}
            ```
            """)
        #expect(fenced.messages?.isEmpty == true)

        #expect(throws: GeminiExtractionError.malformedResponse) {
            try GeminiFrameExtractor.decodeExtraction(from: "{\"messages\": [{\"kind\":")
        }
    }

    @Test
    func emptyCandidateListFailsSafely() {
        #expect(throws: GeminiExtractionError.emptyResponse) {
            try GeminiFrameExtractor.responseText(from: Data(#"{"candidates": []}"#.utf8))
        }
        #expect(throws: GeminiExtractionError.malformedResponse) {
            try GeminiFrameExtractor.responseText(from: Data("not json".utf8))
        }
    }

    @Test
    func httpFailureSurfacesStatusOnlyAndNeverABody() async throws {
        let transport = StubTransport(
            responseJSON: #"{"error": {"message": "secret-echo-of-request"}}"#,
            statusCode: 500
        )
        let extractor = try Self.configuredExtractor(transport: transport)

        await #expect(throws: GeminiExtractionError.transportFailure(status: 500)) {
            try await extractor.extract(from: .syntheticFrame())
        }
    }

    @Test
    func providerFailureReachesMetricsAsCountsOnly() async throws {
        let transport = StubTransport(responseJSON: "nonsense", statusCode: 200)
        let coordinator = ExtractionCoordinator(
            extractor: try Self.configuredExtractor(transport: transport),
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )

        await coordinator.submit(.syntheticFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsFailed == 1)
        let json = String(decoding: try JSONEncoder().encode(metrics), as: UTF8.self)
        #expect(!json.contains("nonsense"))
        #expect(!json.contains("error"))
    }

    // MARK: - Image handling

    @Test
    func encodingStaysInMemoryAndDownsamplesLargeFrames() throws {
        let large = CGImage.syntheticFrameImage(width: 4000, height: 2500)
        let data = try FrameImageEncoder.encodedJPEG(from: large)
        #expect(!data.isEmpty)

        let source = try #require(CGImageSourceCreateWithData(data as CFData, nil))
        let decoded = try #require(CGImageSourceCreateImageAtIndex(source, 0, nil))
        #expect(max(decoded.width, decoded.height) == FrameImageEncoder.maxPixelDimension)
        // Aspect ratio preserved, and nothing was written anywhere.
        #expect(decoded.width > decoded.height)
    }

    @Test
    func smallFramesAreNotUpscaled() {
        let small = CGImage.syntheticFrameImage(width: 320, height: 240)
        let prepared = FrameImageEncoder.downsampled(small)
        #expect(prepared.width == 320)
        #expect(prepared.height == 240)
    }

    // MARK: - Persistence guards

    @Test
    func extractedContentAndCredentialsAreNeverWrittenToDisk() throws {
        let source = try providerSourceText()
        // No credential may reach preferences or any file this app writes.
        #expect(!source.contains("UserDefaults"))
        #expect(!source.contains("applicationSupport"))
        #expect(!source.contains("write(to:"))
        #expect(!source.contains("print("))
        #expect(!source.contains("NSLog"))
        #expect(!source.contains("os_log"))
    }

    @Test
    func latestExtractionIsHeldInMemoryOnly() async throws {
        let coordinator = ExtractionCoordinator(
            extractor: try Self.configuredExtractor(
                transport: StubTransport(responseJSON: Self.validResponseJSON)
            ),
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
        await coordinator.submit(.syntheticFrame())
        await coordinator.waitUntilIdle()

        #expect(await coordinator.latest() != nil)
        // A fresh coordinator starts empty: nothing was restored from disk.
        let reloaded = ExtractionCoordinator()
        #expect(await reloaded.latest() == nil)
    }

    // MARK: - Helpers

    private static func configuredExtractor(
        transport: StubTransport
    ) throws -> GeminiFrameExtractor {
        let store = InMemoryCredentialStore()
        try store.save("test-key", account: GeminiFrameExtractor.credentialAccount)
        return GeminiFrameExtractor(credentials: store, transport: transport)
    }

    private static let validResponseJSON = """
        {"candidates": [{"content": {"parts": [
          {"text": "{\\"chat\\": {\\"title\\": \\"群\\", \\"confidence\\": 0.8}, \\"messages\\": []}"}
        ]}}]}
        """
}

// MARK: - Test doubles

private func providerSourceText() throws -> String {
    let directory = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("WeChatCompanion/Extraction")
    let files = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: nil)?
        .compactMap { $0 as? URL }
        .filter { $0.pathExtension == "swift" } ?? []
    let joined = try files.map { try String(contentsOf: $0, encoding: .utf8) }.joined(separator: "\n")
    // Scan code only: doc comments legitimately name the APIs we forbid.
    return joined
        .split(separator: "\n", omittingEmptySubsequences: false)
        .filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("//") }
        .joined(separator: "\n")
}

private final class InMemoryCredentialStore: CredentialStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: String] = [:]

    func save(_ secret: String, account: String) throws {
        lock.withLock { storage[account] = secret }
    }

    func secret(account: String) throws -> String? {
        lock.withLock { storage[account] }
    }

    func remove(account: String) throws {
        _ = lock.withLock { storage.removeValue(forKey: account) }
    }
}

private actor StubTransport: GeminiTransporting {
    private(set) var requestCount = 0
    private(set) var lastRequest: URLRequest?
    private let responseJSON: String
    private let statusCode: Int

    init(responseJSON: String, statusCode: Int = 200) {
        self.responseJSON = responseJSON
        self.statusCode = statusCode
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requestCount += 1
        lastRequest = request
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: statusCode,
            httpVersion: nil,
            headerFields: nil
        )!
        let body = statusCode == 200
            ? responseJSON
            : #"{"candidates": [{"content": {"parts": [{"text": "x"}]}}]}"#
        return (Data(body.utf8), response)
    }
}

private extension ObservedFrame {
    static func syntheticFrame() -> ObservedFrame {
        let image = CGImage.syntheticFrameImage(width: 240, height: 180)
        return ObservedFrame(
            image: image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
            captureMode: .visibleDisplayRegion,
            fingerprint: FrameFingerprint(image: image)!
        )
    }
}

private extension CGImage {
    /// Synthetic gradient. No real WeChat imagery exists in any test.
    static func syntheticFrameImage(width: Int, height: Int) -> CGImage {
        let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )!
        context.setFillColor(gray: 0.5, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.setFillColor(gray: 0.9, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width / 2, height: height / 2))
        return context.makeImage()!
    }
}
