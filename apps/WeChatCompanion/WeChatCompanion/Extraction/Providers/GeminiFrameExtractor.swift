import CoreGraphics
import Foundation

/// Transport seam so unit tests never perform a real network call.
protocol GeminiTransporting: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

struct URLSessionGeminiTransport: GeminiTransporting {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw GeminiExtractionError.malformedEnvelope
        }
        return (data, http)
    }
}

/// Content-free response metadata used purely for diagnosis.
struct GeminiResponseMetadata: Equatable, Sendable {
    var finishReason: ExtractionFinishReason?
    var blockReason: ExtractionBlockReason?
    var promptTokenCount: Int?
    var candidatesTokenCount: Int?
    var thoughtsTokenCount: Int?
    var totalTokenCount: Int?
}

/// Errors carry no request or response content -- at most an HTTP status code,
/// closed-set reason enums, and integer counts -- so nothing that could echo a
/// frame or a message can reach metrics or the UI.
///
/// `malformedEnvelope` and `malformedExtractionJSON` are separate on purpose:
/// one means the provider's response shape was unreadable, the other means the
/// response was fine but the extraction payload inside it was not valid JSON.
enum GeminiExtractionError: Error, Equatable, ExtractionFailureDescribing {
    case missingCredential
    /// The Keychain read failed. Carries an OSStatus when one exists.
    case credentialFailure(status: Int?)
    case imageEncoding
    /// Building the request body failed before anything left the machine.
    case requestConstruction
    /// The connection failed before any HTTP response existed.
    case connectionFailure(urlErrorCode: Int)
    case transportFailure(status: Int)
    case malformedEnvelope
    case emptyResponse(GeminiResponseMetadata)
    case blocked(GeminiResponseMetadata)
    case malformedExtractionJSON(GeminiResponseMetadata, outputCharacterCount: Int)

    var failureDiagnostics: ExtractionFailureDiagnostics {
        switch self {
        case .missingCredential:
            ExtractionFailureDiagnostics(category: .missingCredential)
        case let .credentialFailure(status):
            ExtractionFailureDiagnostics(category: .credentialFailure, keychainStatus: status)
        case .imageEncoding:
            ExtractionFailureDiagnostics(category: .imageEncoding)
        case .requestConstruction:
            ExtractionFailureDiagnostics(category: .requestConstruction)
        case let .connectionFailure(code):
            ExtractionFailureDiagnostics(category: .transportFailure, urlErrorCode: code)
        case let .transportFailure(status):
            ExtractionFailureDiagnostics(category: .transportFailure, httpStatus: status)
        case .malformedEnvelope:
            ExtractionFailureDiagnostics(category: .malformedEnvelope)
        case let .emptyResponse(metadata):
            metadata.diagnostics(category: .emptyResponse)
        case let .blocked(metadata):
            metadata.diagnostics(category: .blocked)
        case let .malformedExtractionJSON(metadata, characters):
            metadata.diagnostics(
                category: .malformedExtractionJSON,
                outputCharacterCount: characters
            )
        }
    }
}

extension GeminiResponseMetadata {
    func diagnostics(
        category: ExtractionFailureCategory,
        outputCharacterCount: Int? = nil
    ) -> ExtractionFailureDiagnostics {
        ExtractionFailureDiagnostics(
            category: category,
            finishReason: finishReason,
            blockReason: blockReason,
            outputCharacterCount: outputCharacterCount,
            promptTokenCount: promptTokenCount,
            candidatesTokenCount: candidatesTokenCount,
            thoughtsTokenCount: thoughtsTokenCount,
            totalTokenCount: totalTokenCount
        )
    }
}

/// Gemini multimodal extraction behind the shared `FrameExtracting` seam.
///
/// Declares `.remote`, so the coordinator's explicit-consent gate stays
/// authoritative: without the user's toggle no frame is ever handed here.
/// No Gemini type escapes this file -- the return value is the provider
/// independent `ExtractedConversationFrame`.
struct GeminiFrameExtractor: FrameExtracting {
    static let credentialAccount = "gemini-api-key"
    static let defaultModel = "gemini-3.7-flash"
    static let requestTimeout: TimeInterval = 30

    let isConfigured: Bool
    let processingLocation = ExtractionProcessingLocation.remote

    private let credentials: any CredentialStoring
    private let transport: any GeminiTransporting
    private let model: String

