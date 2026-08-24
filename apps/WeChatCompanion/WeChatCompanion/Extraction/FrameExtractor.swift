import Foundation

/// Where a frame would be processed. This is the explicit consent boundary for
/// raw WeChat imagery: `onDevice` never leaves the Mac, `remote` would.
enum ExtractionProcessingLocation: String, Sendable, Equatable {
    case onDevice
    case remote
}

/// The user's standing decision about remote processing.
///
/// Raw WeChat frames may only leave this Mac when the user has explicitly
/// enabled a remote extraction provider. Nothing in this phase can satisfy
/// that condition -- no networking exists -- but the boundary is enforced now
/// so a future provider cannot be wired in by accident.
struct ExtractionCapability: Sendable, Equatable {
    /// Set only by an explicit user action, never inferred or defaulted on.
    var userEnabledRemoteProvider: Bool

    init(userEnabledRemoteProvider: Bool = false) {
        self.userEnabledRemoteProvider = userEnabledRemoteProvider
    }

    /// The safe default: frames never leave the Mac.
    static let onDeviceOnly = ExtractionCapability()

    /// Whether a frame may be handed to an extractor running in `location`.
    func allowsProcessing(at location: ExtractionProcessingLocation) -> Bool {
        switch location {
        case .onDevice: true
        case .remote: userEnabledRemoteProvider
        }
    }
}

/// The extraction boundary. PassiveObserver knows nothing about it, and no
/// provider-specific type may cross it in either direction: an extractor
/// receives an ObservedFrame and returns provider-independent structure.
protocol FrameExtracting: Sendable {
    /// False until a real extractor is configured. An unconfigured extractor is
    /// never invoked, so it cannot fail once per sample.
    var isConfigured: Bool { get }

    /// Declares whether this extractor would send the frame off the Mac.
    var processingLocation: ExtractionProcessingLocation { get }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame
}

extension FrameExtracting {
    var processingLocation: ExtractionProcessingLocation { .onDevice }
}

enum ExtractionError: Error, Equatable {
    case extractorNotConfigured
    case remoteProcessingNotConsented
}

/// The production default. It fabricates nothing and is never invoked by the
/// coordinator, which short-circuits on `isConfigured == false`.
struct NoConfiguredExtractor: FrameExtracting {
    var isConfigured: Bool { false }
    var processingLocation: ExtractionProcessingLocation { .onDevice }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame {
        throw ExtractionError.extractorNotConfigured
    }
}
