import Foundation

/// Why an extraction attempt failed, in provider-independent terms.
///
/// `malformedEnvelope` and `malformedExtractionJSON` are deliberately separate:
/// the first means the provider's own response shape was unreadable, the second
/// means the response arrived fine but the extraction payload inside it was not
/// valid JSON. Collapsing them hides the difference between a transport/API
/// problem and a truncated or off-schema model output.
enum ExtractionFailureCategory: String, Codable, Sendable, Equatable {
    case missingCredential
    /// The credential store itself failed, as opposed to holding nothing.
    case credentialFailure
    case imageEncoding
    /// Building the HTTP request failed before anything was sent.
    case requestConstruction
    /// Covers both an HTTP error response and a connection that never reached
    /// one. The two are told apart by which field is populated: `httpStatus`
    /// for the former, `urlErrorCode` for the latter.
    case transportFailure
    case malformedEnvelope
    case emptyResponse
    case malformedExtractionJSON
    case blocked
    case other

    var label: String {
        switch self {
        case .missingCredential: "Missing Credential"
        case .credentialFailure: "Credential Failure"
        case .imageEncoding: "Image Encoding"
        case .requestConstruction: "Request Construction"
        case .transportFailure: "Transport Failure"
        case .malformedEnvelope: "Malformed Envelope"
        case .emptyResponse: "Empty Response"
        case .malformedExtractionJSON: "Malformed Extraction JSON"
        case .blocked: "Blocked"
        case .other: "Other"
        }
    }
}

/// Why the model stopped generating.
///
/// A closed set: provider values are normalised into these cases and anything
/// unrecognised becomes `.unrecognised`. That makes it impossible for arbitrary
/// provider text to reach diagnostics through this field.
enum ExtractionFinishReason: String, Codable, Sendable, Equatable {
    case stop
    case maxTokens
    case safety
    case recitation
    case blocklist
    case prohibitedContent
    case language
    case malformedFunctionCall
    case other
    case unrecognised

    static func normalised(_ raw: String?) -> Self? {
        guard let raw, !raw.isEmpty else { return nil }
        return switch raw.uppercased() {
        case "STOP": .stop
        case "MAX_TOKENS": .maxTokens
        case "SAFETY": .safety
        case "RECITATION": .recitation
        case "BLOCKLIST": .blocklist
        case "PROHIBITED_CONTENT", "SPII": .prohibitedContent
        case "LANGUAGE": .language
        case "MALFORMED_FUNCTION_CALL": .malformedFunctionCall
        case "OTHER": .other
        default: .unrecognised
        }
    }

    /// Finish reasons that mean the provider refused rather than failed.
    var indicatesRefusal: Bool {
        switch self {
        case .safety, .recitation, .blocklist, .prohibitedContent: true
        default: false
        }
    }

    var label: String {
        switch self {
        case .stop: "STOP"
        case .maxTokens: "MAX_TOKENS"
        case .safety: "SAFETY"
        case .recitation: "RECITATION"
        case .blocklist: "BLOCKLIST"
        case .prohibitedContent: "PROHIBITED_CONTENT"
        case .language: "LANGUAGE"
        case .malformedFunctionCall: "MALFORMED_FUNCTION_CALL"
        case .other: "OTHER"
        case .unrecognised: "UNRECOGNISED"
        }
    }
}

/// Why a prompt was rejected outright. Closed set, same reasoning as above.
enum ExtractionBlockReason: String, Codable, Sendable, Equatable {
    case safety
    case blocklist
    case prohibitedContent
    case imageSafety
    case other
    case unrecognised

    static func normalised(_ raw: String?) -> Self? {
        guard let raw, !raw.isEmpty else { return nil }
        return switch raw.uppercased() {
        case "SAFETY": .safety
        case "BLOCKLIST": .blocklist
        case "PROHIBITED_CONTENT", "SPII": .prohibitedContent
        case "IMAGE_SAFETY": .imageSafety
        case "OTHER": .other
        default: .unrecognised
        }
    }

    var label: String {
        switch self {
        case .safety: "SAFETY"
        case .blocklist: "BLOCKLIST"
        case .prohibitedContent: "PROHIBITED_CONTENT"
        case .imageSafety: "IMAGE_SAFETY"
        case .other: "OTHER"
        case .unrecognised: "UNRECOGNISED"
        }
    }
}

/// Aggregate-only failure diagnostics.
///
/// Every field is a category, a closed enum, an integer count, or a timestamp.
/// There is deliberately no field capable of holding response text, prompt
/// text, message content, a chat title, an image, or an API key -- so no such
/// value can be recorded even by mistake.
struct ExtractionFailureDiagnostics: Codable, Sendable, Equatable {
    var category: ExtractionFailureCategory
    /// Status code only, never a response body.
    var httpStatus: Int?
    /// Numeric URLError code when the connection failed before any HTTP
    /// response existed (for example -1009 notConnectedToInternet). The failing
    /// URL, hostname, proxy, userInfo and localizedDescription are never kept.
    var urlErrorCode: Int?
    /// Numeric OSStatus from the Keychain. Never the key, account or service.
    var keychainStatus: Int?
    var finishReason: ExtractionFinishReason?
    var blockReason: ExtractionBlockReason?
    /// Length of the unusable model output. The output itself is never kept.
    var outputCharacterCount: Int?
    var promptTokenCount: Int?
    var candidatesTokenCount: Int?
    var thoughtsTokenCount: Int?
    var totalTokenCount: Int?
    var occurredAt: Date?

    init(
        category: ExtractionFailureCategory,
        httpStatus: Int? = nil,
        urlErrorCode: Int? = nil,
        keychainStatus: Int? = nil,
        finishReason: ExtractionFinishReason? = nil,
        blockReason: ExtractionBlockReason? = nil,
        outputCharacterCount: Int? = nil,
        promptTokenCount: Int? = nil,
        candidatesTokenCount: Int? = nil,
        thoughtsTokenCount: Int? = nil,
        totalTokenCount: Int? = nil,
        occurredAt: Date? = nil
    ) {
        self.category = category
        self.httpStatus = httpStatus
        self.urlErrorCode = urlErrorCode
        self.keychainStatus = keychainStatus
        self.finishReason = finishReason
        self.blockReason = blockReason
        self.outputCharacterCount = outputCharacterCount
        self.promptTokenCount = promptTokenCount
        self.candidatesTokenCount = candidatesTokenCount
        self.thoughtsTokenCount = thoughtsTokenCount
        self.totalTokenCount = totalTokenCount
        self.occurredAt = occurredAt
    }
}

/// Lets an extraction error describe itself without exposing provider types.
/// The coordinator depends on this, never on Gemini.
protocol ExtractionFailureDescribing: Error {
    var failureDiagnostics: ExtractionFailureDiagnostics { get }
}
