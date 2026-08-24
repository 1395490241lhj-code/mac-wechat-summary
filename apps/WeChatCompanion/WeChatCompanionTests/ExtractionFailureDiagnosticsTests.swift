import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

/// The first real provider failure was unattributable because every error
/// collapsed into one counter. These prove each category is now distinguishable
/// from a single request, using aggregate metadata only.
struct ExtractionFailureDiagnosticsTests {
    // MARK: - Transport

    @Test(arguments: [400, 429, 500, 503])
    func httpErrorsRecordTransportFailureWithStatusOnly(status: Int) async throws {
        let diagnostics = try await failureDiagnostics(
            responseJSON: #"{"error": {"message": "secret-echo-of-request"}}"#,
            statusCode: status
        )
        #expect(diagnostics.category == .transportFailure)
        #expect(diagnostics.httpStatus == status)
        #expect(diagnostics.finishReason == nil)
        #expect(diagnostics.outputCharacterCount == nil)
    }

    // MARK: - Envelope vs extraction JSON

    @Test
    func unreadableOuterJSONIsMalformedEnvelope() async throws {
        let diagnostics = try await failureDiagnostics(responseJSON: "not json at all")
        #expect(diagnostics.category == .malformedEnvelope)
        #expect(diagnostics.httpStatus == nil)
    }

    @Test
    func validEnvelopeWithNoCandidateTextIsEmptyResponse() async throws {
        let diagnostics = try await failureDiagnostics(responseJSON: """
            {"candidates": [], "usageMetadata": {"promptTokenCount": 1200,
             "candidatesTokenCount": 0, "totalTokenCount": 1200}}
            """)
        #expect(diagnostics.category == .emptyResponse)
        #expect(diagnostics.promptTokenCount == 1200)
        #expect(diagnostics.candidatesTokenCount == 0)
    }

    /// The distinction the whole phase exists for.
    @Test
    func validTextButInvalidExtractionJSONIsMalformedExtractionJSON() async throws {
        let prose = "I'm sorry, I can't read this screenshot."
        let diagnostics = try await failureDiagnostics(responseJSON: envelope(
            text: prose, finishReason: "STOP"
        ))
        #expect(diagnostics.category == .malformedExtractionJSON)
        #expect(diagnostics.finishReason == .stop)
        #expect(diagnostics.outputCharacterCount == prose.count)
    }

    /// finishReason distinguishes truncation from an off-schema model answer.
    @Test
    func truncatedOutputPreservesMaxTokensAndLengthButNotText() async throws {
        let truncated = "{\"chat\": {\"title\": \"x\"}, \"messages\": [{\"kind\": \"te"
        let diagnostics = try await failureDiagnostics(responseJSON: envelope(
            text: truncated,
            finishReason: "MAX_TOKENS",
            usage: [
                "promptTokenCount": 1500, "candidatesTokenCount": 8192,
                "thoughtsTokenCount": 700, "totalTokenCount": 10392
            ]
        ))
        #expect(diagnostics.category == .malformedExtractionJSON)
        #expect(diagnostics.finishReason == .maxTokens)
        #expect(diagnostics.outputCharacterCount == truncated.count)
        #expect(diagnostics.candidatesTokenCount == 8192)
        #expect(diagnostics.thoughtsTokenCount == 700)
        #expect(diagnostics.totalTokenCount == 10392)

        // The text itself must not survive anywhere in the diagnosis.
        let json = String(decoding: try JSONEncoder().encode(diagnostics), as: UTF8.self)
        #expect(!json.contains("chat"))
        #expect(!json.contains("title"))
        #expect(!json.contains("messages"))
    }

    // MARK: - Blocked

    @Test
    func promptFeedbackBlockReasonIsBlocked() async throws {
        let diagnostics = try await failureDiagnostics(responseJSON: """
            {"promptFeedback": {"blockReason": "SAFETY"}}
            """)
        #expect(diagnostics.category == .blocked)
        #expect(diagnostics.blockReason == .safety)
    }

    @Test(arguments: ["SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"])
    func refusalFinishReasonsAreBlocked(reason: String) async throws {
        let diagnostics = try await failureDiagnostics(responseJSON: envelope(
            text: nil, finishReason: reason
        ))
        #expect(diagnostics.category == .blocked)
        #expect(diagnostics.finishReason?.indicatesRefusal == true)
    }

    // MARK: - Credential and encoding

