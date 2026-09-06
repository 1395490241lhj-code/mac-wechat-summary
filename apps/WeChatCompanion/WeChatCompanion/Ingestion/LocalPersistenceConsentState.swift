import Foundation

/// The app-owned record of the local-persistence consent, in a shape another
/// local process can read without the app running.
///
/// The consent itself has always lived in this app's preferences as a single
/// boolean. That boolean is enough for the app, which reads it in-process, but
/// it is not enough for a separate local process that needs to know whether
/// the answer it read is *current*, *complete*, and *the app's*: an absent key,
/// a stale key and a key written by nothing at all are indistinguishable. This
/// value fixes that by being versioned, generation-counted and written only
/// here.
///
/// It is deliberately tiny and deliberately dull. It carries two booleans, a
/// schema version, a generation counter and a timestamp -- never a chat, a
/// sender, a database path, a credential or anything a reader produced. It is
/// stored in the same preferences domain as the consent flag it describes, so
/// there is one settings system and one owner.
///
/// Absence is denial. A fresh install has no state, and a process that finds
/// none must treat the answer as "no" rather than "unknown, so probably fine".
struct LocalPersistenceConsentState: Equatable {
    /// The preferences key. A dictionary, so it is one atomic value.
    static let key = "consent.state"
    /// Bumped only when the *shape* changes. A reader must refuse a version it
    /// does not know rather than pick out the fields it recognises.
    static let schemaVersion = 1

    /// May extracted message text be written to the app's own local store.
    var allowsLocalMessageStorage: Bool
    /// May a separate local memory store hold normalised message text.
    ///
    /// Governed by the same user decision today: both are the one question
    /// "may extracted text be written down", and a second toggle for the same
    /// question would be a second place for the answer to drift. It is a
    /// distinct field so that, if the product ever decides memory deserves its
    /// own consent, the shape does not have to change -- only what writes it.
    var allowsMemoryStorage: Bool
    /// Increases on every write. Lets a reader tell a newer state from an
    /// older one without trusting the clock.
    var generation: Int
    /// Seconds since 1970, exactly as stored, so a value round-trips equal.
    var updatedAt: TimeInterval

    private enum Field {
        static let version = "version"
        static let allowsLocalMessageStorage = "allowsLocalMessageStorage"
        static let allowsMemoryStorage = "allowsMemoryStorage"
        static let generation = "generation"
        static let updatedAt = "updatedAt"
    }

    /// The property-list representation. Every value is a plist scalar so the
    /// dictionary round-trips through `UserDefaults` and `defaults export`
    /// without a custom encoder on either side.
    var dictionary: [String: Any] {
        [
            Field.version: Self.schemaVersion,
            Field.allowsLocalMessageStorage: allowsLocalMessageStorage,
            Field.allowsMemoryStorage: allowsMemoryStorage,
            Field.generation: generation,
            Field.updatedAt: updatedAt,
        ]
    }

    /// Strict decoding. A missing field, a wrong type, an unknown version or a
    /// negative generation all yield `nil`; nothing is defaulted, because a
    /// default here would be a consent nobody gave.
    init?(dictionary: [String: Any]) {
        guard let version = dictionary[Field.version] as? Int,
              version == Self.schemaVersion,
              let local = dictionary[Field.allowsLocalMessageStorage] as? Bool,
              let memory = dictionary[Field.allowsMemoryStorage] as? Bool,
              let generation = dictionary[Field.generation] as? Int,
              generation >= 0,
              let updated = dictionary[Field.updatedAt] as? Double
        else { return nil }
        self.allowsLocalMessageStorage = local
        self.allowsMemoryStorage = memory
        self.generation = generation
        self.updatedAt = updated
    }

    init(allowsLocalMessageStorage: Bool, allowsMemoryStorage: Bool, generation: Int, updatedAt: TimeInterval) {
        self.allowsLocalMessageStorage = allowsLocalMessageStorage
        self.allowsMemoryStorage = allowsMemoryStorage
        self.generation = generation
        self.updatedAt = updatedAt
    }

    static func load(from defaults: UserDefaults) -> LocalPersistenceConsentState? {
        guard let stored = defaults.dictionary(forKey: key) else { return nil }
        return LocalPersistenceConsentState(dictionary: stored)
    }

    /// Writes the next generation of the state for the given decision.
    ///
    /// The generation continues from whatever is stored, so a reader watching
    /// the value sees it move even when the booleans do not. A malformed stored
    /// state is simply replaced: this app is the authority, and the way it
    /// asserts that is by writing a well-formed value.
    @discardableResult
    static func record(
        allowsLocalMessageStorage: Bool,
        in defaults: UserDefaults,
        now: Date = Date()
    ) -> LocalPersistenceConsentState {
        let previous = load(from: defaults)?.generation ?? 0
        let state = LocalPersistenceConsentState(
            allowsLocalMessageStorage: allowsLocalMessageStorage,
            // One decision, two fields; see `allowsMemoryStorage`.
            allowsMemoryStorage: allowsLocalMessageStorage,
            generation: previous + 1,
            updatedAt: now.timeIntervalSince1970
        )
        defaults.set(state.dictionary, forKey: key)
        return state
    }
}