    init(
        credentials: any CredentialStoring,
        transport: any GeminiTransporting = URLSessionGeminiTransport(),
        model: String = GeminiFrameExtractor.defaultModel
    ) {
        self.credentials = credentials
        self.transport = transport
        self.model = model
        // Checked once at configuration time, not once per frame.
        isConfigured = credentials.hasSecret(account: Self.credentialAccount)
    }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame {
        let storedKey: String?
        do {
            storedKey = try credentials.secret(account: Self.credentialAccount)
        } catch let error as CredentialStoreError {
            // Numeric OSStatus only; never the key, account, service or text.
            throw GeminiExtractionError.credentialFailure(status: error.osStatus)
        } catch {
            throw GeminiExtractionError.credentialFailure(status: nil)
        }
        guard let key = storedKey, !key.isEmpty else {
            throw GeminiExtractionError.missingCredential
        }
        try Task.checkCancellation()

        let imageData: Data
        do {
            imageData = try FrameImageEncoder.encodedJPEG(from: frame.image)
        } catch {
            // Classified explicitly rather than falling into a generic bucket.
            throw GeminiExtractionError.imageEncoding
        }
        try Task.checkCancellation()

        let request: URLRequest
        do {
            request = try makeRequest(apiKey: key, imageData: imageData)
        } catch {
            throw GeminiExtractionError.requestConstruction
        }

        let data: Data
        let response: HTTPURLResponse
        do {
            (data, response) = try await transport.send(request)
        } catch let error as GeminiExtractionError {
            throw error
        } catch is CancellationError {
            // Cancellation must stay cancellation, never a transport failure.
            throw CancellationError()
        } catch let error as URLError {
            guard error.code != .cancelled else { throw error }
            throw GeminiExtractionError.connectionFailure(urlErrorCode: error.errorCode)
        }
        try Task.checkCancellation()

        guard (200..<300).contains(response.statusCode) else {
            throw GeminiExtractionError.transportFailure(status: response.statusCode)
        }
        let envelope = try Self.decodeEnvelope(from: data)
        let metadata = Self.metadata(of: envelope)

        // A refusal is not a malformed response; classify it as blocked.
        if metadata.blockReason != nil || metadata.finishReason?.indicatesRefusal == true {
            throw GeminiExtractionError.blocked(metadata)
        }
        guard let payload = Self.candidateText(of: envelope) else {
            throw GeminiExtractionError.emptyResponse(metadata)
        }
        do {
            let dto = try Self.decodeExtraction(from: payload)
            return dto.asExtractedConversationFrame(capturedAt: frame.capturedAt)
        } catch {
            // Only the LENGTH of the unusable output is retained, never the text.
            throw GeminiExtractionError.malformedExtractionJSON(
                metadata,
                outputCharacterCount: payload.count
            )
        }
    }