    @Test
    func missingCredentialIsItsOwnCategory() {
        #expect(GeminiExtractionError.missingCredential.failureDiagnostics.category
            == .missingCredential)
    }

    @Test
    func imageEncodingFailureIsItsOwnCategory() {
        #expect(GeminiExtractionError.imageEncoding.failureDiagnostics.category
            == .imageEncoding)
    }

    // MARK: - Success

    @Test
    func successfulExtractionRecordsNoFailureDiagnosis() async throws {
        let transport = DiagnosticsStubTransport(
            responseJSON: envelope(
                text: "{\"chat\": null, \"messages\": []}", finishReason: "STOP"
            ),
            statusCode: 200
        )
        let coordinator = try await coordinator(transport: transport)
        await coordinator.submit(.diagnosticsFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsSucceeded == 1)
        #expect(metrics.extractionsFailed == 0)
        #expect(metrics.lastFailure == nil)
    }

    /// The generic counter keeps its old meaning; only the diagnosis is new.
    @Test
    func genericFailureCounterIsUnchangedByClassification() async throws {
        let transport = DiagnosticsStubTransport(responseJSON: "not json", statusCode: 200)
        let coordinator = try await coordinator(transport: transport)
        await coordinator.submit(.diagnosticsFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsFailed == 1)
        #expect(metrics.extractionsCancelled == 0)
        #expect(metrics.lastFailure?.category == .malformedEnvelope)
    }

    // MARK: - Connection-level failures (no HTTP response)

    @Test(arguments: [
        URLError.timedOut, .cannotFindHost, .cannotConnectToHost,
        .notConnectedToInternet, .secureConnectionFailed, .networkConnectionLost
    ])
    func connectionFailuresRecordURLErrorCodeAndNoHTTPStatus(
        code: URLError.Code
    ) async throws {
        let diagnostics = try await failureDiagnostics(
            transport: ThrowingTransport(error: URLError(code))
        )
        #expect(diagnostics.category == .transportFailure)
        #expect(diagnostics.urlErrorCode == code.rawValue)
        // Mutually exclusive with an HTTP status: no response ever existed.
        #expect(diagnostics.httpStatus == nil)
        #expect(diagnostics.finishReason == nil)
    }

    /// An HTTP error response is a different shape of transport failure and
    /// must never gain a URL error code.
    @Test(arguments: [400, 429])
    func httpTransportFailuresNeverCarryAURLErrorCode(status: Int) async throws {
        let diagnostics = try await failureDiagnostics(
            responseJSON: "{}", statusCode: status
        )
        #expect(diagnostics.category == .transportFailure)
        #expect(diagnostics.httpStatus == status)
        #expect(diagnostics.urlErrorCode == nil)
    }

    // MARK: - Cancellation must survive classification

    @Test
    func urlErrorCancelledStaysCancellationAndRecordsNoDiagnosis() async throws {
        let coordinator = try await coordinator(
            transport: ThrowingTransport(error: URLError(.cancelled))
        )
        await coordinator.submit(.diagnosticsFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsCancelled == 1)
        #expect(metrics.extractionsFailed == 0)
        #expect(metrics.lastFailure == nil)
        #expect(metrics.lastCancellationAt != nil)
        #expect(metrics.lastExtractionAt == nil)
    }

    @Test
    func cancellationErrorStaysCancellationAndRecordsNoDiagnosis() async throws {
        let coordinator = try await coordinator(
            transport: ThrowingTransport(error: CancellationError())
        )
        await coordinator.submit(.diagnosticsFrame())
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsCancelled == 1)
        #expect(metrics.extractionsFailed == 0)
        #expect(metrics.lastFailure == nil)
    }

    // MARK: - Credential store failures

    @Test
    func keychainFailureRecordsOSStatusOnly() async throws {
        let diagnostics = try await failureDiagnostics(
            credentials: FailingCredentialStore(error: .keychainFailure(status: -25300))
        )
        #expect(diagnostics.category == .credentialFailure)
        #expect(diagnostics.keychainStatus == -25300)
        #expect(diagnostics.httpStatus == nil)
        #expect(diagnostics.urlErrorCode == nil)
    }

    @Test
    func invalidSecretEncodingIsCredentialFailureWithoutStatus() async throws {
        let diagnostics = try await failureDiagnostics(
            credentials: FailingCredentialStore(error: .invalidSecretEncoding)
        )
        #expect(diagnostics.category == .credentialFailure)
        #expect(diagnostics.keychainStatus == nil)

        let json = String(decoding: try JSONEncoder().encode(diagnostics), as: UTF8.self)
        #expect(!json.contains("invalidSecretEncoding"))
        #expect(!json.contains("gemini-api-key"))
    }

    // MARK: - Request construction

    /// A non-encodable body is the only realistic way makeRequest can throw.
    @Test
    func requestConstructionFailureIsItsOwnCategory() {
        #expect(GeminiExtractionError.requestConstruction.failureDiagnostics.category
            == .requestConstruction)
    }

    // MARK: - Unknown fallback

    @Test
    func unknownErrorRemainsOther() async throws {
        let diagnostics = try await failureDiagnostics(
            transport: ThrowingTransport(error: UnforeseenTestError())
        )
        #expect(diagnostics.category == .other)
        #expect(diagnostics.httpStatus == nil)
        #expect(diagnostics.urlErrorCode == nil)
        #expect(diagnostics.keychainStatus == nil)
    }

    // MARK: - Connection diagnostics privacy

    @Test
    func connectionDiagnosticsRetainNoURLHostProxyOrDescription() async throws {
        let error = URLError(
            .cannotFindHost,
            userInfo: [
                NSURLErrorFailingURLStringErrorKey:
                    "https://generativelanguage.googleapis.com/secret-path",
                NSLocalizedDescriptionKey: "A server with the specified hostname could not be found."
            ]
        )
        let diagnostics = try await failureDiagnostics(
            transport: ThrowingTransport(error: error)
        )
        let json = String(decoding: try JSONEncoder().encode(diagnostics), as: UTF8.self)

        #expect(diagnostics.urlErrorCode == URLError.Code.cannotFindHost.rawValue)
        for forbidden in [
            "generativelanguage", "googleapis", "https", "secret-path", "hostname",
            "could not be found", "proxy", "userInfo", "NSURL", "test-key"
        ] {
            #expect(!json.contains(forbidden), "\(forbidden) must not be retained")
        }
    }

    // MARK: - Closed-set normalisation

    /// Arbitrary provider strings can never reach diagnostics through these
    /// fields: anything unknown collapses to `.unrecognised`.
    @Test
    func unknownProviderReasonsCollapseToUnrecognised() {
        #expect(ExtractionFinishReason.normalised("STOP") == .stop)
        #expect(ExtractionFinishReason.normalised("MAX_TOKENS") == .maxTokens)
        #expect(ExtractionFinishReason.normalised("some secret detail") == .unrecognised)
        #expect(ExtractionFinishReason.normalised(nil) == nil)
        #expect(ExtractionBlockReason.normalised("SAFETY") == .safety)
        #expect(ExtractionBlockReason.normalised("leaked text") == .unrecognised)
        #expect(ExtractionBlockReason.normalised(nil) == nil)
    }

    // MARK: - Privacy

    @Test
    func diagnosticsRetainNoBodyMessageKeyOrImageData() async throws {
        let secret = "provider-body-that-must-not-be-stored"
        let diagnostics = try await failureDiagnostics(
            responseJSON: "{\"error\": {\"message\": \"\(secret)\"}}",
            statusCode: 403
        )
        let json = String(decoding: try JSONEncoder().encode(diagnostics), as: UTF8.self)

        #expect(!json.contains(secret))
        #expect(!json.contains("test-key"))
        #expect(!json.contains("error"))
        #expect(!json.contains("message"))
        #expect(!json.contains("data"))
        #expect(!json.contains("image"))
        // Only the safe metadata keys may appear.
        #expect(json.contains("category"))
        #expect(json.contains("httpStatus"))
    }

    /// Structural guard: the diagnostics type has no field able to hold text.
    @Test
    func diagnosticsTypeExposesOnlySafeMetadata() throws {
        let full = ExtractionFailureDiagnostics(
            category: .malformedExtractionJSON,
            httpStatus: 200,
            urlErrorCode: -1009,
            keychainStatus: -25300,
            finishReason: .maxTokens,
            blockReason: .safety,
            outputCharacterCount: 4096,
            promptTokenCount: 1,
            candidatesTokenCount: 2,
            thoughtsTokenCount: 3,
            totalTokenCount: 6,
            occurredAt: Date(timeIntervalSince1970: 1_700_000_000)
        )
        let object = try JSONSerialization.jsonObject(
            with: try JSONEncoder().encode(full)
        ) as? [String: Any]
        let keys = Set((object ?? [:]).keys)

        // Every field must be listed here, so a newly added one fails this
        // guard rather than slipping into diagnostics unreviewed.
        #expect(keys == [
            "category", "httpStatus", "urlErrorCode", "keychainStatus",
            "finishReason", "blockReason", "outputCharacterCount",
            "promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount",
            "totalTokenCount", "occurredAt"
        ])
    }

    // MARK: - Helpers

    /// Builds a provider envelope through JSONSerialization so the test never
    /// depends on hand-escaped JSON string literals.
    private func envelope(
        text: String?,
        finishReason: String,
        usage: [String: Int]? = nil
    ) -> String {
        var candidate: [String: Any] = ["finishReason": finishReason]
        if let text {
            candidate["content"] = ["parts": [["text": text]]]
        }
        var payload: [String: Any] = ["candidates": [candidate]]
        if let usage { payload["usageMetadata"] = usage }
        let data = try! JSONSerialization.data(withJSONObject: payload)
        return String(decoding: data, as: UTF8.self)
    }

    private func coordinator(
        transport: any GeminiTransporting,
        credentials: (any CredentialStoring)? = nil
    ) async throws -> ExtractionCoordinator {
        let store: any CredentialStoring
        if let credentials {
            store = credentials
        } else {
            let inMemory = InMemoryDiagnosticsCredentialStore()
            try inMemory.save("test-key", account: GeminiFrameExtractor.credentialAccount)
            store = inMemory
        }
        return ExtractionCoordinator(
            extractor: GeminiFrameExtractor(credentials: store, transport: transport),
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
    }

    private func failureDiagnostics(
        responseJSON: String = "{}",
        statusCode: Int = 200,
        transport: (any GeminiTransporting)? = nil,
        credentials: (any CredentialStoring)? = nil
    ) async throws -> ExtractionFailureDiagnostics {
        let resolved = transport ?? DiagnosticsStubTransport(
            responseJSON: responseJSON, statusCode: statusCode
        )
        let coordinator = try await coordinator(
            transport: resolved, credentials: credentials
        )
        await coordinator.submit(.diagnosticsFrame())
        await coordinator.waitUntilIdle()
        let metrics = await coordinator.snapshot()
        return try #require(metrics.lastFailure)
    }
}

