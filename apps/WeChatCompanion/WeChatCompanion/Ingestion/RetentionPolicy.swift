import Foundation

/// How long reconciled messages are kept on this Mac.
///
/// Only meaningful while local persistence is enabled: with the consent off
/// nothing is written, so there is nothing to expire.
enum RetentionPolicy: String, CaseIterable, Codable, Sendable, Identifiable {
    case sevenDays
    case thirtyDays
    case ninetyDays
    case untilDeleted

    /// The safe middle ground, and what a fresh install gets.
    static let defaultPolicy = RetentionPolicy.thirtyDays

    var id: String { rawValue }

    var label: String {
        switch self {
        case .sevenDays: "7 days"
        case .thirtyDays: "30 days"
        case .ninetyDays: "90 days"
        case .untilDeleted: "Until I delete it"
        }
    }

    /// nil means "never expires". Measured against `firstObservedAt` -- when we
    /// actually saw the message -- and never against `visibleTime`, which is a
    /// display string like "昨天" that carries no reliable date.
    var maximumAge: TimeInterval? {
        switch self {
        case .sevenDays: 7 * 86_400
        case .thirtyDays: 30 * 86_400
        case .ninetyDays: 90 * 86_400
        case .untilDeleted: nil
        }
    }

    /// Falls back to the default rather than throwing, so an unrecognised or
    /// absent stored value can never leave retention undefined.
    static func resolved(fromStoredID id: String?) -> RetentionPolicy {
        guard let id, let policy = RetentionPolicy(rawValue: id) else {
            return .defaultPolicy
        }
        return policy
    }
}
