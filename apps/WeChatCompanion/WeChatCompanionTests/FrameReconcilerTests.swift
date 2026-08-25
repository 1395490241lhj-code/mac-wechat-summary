import Foundation
import Testing
@testable import WeChatCompanion

/// All fixtures here are synthetic placeholders. No real WeChat content is
/// used anywhere in this suite.
private func key(_ text: String, ownership: MessageOwnership = .other) -> MessageIdentityKey {
    MessageIdentityKey(
        sender: nil, ownership: ownership, visibleTime: nil, text: text, kind: .text
    )
}

private func keys(_ texts: String...) -> [MessageIdentityKey] { texts.map { key($0) } }

struct FrameReconcilerTests {
    // MARK: - Overlapping frames

    @Test
    func firstFrameOfAnEmptyConversationIsEntirelyNew() {
        let result = FrameReconciler.reconcile(
            incoming: keys("a", "b", "c"), storedTail: []
        )
        #expect(result == .appended(overlap: 0, newMessages: 0..<3))
    }

    @Test
    func overlappingSuffixAndPrefixAppendsOnlyTheTail() {
        // Stored ...c,d ; the next frame scrolled down and shows c,d,e,f.
        let result = FrameReconciler.reconcile(
            incoming: keys("c", "d", "e", "f"),
            storedTail: keys("a", "b", "c", "d")
        )
        #expect(result == .appended(overlap: 2, newMessages: 2..<4))
    }

    @Test
    func anUnchangedFrameAddsNothing() {
        let result = FrameReconciler.reconcile(
            incoming: keys("a", "b", "c"), storedTail: keys("a", "b", "c")
        )
        #expect(result == .nothingNew)
    }

    @Test
    func aFrameFullyContainedInStoredHistoryAddsNothing() {
        // The visible window narrowed: b,c are already held but neither aligns
        // with the stored tail, so only containment prevents a re-append.
        let result = FrameReconciler.reconcile(
            incoming: keys("b", "c"), storedTail: keys("a", "b", "c", "d")
        )
        #expect(result == .nothingNew)
    }

    @Test
    func scrollingUpPrependsRecoveredHistory() {
        let result = FrameReconciler.reconcile(
            incoming: keys("x", "y", "a", "b"),
            storedTail: keys("a", "b", "c"),
            storedHead: keys("a", "b", "c")
        )
        #expect(result == .prepended(overlap: 2, newMessages: 0..<2))
    }

    @Test
    func aJumpWithNoSharedRunIsReportedAsAGap() {
        let result = FrameReconciler.reconcile(
            incoming: keys("p", "q"), storedTail: keys("a", "b")
        )
        #expect(result == .gap(newMessages: 0..<2))
    }

    // MARK: - Repeated text must not be collapsed

    @Test
    func aResentIdenticalMessageIsKeptAlongsideTheFirst() {
        // "ok" was already stored once. The new frame shows it twice: the
        // second one is a genuine second send, not the same bubble again.
        let result = FrameReconciler.reconcile(
            incoming: keys("ok", "ok"), storedTail: keys("hello", "ok")
        )
        #expect(result == .appended(overlap: 1, newMessages: 1..<2))
    }

    @Test
    func twoIdenticalMessagesAlreadyStoredStayTwo() {
        let result = FrameReconciler.reconcile(
            incoming: keys("ok", "ok", "next"), storedTail: keys("ok", "ok")
        )
        #expect(result == .appended(overlap: 2, newMessages: 2..<3))
    }

    @Test
    func identicalTextFromDifferentSidesIsNotTreatedAsTheSameMessage() {
        let incoming = [key("ok", ownership: .other), key("ok", ownership: .own)]
        let result = FrameReconciler.reconcile(
            incoming: incoming, storedTail: [key("ok", ownership: .other)]
        )
        #expect(result == .appended(overlap: 1, newMessages: 1..<2))
    }

    // MARK: - Overlap primitives

    @Test
    func longestOverlapPrefersTheLongestSharedRun() {
        #expect(
            FrameReconciler.longestOverlap(
                suffixOf: keys("a", "b", "b"), prefixOf: keys("b", "b", "c")
            ) == 2
        )
        #expect(
            FrameReconciler.longestOverlap(
                suffixOf: keys("a", "b"), prefixOf: keys("c", "d")
            ) == 0
        )
    }

    @Test
    func anEmptyFrameChangesNothing() {
        #expect(
            FrameReconciler.reconcile(incoming: [], storedTail: keys("a")) == .nothingNew
        )
    }
}