    private func makeRequest(apiKey: String, imageData: Data) throws -> URLRequest {
        let url = URL(
            string: "https://generativelanguage.googleapis.com/v1beta/models/"
                + "\(model):generateContent"
        )!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = Self.requestTimeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(apiKey, forHTTPHeaderField: "x-goog-api-key")

        let body: [String: Any] = [
            "contents": [[
                "role": "user",
                "parts": [
                    ["text": Self.extractionPrompt],
                    ["inline_data": [
                        "mime_type": "image/jpeg",
                        // Transient: encoded for this request only, never stored.
                        "data": imageData.base64EncodedString()
                    ]]
                ]
            ]],
            "generationConfig": [
                "temperature": 0,
                "responseMimeType": "application/json"
            ]
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        return request
    }

    static func decodeEnvelope(from data: Data) throws -> GeminiResponseEnvelope {
        do {
            return try JSONDecoder().decode(GeminiResponseEnvelope.self, from: data)
        } catch {
            throw GeminiExtractionError.malformedEnvelope
        }
    }

    /// Content-free metadata only: closed-set reasons and integer token counts.
    static func metadata(of envelope: GeminiResponseEnvelope) -> GeminiResponseMetadata {
        GeminiResponseMetadata(
            finishReason: ExtractionFinishReason.normalised(
                envelope.candidates?.first?.finishReason
            ),
            blockReason: ExtractionBlockReason.normalised(
                envelope.promptFeedback?.blockReason
            ),
            promptTokenCount: envelope.usageMetadata?.promptTokenCount,
            candidatesTokenCount: envelope.usageMetadata?.candidatesTokenCount,
            thoughtsTokenCount: envelope.usageMetadata?.thoughtsTokenCount,
            totalTokenCount: envelope.usageMetadata?.totalTokenCount
        )
    }

    static func candidateText(of envelope: GeminiResponseEnvelope) -> String? {
        envelope.candidates?
            .compactMap { $0.content?.parts?.compactMap(\.text).joined() }
            .first(where: { !$0.isEmpty })
    }

    /// Strict: anything that is not the expected JSON object is rejected, so
    /// provider prose can never be accepted as chat data.
    static func decodeExtraction(from text: String) throws -> GeminiExtractionDTO {
        let trimmed = Self.strippingCodeFence(text)
        guard let data = trimmed.data(using: .utf8) else {
            throw GeminiExtractionError.malformedEnvelope
        }
        do {
            return try JSONDecoder().decode(GeminiExtractionDTO.self, from: data)
        } catch {
            throw GeminiExtractionError.malformedExtractionJSON(
                GeminiResponseMetadata(),
                outputCharacterCount: text.count
            )
        }
    }

    private static func strippingCodeFence(_ text: String) -> String {
        var trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix("```") else { return trimmed }
        if let firstNewline = trimmed.firstIndex(of: "\n") {
            trimmed = String(trimmed[trimmed.index(after: firstNewline)...])
        }
        if let fenceRange = trimmed.range(of: "```", options: .backwards) {
            trimmed = String(trimmed[..<fenceRange.lowerBound])
        }
        return trimmed.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static let extractionPrompt = """
        You are reading one screenshot of the WeChat desktop app.

        Extract ONLY what is visibly present in this image. Return JSON matching \
        this schema exactly:

        {
          "chat": {"title": string|null, "confidence": number},
          "messages": [
            {
              "sender": string|null,
              "ownership": "own"|"other"|"unknown",
              "visibleTime": string|null,
              "text": string|null,
              "kind": "text"|"image"|"file"|"link"|"voice"|"system"|"unknown",
              "confidence": number,
              "bounds": {"x": number, "y": number, "width": number, "height": number}|null
            }
          ]
        }

        Rules:
        - Never invent hidden or offscreen messages. Only bubbles visible in this image.
        - Never infer a sender that is not visually identifiable. Use null instead.
        - Decide own vs other primarily from WeChat bubble layout and alignment.
        - "unknown" is always preferable to guessing.
        - Preserve Chinese text exactly as shown. Do not translate.
        - Do not summarize. Do not rewrite or clean up message text. Extract only.
        - System notices (recalls, time separators, joins) are kind "system", not \
        normal user messages.
        - confidence is 0 to 1 for how certain you are of that row.
        - bounds are normalized 0 to 1 relative to the image. If you cannot infer \
        them reliably, return null rather than fabricating them.
        - If nothing is legible, return {"chat": null, "messages": []}.
        """
}

// MARK: - Provider DTO

/// Codable lives only at the provider boundary. The core extracted models stay
/// non-Codable so extracted content cannot be persisted by accident.
struct GeminiExtractionDTO: Codable, Equatable {
    struct Chat: Codable, Equatable {
        let title: String?
        let confidence: Double?
    }

    struct Bounds: Codable, Equatable {
        let x: Double
        let y: Double
        let width: Double
        let height: Double
    }

    struct Message: Codable, Equatable {
        let sender: String?
        let ownership: String?
        let visibleTime: String?
        let text: String?
        let kind: String?
        let confidence: Double?
        let bounds: Bounds?
    }

    let chat: Chat?
    let messages: [Message]?

    func asExtractedConversationFrame(capturedAt: Date) -> ExtractedConversationFrame {
        ExtractedConversationFrame(
            capturedAt: capturedAt,
            chat: chat?.asChatIdentity(),
            messages: (messages ?? []).map { $0.asVisibleMessage() }
        )
    }
}

private extension GeminiExtractionDTO.Chat {
    func asChatIdentity() -> ExtractedChatIdentity? {
        guard let trimmed = title?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        return ExtractedChatIdentity(
            title: trimmed,
            confidence: ExtractionValidation.clampedConfidence(confidence)
        )
    }
}

private extension GeminiExtractionDTO.Message {
    func asVisibleMessage() -> ExtractedVisibleMessage {
        ExtractedVisibleMessage(
            sender: ExtractionValidation.nonEmpty(sender),
            ownership: MessageOwnership(rawValue: ownership ?? "") ?? .unknown,
            visibleTime: ExtractionValidation.nonEmpty(visibleTime),
            text: ExtractionValidation.nonEmpty(text),
            kind: VisibleMessageKind(rawValue: kind ?? "") ?? .unknown,
            confidence: ExtractionValidation.clampedConfidence(confidence),
            normalizedBounds: bounds?.asNormalizedRect()
        )
    }
}

private extension GeminiExtractionDTO.Bounds {
    /// Rejects anything outside the unit square or non-finite rather than
    /// passing along fabricated geometry.
    func asNormalizedRect() -> CGRect? {
        let values = [x, y, width, height]
        guard values.allSatisfy(\.isFinite),
              width > 0, height > 0,
              x >= -0.01, y >= -0.01,
              x + width <= 1.01, y + height <= 1.01 else { return nil }
        return CGRect(x: x, y: y, width: width, height: height)
    }
}

enum ExtractionValidation {
    static func clampedConfidence(_ value: Double?) -> Double {
        guard let value, value.isFinite else { return 0 }
        return min(max(value, 0), 1)
    }

    static func nonEmpty(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        return trimmed
    }
}

// MARK: - Response envelope

/// Only the fields needed for diagnosis are decoded. Safety-rating text and
/// any other free-form provider strings are deliberately not modelled.
struct GeminiResponseEnvelope: Decodable {
    struct Candidate: Decodable {
        struct Content: Decodable {
            struct Part: Decodable {
                let text: String?
            }
            let parts: [Part]?
        }
        let content: Content?
        let finishReason: String?
    }

    struct PromptFeedback: Decodable {
        let blockReason: String?
    }

    struct UsageMetadata: Decodable {
        let promptTokenCount: Int?
        let candidatesTokenCount: Int?
        let thoughtsTokenCount: Int?
        let totalTokenCount: Int?
    }

    let candidates: [Candidate]?
    let promptFeedback: PromptFeedback?
    let usageMetadata: UsageMetadata?
}