// MARK: - Test doubles

private actor DiagnosticsStubTransport: GeminiTransporting {
    private let responseJSON: String
    private let statusCode: Int

    init(responseJSON: String, statusCode: Int) {
        self.responseJSON = responseJSON
        self.statusCode = statusCode
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let response = HTTPURLResponse(
            url: request.url!, statusCode: statusCode, httpVersion: nil, headerFields: nil
        )!
        return (Data(responseJSON.utf8), response)
    }
}

private struct UnforeseenTestError: Error {}

/// Transport that always throws, so connection-level paths can be exercised.
private struct ThrowingTransport: GeminiTransporting {
    let error: any Error
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        throw error
    }
}

/// Credential store that reads once successfully and fails afterwards.
///
/// This models the only way `credentialFailure` is reachable in production:
/// `isConfigured` probes the store with `try?` at construction, so a store that
/// fails immediately makes the extractor report "not configured" and never run.
/// A Keychain that becomes unreadable later — locked, or ACL changed — fails on
/// the read inside `extract`.
private final class FailingCredentialStore: CredentialStoring, @unchecked Sendable {
    private let error: CredentialStoreError
    private let lock = NSLock()
    private var reads = 0

    init(error: CredentialStoreError) { self.error = error }

    func save(_ secret: String, account: String) throws {}

    func secret(account: String) throws -> String? {
        let isFirstRead = lock.withLock {
            reads += 1
            return reads == 1
        }
        if isFirstRead { return "test-key" }
        throw error
    }

    func remove(account: String) throws {}
}

private final class InMemoryDiagnosticsCredentialStore: CredentialStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: String] = [:]
    func save(_ secret: String, account: String) throws {
        lock.withLock { storage[account] = secret }
    }
    func secret(account: String) throws -> String? { lock.withLock { storage[account] } }
    func remove(account: String) throws { _ = lock.withLock { storage.removeValue(forKey: account) } }
}

private extension ObservedFrame {
    /// Synthetic frame. No real WeChat imagery in any test.
    static func diagnosticsFrame() -> ObservedFrame {
        let side = 32
        let context = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8,
            bytesPerRow: side, space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        )!
        context.setFillColor(gray: 0.5, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: side, height: side))
        let image = context.makeImage()!
        return ObservedFrame(
            image: image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
            captureMode: .systemSelectedWindow,
            fingerprint: FrameFingerprint(image: image)!
        )
    }
}
