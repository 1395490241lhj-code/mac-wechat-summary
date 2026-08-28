import Foundation

/// The Gemini models this app is allowed to use.
///
/// A closed set on purpose: the production path cannot be handed an arbitrary
/// model string, and there is deliberately no `gemini-flash-latest` alias. A
/// moving alias hides exactly the kind of silent model change that cost this
/// project a long diagnosis when a pinned model stopped being served.
enum GeminiModel: String, CaseIterable, Identifiable, Codable, Sendable {
    case gemini35Flash = "gemini-3.5-flash"
    case gemini36Flash = "gemini-3.6-flash"
    case gemini37Flash = "gemini-3.7-flash"

    var id: String { rawValue }

    /// Stable API model identifier sent to the provider.
    var modelID: String { rawValue }

    var label: String {
        switch self {
        case .gemini35Flash: "Gemini 3.5 Flash"
        case .gemini36Flash: "Gemini 3.6 Flash"
        case .gemini37Flash: "Gemini 3.7 Flash"
        }
    }

    /// Deliberately the model production already ships. Adding model
    /// *selection* is not a reason to move every existing user onto a different
    /// model: a fresh install, a missing preference and a stale one all still
    /// resolve to 3.7 Flash, exactly as before this change.
    ///
    /// No reproducible benchmark of 3.5 / 3.6 / 3.7 against real extraction
    /// fixtures exists yet, so there is no evidence here to move it. Changing
    /// the default is its own decision and needs that benchmark first -- and
    /// moving *backwards* would sit badly with the retired-model incident that
    /// pinned 3.7 in the first place.
    static let provisionalDefault = GeminiModel.gemini37Flash

    /// Unknown or stale persisted values fall back safely rather than being
    /// passed through to the provider.
    static func resolved(fromStoredID id: String?) -> GeminiModel {
        guard let id, let model = GeminiModel(rawValue: id) else {
            return provisionalDefault
        }
        return model
    }
}
