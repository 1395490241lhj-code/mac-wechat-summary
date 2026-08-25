import Foundation

/// What a newly observed frame contributes to a conversation already on disk.
enum FrameReconciliation: Sendable, Equatable {
    /// The frame's leading `overlap` messages were already stored; the messages
    /// at `newMessages` are new and belong after the stored tail.
    /// `overlap == 0` with an empty store is the first-frame case.
    case appended(overlap: Int, newMessages: Range<Int>)
    /// The frame scrolled UP and revealed history above what is stored. Its
    /// trailing `overlap` messages were already stored; `newMessages` belong
    /// before the stored head.
    case prepended(overlap: Int, newMessages: Range<Int>)
    /// The frame shares no run with the stored window at either end and is not
    /// contained in it -- the view jumped further than one screen. The messages
    /// are still new, but continuity to the stored tail is NOT established.
    case gap(newMessages: Range<Int>)
    /// Everything visible in this frame is already stored. The common case for
    /// a chat sitting still.
    case nothingNew
}

/// Reconciles a frame's visible messages against the messages already stored.
///
/// Deduplication is by *run alignment*, never by hashing individual messages.
/// Consecutive frames overlap heavily, and WeChat is full of repeated text
/// ("ok", "收到", the same sticker twice). Hashing would silently swallow the
/// second real send of an identical message; matching the longest shared run
/// keeps both, because they occupy different positions in the sequence.
enum FrameReconciler {
    /// - Parameters:
    ///   - incoming: the frame's messages, in visual top-to-bottom order.
    ///   - storedTail: the NEWEST stored messages, oldest-to-newest. A window is
    ///     enough: an overlap can never be longer than one screen of bubbles.
    ///   - storedHead: the OLDEST stored messages, oldest-to-newest. Matched
    ///     against only when the frame scrolled up past everything stored.
    ///     Defaults to `storedTail`, which is the same list for any
    ///     conversation shorter than the window.
    static func reconcile(
        incoming: [MessageIdentityKey],
        storedTail: [MessageIdentityKey],
        storedHead: [MessageIdentityKey]? = nil
    ) -> FrameReconciliation {
        let head = storedHead ?? storedTail
        guard !incoming.isEmpty else { return .nothingNew }
        guard !storedTail.isEmpty else {
            return .appended(overlap: 0, newMessages: 0..<incoming.count)
        }

        // Scrolled down / new message arrived: stored tail == frame head.
        let forward = longestOverlap(suffixOf: storedTail, prefixOf: incoming)
        if forward > 0 {
            guard forward < incoming.count else { return .nothingNew }
            return .appended(overlap: forward, newMessages: forward..<incoming.count)
        }

        // The window shrank or drifted inward: the whole frame is already held.
        // Checked BEFORE declaring a gap, otherwise a narrower view of the same
        // messages would be appended a second time.
        if contains(storedTail, incoming) || contains(head, incoming) {
            return .nothingNew
        }

        // Scrolled up: frame tail == stored head, so the frame shows older history.
        let backward = longestOverlap(suffixOf: incoming, prefixOf: head)
        if backward > 0 {
            return .prepended(
                overlap: backward,
                newMessages: 0..<(incoming.count - backward)
            )
        }

        return .gap(newMessages: 0..<incoming.count)
    }

    /// Largest `k` where `a.suffix(k) == b.prefix(k)`.
    ///
    /// Largest, not smallest, is the conservative choice: it assumes the shared
    /// run is genuine overlap rather than newly arrived repeats. It can under
    /// append when someone re-sends a whole screen of identical messages, which
    /// loses a message; the alternative over-appends, which invents one. Losing
    /// beats inventing.
    static func longestOverlap(
        suffixOf a: [MessageIdentityKey],
        prefixOf b: [MessageIdentityKey]
    ) -> Int {
        var k = min(a.count, b.count)
        while k > 0 {
            if a.suffix(k).elementsEqual(b.prefix(k)) { return k }
            k -= 1
        }
        return 0
    }

    /// Whether `needle` appears as a contiguous run anywhere inside `haystack`.
    static func contains(_ haystack: [MessageIdentityKey], _ needle: [MessageIdentityKey]) -> Bool {
        guard !needle.isEmpty, needle.count <= haystack.count else { return false }
        for start in 0...(haystack.count - needle.count) {
            if haystack[start..<(start + needle.count)].elementsEqual(needle) { return true }
        }
        return false
    }
}
