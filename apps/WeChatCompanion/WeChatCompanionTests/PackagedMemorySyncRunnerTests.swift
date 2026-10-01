import Foundation
import Testing
@testable import WeChatCompanion

/// The packaged runner (M2.2d), driven against a stub worker executable.
///
/// These exercise the real code path -- a real child process, a real JSON
/// request on its stdin, a real reply parsed from its stdout -- without
/// needing the 20 MB frozen worker in the test environment. The frozen worker
/// itself is covered end to end by the Python suite.
private struct StubWorker {
    let directory: URL
    let executable: URL
    var envDump: URL { directory.appendingPathComponent("env.txt") }
    var requestDump: URL { directory.appendingPathComponent("request.json") }

    /// Writes a stub that records what it was given and replies with `body`.
    init(replying body: String, exitCode: Int = 0, sleepSeconds: Double = 0) throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("MemoryWorkerStub-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        executable = directory.appendingPathComponent("memoryworker")
        let script = """
        #!/bin/bash
        cat > '\(requestDump.path)'
        env > '\(envDump.path)'
        \(sleepSeconds > 0 ? "sleep \(sleepSeconds)" : "")
        cat <<'REPLY'
        \(body)
        REPLY
        exit \(exitCode)
        """
        try script.write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
    }

    func cleanup() { try? FileManager.default.removeItem(at: directory) }
}

private func temporaryStore() -> URL {
    FileManager.default.temporaryDirectory
        .appendingPathComponent("MemoryStore-\(UUID().uuidString)", isDirectory: true)
        .appendingPathComponent("Library/Application Support/WeChatCompanion/memory.sqlite")
}

private let successReply = """
{"ok": true, "op": "sync", "state": "synced", "source": "visual",
 "counts": {"conversations_seen": 2, "messages_seen": 13, "messages_inserted": 3,
            "messages_updated": 10, "duplicates_detected": 0},
 "freshness": {"generated_at": 1700000500.0, "participating_sources": ["visual"],
   "sources": {"visual": {"source": "visual", "runs_total": 1,
     "last_attempted_at": 1700000000.0, "last_attempt_state": "succeeded",
     "last_attempt_failure_state": null, "last_succeeded_at": 1700000000.0,
     "observed_through": 1699999880.0, "complete_through": 1699999880.0,
     "latest_message_at": 1699998920.0, "latest_message_timestamp_kind": "first_observed",
     "stored_messages": 13}}}}
"""



private let dailySummaryReply = """
{"ok": true, "op": "summary_input", "state": "ready", "source": "archive",
 "window": {"start": 1700000000.0, "end": 1700003600.0},
 "counts": {"returned_messages": 1, "returned_conversations": 1,
            "returned_senders": 1, "text_truncated": 0},
 "truncated": false,
 "coverage": {"status": "complete", "trustworthy_empty": true,
              "required_sources": ["archive"], "supplemental_sources": [],
              "complete_sources": ["archive"], "per_source": {}, "caveats": []},
 "freshness": {"generated_at": 1700004000.0, "participating_sources": ["archive"],
   "sources": {"archive": {"source": "archive", "runs_total": 1,
     "last_attempted_at": 1700000000.0, "last_attempt_state": "succeeded",
     "last_attempt_failure_state": null, "last_succeeded_at": 1700000000.0,
     "observed_through": 1700003000.0, "complete_through": 1700003000.0,
     "latest_message_at": 1700002000.0, "latest_message_timestamp_kind": "source_created",
     "stored_messages": 1}}},
 "conversations": [{"index": 0, "label": "Imported archive export",
                    "message_count": 1, "sender_count": 1,
                    "first_at": 1700001200.0, "last_at": 1700001200.0}],
 "senders": [{"sender": "A", "count": 1}],
 "messages": [{"ordinal": 0, "conversation_index": 0, "source": "archive",
               "timestamp": 1700001200.0, "timestamp_kind": "source_created",
               "sender": "A", "kind": "text", "text": "hello",
               "text_truncated": false}]}
"""



private let followUpReply = """
{"ok": true, "op": "reminder_candidates", "state": "ready", "source": "archive",
 "window": {"start": 1700000000.0, "end": 1700003600.0},
 "counts": {"scanned_messages": 4, "returned_candidates": 1,
            "returned_conversations": 1, "text_truncated": 0},
 "truncated": false,
 "coverage": {"status": "partial", "trustworthy_empty": false,
              "required_sources": ["archive"], "supplemental_sources": [],
              "complete_sources": [], "per_source": {}, "caveats": ["archive:partial"]},
 "freshness": {"generated_at": 1700004000.0, "participating_sources": ["archive"],
   "sources": {"archive": {"source": "archive", "runs_total": 1,
     "last_attempted_at": 1700000000.0, "last_attempt_state": "succeeded",
     "last_attempt_failure_state": null, "last_succeeded_at": 1700000000.0,
     "observed_through": 1700003000.0, "complete_through": null,
     "latest_message_at": 1700002000.0, "latest_message_timestamp_kind": "source_created",
     "stored_messages": 4}}},
 "conversations": [{"index": 0, "label": "Imported archive export"}],
 "candidates": [{"ordinal": 0, "conversation_index": 0, "source": "archive",
                 "timestamp": 1700001200.0, "timestamp_kind": "source_created",
                 "sender": "A", "text": "麻烦明天确认一下报价", "text_truncated": false,
                 "reasons": ["explicit_request", "explicit_follow_up", "time_reference"],
                 "archive_evidence": {"import_id": 1, "sequence": 0}}]}
"""


private let answerEvidenceReply = """
{"ok": true, "op": "answer_evidence", "state": "ready", "source": "archive",
 "window": {"start": 1700000000.0, "end": 1700003600.0},
 "query_scope": {"kind": "recent", "conversation_canonical_id": null,
                 "window": [1700000000.0, 1700003600.0], "limit": 200,
                 "order": "oldest", "text": null, "sender": null, "anchor": null,
                 "policy": {"required_sources": ["archive"], "supplemental_sources": []}},
 "counts": {"scanned_messages": 2, "returned_evidence": 2,
            "excluded_unanchored": 0, "text_truncated": 0},
 "truncated": false,
 "coverage": {"status": "partial", "trustworthy_empty": false,
              "required_sources": ["archive"], "supplemental_sources": [],
              "complete_sources": [], "per_source": {}, "caveats": ["archive:partial"]},
 "freshness": {"generated_at": 1700004000.0, "participating_sources": ["archive"],
   "sources": {"archive": {"source": "archive", "runs_total": 1,
     "last_attempted_at": 1700000000.0, "last_attempt_state": "succeeded",
     "last_attempt_failure_state": null, "last_succeeded_at": 1700000000.0,
     "observed_through": 1700003000.0, "complete_through": null,
     "latest_message_at": 1700002000.0, "latest_message_timestamp_kind": "source_created",
     "stored_messages": 2}}},
 "evidence": [
   {"source": "archive", "canonical_message_id": "msg-a",
    "canonical_conversation_id": "conv-a", "timestamp": 1700001200.0,
    "timestamp_kind": "source_created", "sender": "A", "text": "同一句话",
    "text_truncated": false, "archive_evidence": {"import_id": 1, "sequence": 0}},
   {"source": "archive", "canonical_message_id": "msg-b",
    "canonical_conversation_id": "conv-a", "timestamp": 1700001200.0,
    "timestamp_kind": "source_created", "sender": "A", "text": "同一句话",
    "text_truncated": false, "archive_evidence": {"import_id": 1, "sequence": 1}}]}
"""

private let archiveConversationsReply = """
{"ok": true, "op": "archive_conversations", "state": "ready",
 "counts": {"returned_conversations": 2},
 "conversations": [
   {"canonical_conversation_id": "conv:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "source": "archive",
    "label": "Imported Archive snapshot", "first_seen_at": 100.0, "last_seen_at": 100.0},
   {"canonical_conversation_id": "conv:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "source": "archive",
    "label": "Imported Archive snapshot", "first_seen_at": 300.0, "last_seen_at": 300.0}
 ]}
"""


private let emptyAnswerEvidenceReply = """
{"ok": true, "op": "answer_evidence", "state": "ready", "source": "archive",
 "window": {"start": 1700000000.0, "end": 1700003600.0},
 "query_scope": {"kind": "recent", "conversation_canonical_id": null,
                 "window": [1700000000.0, 1700003600.0], "limit": 200,
                 "order": "oldest", "text": null, "sender": null, "anchor": null,
                 "policy": {"required_sources": ["archive"], "supplemental_sources": []}},
 "counts": {"scanned_messages": 0, "returned_evidence": 0,
            "excluded_unanchored": 0, "text_truncated": 0},
 "truncated": false,
 "coverage": {"status": "complete", "trustworthy_empty": true,
              "required_sources": ["archive"], "supplemental_sources": [],
              "complete_sources": ["archive"], "per_source": {}, "caveats": []},
 "freshness": {"generated_at": 1700004000.0, "participating_sources": ["archive"],
   "sources": {"archive": {"source": "archive", "runs_total": 1,
     "last_attempted_at": 1700000000.0, "last_attempt_state": "succeeded",
     "last_attempt_failure_state": null, "last_succeeded_at": 1700000000.0,
     "observed_through": 1700003000.0, "complete_through": 1700003000.0,
     "latest_message_at": 1700002000.0, "latest_message_timestamp_kind": "source_created",
     "stored_messages": 0}}},
 "evidence": []}
"""

struct PackagedMemorySyncRunnerTests {
    // MARK: - Resolution

    // MARK: - The M2.2d incident class

    @Test @MainActor
    func aTestHostNeverPairsARealWorkerWithTheRealStore() {
        // The regression test for M2.2d: this very process is a test host
        // whose bundle contains a real packaged worker whenever the worker has
        // been built. Resolving it here would point at the user's own store.
        #expect(PackagedMemorySyncRunner.isUnderTestHost)
        #expect(PackagedMemorySyncRunner.bundled() == nil)
        #expect(AppModel.defaultMemorySyncRunner() is UnavailableMemorySyncRunner)
        #expect(AppModel.defaultDailySummaryRunner() is UnavailableDailySummaryRunner)
        #expect(AppModel.defaultFollowUpRunner() is UnavailableFollowUpRunner)
        #expect(AppModel.defaultReminderStore() is VolatileReminderStore)
        // Proven without reading the user's store: only its absence-or-not is
        // observed, and nothing here opens or creates it.
        #expect(!FileManager.default.fileExists(
            atPath: MemoryStoreLocation.canonical.path + ".test-marker"))
    }

    @Test
    func theGuardRefusesEvenWhenAWorkerIsPresent() throws {
        // A bundle that definitely contains a worker still yields no runner,
        // so the refusal is the test host, not a missing file.
        let fake = FileManager.default.temporaryDirectory
            .appendingPathComponent("FakeBundle-\(UUID().uuidString).app", isDirectory: true)
        let helper = fake.appendingPathComponent("Contents/\(PackagedMemorySyncRunner.bundleSubpath)")
        try FileManager.default.createDirectory(
            at: helper.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "#!/bin/bash\nexit 0\n".write(to: helper, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: helper.path)
        defer { try? FileManager.default.removeItem(at: fake) }
        #expect(FileManager.default.isExecutableFile(atPath: helper.path))
        #expect(PackagedMemorySyncRunner.bundled(in: Bundle(path: fake.path) ?? .main) == nil)
    }

    @Test
    func noWorkerInTheBundleMeansNoPackagedRunner() {
        let empty = FileManager.default.temporaryDirectory
            .appendingPathComponent("EmptyBundle-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: empty, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: empty) }
        #expect(PackagedMemorySyncRunner.bundled(in: Bundle(path: empty.path) ?? .main) == nil
                || Bundle(path: empty.path) == nil)
    }

    @Test
    func theWorkerIsResolvedInsideTheBundleAndNeverFromPath() {
        #expect(PackagedMemorySyncRunner.bundleSubpath
                == "Helpers/MemoryWorker.app/Contents/MacOS/MemoryWorker")
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent().deletingLastPathComponent()
                .appendingPathComponent("WeChatCompanion/Ingestion/PackagedMemorySyncRunner.swift"),
            encoding: .utf8)
        // No shell, and no lookup that a user's PATH could answer.
        #expect(!source.contains("/bin/sh"))
        #expect(!source.contains("/usr/bin/env"))
        #expect(!source.contains("launchPath"))
        #expect(source.contains("executableURL = workerURL"))
    }

    // MARK: - Success

    @Test
    func aSuccessfulSyncMapsCountsAndFreshness() async throws {
        let stub = try StubWorker(replying: successReply)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable, storeURL: store,
            messageStoreURL: store.deletingLastPathComponent().appendingPathComponent("messages.sqlite"))

        let outcome = await runner.sync(source: .visual)
        guard case .succeeded(let counts, let freshness) = outcome else {
            Issue.record("expected success, got \(outcome)"); return
        }
        #expect(counts == MemorySyncCounts(conversationsSeen: 2, messagesSeen: 13,
                                           messagesInserted: 3, messagesUpdated: 10))
        #expect(freshness?.lastSuccessfulSync == Date(timeIntervalSince1970: 1_700_000_000))
        #expect(freshness?.observedThrough == Date(timeIntervalSince1970: 1_699_999_880))
        #expect(freshness?.latestMessageAt == Date(timeIntervalSince1970: 1_699_998_920))
        #expect(freshness?.coverageSummary == "complete")
        #expect(freshness?.lastRunState == "succeeded")
        // The three timestamps stay three different facts across the boundary.
        #expect(freshness!.latestMessageAt! < freshness!.observedThrough!)
        #expect(freshness!.observedThrough! < freshness!.lastSuccessfulSync!)
    }

    @Test
    func theStoreDirectoryIsCreatedOwnerOnlyBeforeTheWorkerRuns() async throws {
        let stub = try StubWorker(replying: successReply)
        defer { stub.cleanup() }
        let store = temporaryStore()
        #expect(!FileManager.default.fileExists(atPath: store.deletingLastPathComponent().path))
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: store,
                                              messageStoreURL: store)
        _ = await runner.sync(source: .visual)
        let attributes = try FileManager.default.attributesOfItem(
            atPath: store.deletingLastPathComponent().path)
        #expect((attributes[.posixPermissions] as? NSNumber)?.int16Value == 0o700)
    }

    @Test
    func therequestNamesTheStoreTheSourceAndNothingElse() async throws {
        let stub = try StubWorker(replying: successReply)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let messages = store.deletingLastPathComponent().appendingPathComponent("messages.sqlite")
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: store,
                                              messageStoreURL: messages)
        _ = await runner.sync(source: .visual)
        let sent = try JSONSerialization.jsonObject(
            with: Data(contentsOf: stub.requestDump)) as! [String: Any]
        #expect(sent["op"] as? String == "sync")
        #expect(sent["store_path"] as? String == store.path)
        #expect(sent["message_store_path"] as? String == messages.path)
        // The source is named, so the worker refuses a selection it cannot
        // honour rather than quietly reading a different one.
        #expect(sent["message_source"] as? String == "visual")
    }

    @Test
    func archiveSelectionIsSentExplicitlyToTheWorker() async throws {
        let stub = try StubWorker(replying: successReply)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let messages = store.deletingLastPathComponent().appendingPathComponent("messages.sqlite")
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: store,
            messageStoreURL: messages
        )

        _ = await runner.sync(source: .archive)

        let sent = try JSONSerialization.jsonObject(
            with: Data(contentsOf: stub.requestDump)
        ) as! [String: Any]
        #expect(sent["message_source"] as? String == "archive")
        #expect(sent["message_store_path"] as? String == messages.path)
    }



    @Test
    func dailySummaryRequestPinsScopeAndMapsStructuredEvidence() async throws {
        let stub = try StubWorker(replying: dailySummaryReply)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let messages = store.deletingLastPathComponent().appendingPathComponent("messages.sqlite")
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: store,
            messageStoreURL: messages
        )

        let outcome = await runner.prepare(
            source: .archive,
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200
        )

        guard case .ready(let snapshot) = outcome else {
            Issue.record("expected Daily Summary success, got \(outcome)")
            return
        }
        #expect(snapshot.source == .archive)
        #expect(snapshot.coverage.status == "complete")
        #expect(snapshot.coverage.trustworthyEmpty)
        #expect(snapshot.returnedMessages == 1)
        #expect(snapshot.conversations.first?.label == "Imported archive export")
        #expect(snapshot.messages.first?.timestampKind == "source_created")
        #expect(snapshot.messages.first?.text == "hello")
        #expect(snapshot.freshness?.source == .archive)

        let sent = try JSONSerialization.jsonObject(
            with: Data(contentsOf: stub.requestDump)
        ) as! [String: Any]
        #expect(sent["op"] as? String == "summary_input")
        #expect(sent["message_source"] as? String == "archive")
        #expect(sent["start"] as? Double == 1_700_000_000)
        #expect(sent["end"] as? Double == 1_700_003_600)
        #expect(sent["message_limit"] as? Int == 200)
        #expect(sent["store_path"] as? String == store.path)
        #expect(sent["message_store_path"] as? String == messages.path)
    }

    @Test
    func dailySummaryFailsClosedWhenWorkerReturnsADifferentSource() async throws {
        let mismatched = dailySummaryReply.replacingOccurrences(
            of: #""source": "archive""#,
            with: #""source": "visual""#
        )
        let stub = try StubWorker(replying: mismatched)
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: temporaryStore(),
            messageStoreURL: temporaryStore()
        )

        let outcome = await runner.prepare(
            source: .archive,
            start: Date(timeIntervalSince1970: 1),
            end: Date(timeIntervalSince1970: 2),
            messageLimit: 200
        )

        #expect(outcome == .failed(.workerFailed(state: "worker_response_malformed")))
    }



    @Test
    func dailySummaryFailsClosedWhenWorkerReturnsADifferentWindow() async throws {
        let mismatched = dailySummaryReply.replacingOccurrences(
            of: #""end": 1700003600.0"#,
            with: #""end": 1700003601.0"#
        )
        let stub = try StubWorker(replying: mismatched)
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: temporaryStore(),
            messageStoreURL: temporaryStore()
        )

        let outcome = await runner.prepare(
            source: .archive,
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200
        )

        #expect(outcome == .failed(.workerFailed(state: "worker_response_malformed")))
    }

    @Test
    func dailySummaryMapsMissingMemoryToAnExplicitReadFailure() async throws {
        let stub = try StubWorker(
            replying: #"{"ok": false, "op": "summary_input", "state": "memory_store_missing", "detail": "x"}"#,
            exitCode: 1
        )
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: temporaryStore(),
            messageStoreURL: temporaryStore()
        )

        let outcome = await runner.prepare(
            source: .visual,
            start: Date(timeIntervalSince1970: 1),
            end: Date(timeIntervalSince1970: 2),
            messageLimit: 200
        )

        #expect(outcome == .failed(.memoryUnavailable(state: "memory_store_missing")))
    }



    @Test
    func followUpRequestPinsScopeAndMapsCandidates() async throws {
        let stub = try StubWorker(replying: followUpReply)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let messages = store.deletingLastPathComponent().appendingPathComponent("messages.sqlite")
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: store,
            messageStoreURL: messages
        )

        let outcome = await runner.scan(
            source: .archive,
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200,
            candidateLimit: 50
        )

        guard case .ready(let snapshot) = outcome else {
            Issue.record("expected Follow-Up success, got \(outcome)")
            return
        }
        #expect(snapshot.source == .archive)
        #expect(snapshot.coverage.status == "partial")
        #expect(snapshot.coverage.trustworthyEmpty == false)
        #expect(snapshot.scannedMessages == 4)
        #expect(snapshot.returnedCandidates == 1)
        #expect(snapshot.conversations.first?.label == "Imported archive export")
        #expect(snapshot.candidates.first?.timestampKind == "source_created")
        #expect(snapshot.candidates.first?.reasons == [
            "explicit_request", "explicit_follow_up", "time_reference"
        ])
        #expect(snapshot.candidates.first?.archiveEvidence
                == ArchiveEvidenceAnchor(importID: 1, sequence: 0))
        #expect(snapshot.freshness?.source == .archive)

        let sent = try JSONSerialization.jsonObject(
            with: Data(contentsOf: stub.requestDump)
        ) as! [String: Any]
        #expect(sent["op"] as? String == "reminder_candidates")
        #expect(sent["message_source"] as? String == "archive")
        #expect(sent["start"] as? Double == 1_700_000_000)
        #expect(sent["end"] as? Double == 1_700_003_600)
        #expect(sent["message_limit"] as? Int == 200)
        #expect(sent["candidate_limit"] as? Int == 50)
        #expect(sent["store_path"] as? String == store.path)
        #expect(sent["message_store_path"] as? String == messages.path)
    }

    @Test
    func followUpAnchorIsRequiredForArchiveAndRejectedWhenMalformedOrWrongSource()
    async throws {
        // The anchor is the only route from a saved follow-up back to exact
        // evidence, so a wrong, defaulted, or partially-present one must fail
        // the whole reply rather than produce a candidate that cannot be
        // revealed truthfully.
        for body in [
            followUpReply.replacingOccurrences(
                of: #""archive_evidence": {"import_id": 1, "sequence": 0}"#,
                with: #""archive_evidence": {"sequence": 0}"#
            ),
            followUpReply.replacingOccurrences(
                of: #""archive_evidence": {"import_id": 1, "sequence": 0}"#,
                with: #""archive_evidence": {"import_id": 0, "sequence": 0}"#
            ),
            followUpReply.replacingOccurrences(
                of: #""archive_evidence": {"import_id": 1, "sequence": 0}"#,
                with: #""archive_evidence": {"import_id": 1, "sequence": -1}"#
            ),
            // Absent entirely: Archive evidence is only ever named by the
            // worker's own identity, so a missing anchor is malformed.
            followUpReply.replacingOccurrences(
                of: ",\n                 \"archive_evidence\": {\"import_id\": 1, \"sequence\": 0}",
                with: ""
            ),
        ] {
            let stub = try StubWorker(replying: body)
            defer { stub.cleanup() }
            let store = temporaryStore()
            let runner = PackagedMemorySyncRunner(
                workerURL: stub.executable,
                storeURL: store,
                messageStoreURL: store.deletingLastPathComponent()
                    .appendingPathComponent("messages.sqlite")
            )
            let outcome = await runner.scan(
                source: .archive,
                start: Date(timeIntervalSince1970: 1_700_000_000),
                end: Date(timeIntervalSince1970: 1_700_003_600),
                messageLimit: 200,
                candidateLimit: 50
            )
            #expect(outcome == .failed(.workerFailed(state: "worker_response_malformed")))
        }
    }

    @Test
    func followUpRejectsAnArchiveAnchorOnANonArchiveCandidate() async throws {
        // An anchor is only ever an Archive fact. Carrying one on a Visual
        // candidate would let a Visual follow-up reveal an unrelated Archive
        // row, so the source check is the same shape as the existing
        // candidate-source check above it.
        let mismatched = followUpReply.replacingOccurrences(
            of: #""source": "archive""#, with: #""source": "visual""#
        )
        let stub = try StubWorker(replying: mismatched)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: store,
            messageStoreURL: store.deletingLastPathComponent()
                .appendingPathComponent("messages.sqlite")
        )

        let outcome = await runner.scan(
            source: .visual,
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200,
            candidateLimit: 50
        )

        #expect(outcome == .failed(.workerFailed(state: "worker_response_malformed")))
    }

    @Test
    func followUpFromAVisualSourceCarriesNoArchiveAnchor() async throws {
        let visual = followUpReply
            .replacingOccurrences(of: #""source": "archive""#, with: #""source": "visual""#)
            .replacingOccurrences(
                of: #""archive_evidence": {"import_id": 1, "sequence": 0}"#, with: ""
            )
        let stub = try StubWorker(replying: visual)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: store,
            messageStoreURL: store.deletingLastPathComponent()
                .appendingPathComponent("messages.sqlite")
        )

        let outcome = await runner.scan(
            source: .visual,
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200,
            candidateLimit: 50
        )

        guard case .ready(let snapshot) = outcome else {
            Issue.record("expected Follow-Up success, got \(outcome)")
            return
        }
        #expect(snapshot.candidates.first?.archiveEvidence == nil)
    }

    @Test
    func followUpAnchorOnANonArchiveCandidateIsRejected() async throws {
        // A Visual candidate has no Archive row, so an anchor on it would be a
        // provenance claim the worker cannot have made.
        let visual = followUpReply.replacingOccurrences(
            of: #""source": "archive"#, with: #""source": "visual"#
        )
        let stub = try StubWorker(replying: visual)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: store,
            messageStoreURL: store.deletingLastPathComponent()
                .appendingPathComponent("messages.sqlite")
        )

        let outcome = await runner.scan(
            source: .visual,
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200,
            candidateLimit: 50
        )

        #expect(outcome == .failed(.workerFailed(state: "worker_response_malformed")))
    }

    @Test
    func followUpFailsClosedWhenWorkerReturnsADifferentSourceOrWindow() async throws {
        for body in [
            followUpReply.replacingOccurrences(
                of: #""source": "archive""#,
                with: #""source": "visual""#
            ),
            followUpReply.replacingOccurrences(
                of: #""end": 1700003600.0"#,
                with: #""end": 1700003601.0"#
            ),
        ] {
            let stub = try StubWorker(replying: body)
            defer { stub.cleanup() }
            let runner = PackagedMemorySyncRunner(
                workerURL: stub.executable,
                storeURL: temporaryStore(),
                messageStoreURL: temporaryStore()
            )

            let outcome = await runner.scan(
                source: .archive,
                start: Date(timeIntervalSince1970: 1_700_000_000),
                end: Date(timeIntervalSince1970: 1_700_003_600),
                messageLimit: 200,
                candidateLimit: 50
            )
            #expect(outcome == .failed(.workerFailed(state: "worker_response_malformed")))
        }
    }

    @Test
    func followUpMapsMissingMemoryToAnExplicitReadFailure() async throws {
        let stub = try StubWorker(
            replying: #"{"ok": false, "op": "reminder_candidates", "state": "memory_store_missing", "detail": "x"}"#,
            exitCode: 1
        )
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: temporaryStore(),
            messageStoreURL: temporaryStore()
        )

        let outcome = await runner.scan(
            source: .visual,
            start: Date(timeIntervalSince1970: 1),
            end: Date(timeIntervalSince1970: 2),
            messageLimit: 200,
            candidateLimit: 50
        )
        #expect(outcome == .failed(.memoryUnavailable(state: "memory_store_missing")))
    }

    @Test
    func theChildEnvironmentIsBuiltFromScratch() async throws {
        let stub = try StubWorker(replying: successReply)
        defer { stub.cleanup() }
        setenv("WECHAT_COMPANION_MESSAGE_SOURCE", "database", 1)
        defer { unsetenv("WECHAT_COMPANION_MESSAGE_SOURCE") }
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: temporaryStore(),
                                              messageStoreURL: temporaryStore())
        _ = await runner.sync(source: .visual)
        let dumped = try String(contentsOf: stub.envDump, encoding: .utf8)
        let names = Set(dumped.split(separator: "\n").compactMap { $0.split(separator: "=").first.map(String.init) })
        // A parent variable must not be able to select a source behind the app.
        #expect(!names.contains("WECHAT_COMPANION_MESSAGE_SOURCE"))
        #expect(names.isSubset(of: ["HOME", "PATH", "LANG", "_", "SHLVL", "PWD"]))
    }

    // MARK: - Answer evidence window

    @Test
    func archiveConversationDiscoveryKeepsSameLabelsAsDistinctOpaqueRows() async throws {
        let stub = try StubWorker(replying: archiveConversationsReply)
        defer { stub.cleanup() }
        let outcome = await evidenceRunner(stub).archiveConversations()
        guard case .ready(let snapshots) = outcome else {
            Issue.record("expected Archive snapshot discovery success, got (outcome)")
            return
        }
        #expect(snapshots.map(\.id) == [
            "conv:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "conv:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ])
        #expect(Set(snapshots.map(\.label)).count == 1)
        #expect(snapshots.map(\.rangeLabel).count == 2)
        let sent = try JSONSerialization.jsonObject(
            with: Data(contentsOf: stub.requestDump)
        ) as! [String: Any]
        #expect(sent.keys.sorted() == ["op", "store_path"])
    }

    @Test
    func archiveConversationDiscoveryRejectsMalformedEnvelopeAndRows() async throws {
        let malformed = [
            archiveConversationsReply.replacingOccurrences(
                of: "conv:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                with: "wrong:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
            archiveConversationsReply.replacingOccurrences(
                of: "conv:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                with: "conv:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            ),
            archiveConversationsReply.replacingOccurrences(
                of: "conv:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                with: "conv:bad"
            ),
            archiveConversationsReply.replacingOccurrences(
                of: "\"op\": \"archive_conversations\", ", with: ""
            ),
            archiveConversationsReply.replacingOccurrences(
                of: "\"op\": \"archive_conversations\"",
                with: "\"op\": \"answer_evidence\""
            ),
            archiveConversationsReply.replacingOccurrences(
                of: "\"state\": \"ready\"",
                with: "\"state_missing\": \"ready\""
            ),
            archiveConversationsReply.replacingOccurrences(
                of: "\"state\": \"ready\"",
                with: "\"state\": \"partial\""
            ),
        ]

        for (index, body) in malformed.enumerated() {
            let stub = try StubWorker(replying: body)
            defer { stub.cleanup() }
            let outcome = await evidenceRunner(stub).archiveConversations()
            #expect(
                outcome == .failed(.workerFailed(state: "worker_response_malformed")),
                "malformed discovery case \(index)"
            )
        }
    }

    private func evidenceRunner(_ stub: StubWorker) -> PackagedMemorySyncRunner {
        let store = temporaryStore()
        return PackagedMemorySyncRunner(
            workerURL: stub.executable,
            storeURL: store,
            messageStoreURL: store.deletingLastPathComponent()
                .appendingPathComponent("messages.sqlite")
        )
    }

    @Test
    func answerEvidencePinsArchiveAndDecodesEveryRowWithItsOwnAnchor() async throws {
        let stub = try StubWorker(replying: answerEvidenceReply)
        defer { stub.cleanup() }
        let runner = evidenceRunner(stub)

        let outcome = await runner.answerEvidence(
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200
        )

        guard case .ready(let snapshot) = outcome else {
            Issue.record("expected evidence success, got \(outcome)")
            return
        }
        #expect(snapshot.returnedEvidence == 2)
        #expect(snapshot.excludedUnanchored == 0)
        #expect(snapshot.truncated == false)
        #expect(snapshot.coverage.status == "partial")
        #expect(snapshot.coverage.trustworthyEmpty == false)
        #expect(snapshot.coverage.caveats == ["archive:partial"])
        #expect(snapshot.freshness?.source == .archive)
        // Two rows that display identically stay two rows, and only the
        // canonical anchor tells them apart.
        #expect(snapshot.rows.map(\.archiveEvidence) == [
            ArchiveEvidenceAnchor(importID: 1, sequence: 0),
            ArchiveEvidenceAnchor(importID: 1, sequence: 1),
        ])
        #expect(Set(snapshot.rows.map(\.canonicalMessageID)).count == 2)
        #expect(snapshot.rows.first?.timestampKind == "source_created")

        let sent = try JSONSerialization.jsonObject(
            with: Data(contentsOf: stub.requestDump)
        ) as! [String: Any]
        #expect(sent["op"] as? String == "answer_evidence")
        #expect(sent["message_source"] as? String == "archive")
        #expect(sent["start"] as? Double == 1_700_000_000)
        #expect(sent["end"] as? Double == 1_700_003_600)
        #expect(sent["message_limit"] as? Int == 200)
        #expect(sent["store_path"] as? String == runner.storeURL.path)
        #expect(sent["message_store_path"] as? String == runner.messageStoreURL.path)
        // The window is asked for by shape: no wording ever crosses the wire.
        #expect(sent.keys.sorted() == [
            "end", "message_limit", "message_source", "message_store_path",
            "op", "start", "store_path",
        ])
    }

    @Test
    func scopedAnswerEvidencePassesOpaqueConversationIDAndRequiresEcho() async throws {
        let id = "conv:" + String(repeating: "a", count: 32)
        let reply = answerEvidenceReply.replacingOccurrences(
            of: "\"conversation_canonical_id\": null",
            with: "\"conversation_canonical_id\": \"\(id)\""
        )
        let stub = try StubWorker(replying: reply)
        defer { stub.cleanup() }

        let outcome = await evidenceRunner(stub).answerEvidenceScoped(
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200,
            conversationCanonicalID: id
        )
        guard case .ready = outcome else {
            Issue.record("expected scoped evidence success, got (outcome)")
            return
        }
        let sent = try JSONSerialization.jsonObject(
            with: Data(contentsOf: stub.requestDump)
        ) as! [String: Any]
        #expect(sent["conversation_canonical_id"] as? String == id)
    }

    @Test
    func answerEvidenceFailsClosedOnAnythingItCannotNameExactly() async throws {
        // Every case below would otherwise produce a row that looks citeable
        // but cannot be revealed back to an exact Archive record.
        let anchor = #""archive_evidence": {"import_id": 1, "sequence": 0}"#
        for body in [
            // The source the app did not ask for.
            answerEvidenceReply.replacingOccurrences(
                of: #""source": "archive""#, with: #""source": "visual""#
            ),
            // A window the app did not ask for.
            answerEvidenceReply.replacingOccurrences(
                of: #""end": 1700003600.0"#, with: #""end": 1700003601.0"#
            ),
            // Absent, partial, non-positive, or negative anchor.
            answerEvidenceReply.replacingOccurrences(of: anchor, with: "{}"),
            answerEvidenceReply.replacingOccurrences(
                of: anchor, with: #""archive_evidence": {"sequence": 0}"#
            ),
            answerEvidenceReply.replacingOccurrences(
                of: anchor, with: #""archive_evidence": {"import_id": 0, "sequence": 0}"#
            ),
            answerEvidenceReply.replacingOccurrences(
                of: anchor, with: #""archive_evidence": {"import_id": 1, "sequence": -1}"#
            ),
            // A row whose displayed fields are not decodable.
            answerEvidenceReply.replacingOccurrences(
                of: #""text_truncated": false"#, with: #""text_truncated": "no""#
            ),
            // A count that disagrees with the rows actually sent.
            answerEvidenceReply.replacingOccurrences(
                of: #""returned_evidence": 2"#, with: #""returned_evidence": 3"#
            ),
            // A count the envelope does not state, or states as a word.
            answerEvidenceReply.replacingOccurrences(
                of: #""excluded_unanchored": 0"#, with: #""excluded_unanchored": "none""#
            ),
            answerEvidenceReply.replacingOccurrences(
                of: #""text_truncated": 0"#, with: #""truncated": false"#
            ),
            // A truncation flag that hides a row the window actually lost.
            answerEvidenceReply.replacingOccurrences(
                of: #""scanned_messages": 2"#, with: #""scanned_messages": 4"#
            ),
            // A scope that describes a different window, or no window at all.
            answerEvidenceReply.replacingOccurrences(
                of: #""window": [1700000000.0, 1700003600.0]"#,
                with: #""window": [1699990000.0, 1700003600.0]"#
            ),
            // A scope that claims a bound it could not have read under.
            answerEvidenceReply.replacingOccurrences(
                of: #""limit": 200"#, with: #""limit": 0"#
            ),
            // Not an evidence envelope at all.
            #"{"ok": true, "op": "answer_evidence", "state": "ready"}"#,
        ] {
            let stub = try StubWorker(replying: body)
            defer { stub.cleanup() }
            let outcome = await evidenceRunner(stub).answerEvidence(
                start: Date(timeIntervalSince1970: 1_700_000_000),
                end: Date(timeIntervalSince1970: 1_700_003_600),
                messageLimit: 200
            )
            #expect(
                outcome == .failed(.workerFailed(state: "worker_response_malformed")),
                Comment(rawValue: body))
        }
    }

    @Test
    func answerEvidenceMapsAMissingOrCorruptMemoryToAnExplicitReadFailure() async throws {
        for state in ["memory_store_missing", "archive_evidence_malformed"] {
            let stub = try StubWorker(
                replying: #"{"ok": false, "op": "answer_evidence", "state": "\#(state)", "detail": "x"}"#,
                exitCode: 1
            )
            defer { stub.cleanup() }
            let outcome = await evidenceRunner(stub).answerEvidence(
                start: Date(timeIntervalSince1970: 1),
                end: Date(timeIntervalSince1970: 2),
                messageLimit: 200
            )
            #expect(outcome == .failed(.memoryUnavailable(state: state)))
        }
    }

    @Test
    func answerEvidenceWithholdsWhenConsentIsWithheld() async throws {
        let stub = try StubWorker(
            replying: #"{"ok": false, "op": "answer_evidence", "state": "consent_withheld", "detail": "x"}"#,
            exitCode: 1
        )
        defer { stub.cleanup() }
        let outcome = await evidenceRunner(stub).answerEvidence(
            start: Date(timeIntervalSince1970: 1),
            end: Date(timeIntervalSince1970: 2),
            messageLimit: 200
        )
        #expect(outcome == .failed(.consentWithheld))
    }

    @Test
    func anEmptyButValidWindowIsAnEmptySuccess() async throws {
        let stub = try StubWorker(replying: emptyAnswerEvidenceReply)
        defer { stub.cleanup() }
        let outcome = await evidenceRunner(stub).answerEvidence(
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            messageLimit: 200
        )
        guard case .ready(let snapshot) = outcome else {
            Issue.record("expected an empty success, got \(outcome)")
            return
        }
        #expect(snapshot.rows.isEmpty)
        #expect(snapshot.returnedEvidence == 0)
        #expect(snapshot.coverage.trustworthyEmpty == true)
    }

    // MARK: - Failure

    @Test
    func aConsentRefusalFromTheWorkerIsReportedAsConsent() async throws {
        let stub = try StubWorker(
            replying: #"{"ok": false, "op": "sync", "state": "consent_withheld", "detail": "x"}"#,
            exitCode: 1)
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: temporaryStore(),
                                              messageStoreURL: temporaryStore())
        #expect(await runner.sync(source: .visual) == .failed(.consentWithheld))
    }

    @Test
    func anUnavailableSelectedSourceIsNeverReportedAsPlainFailure() async throws {
        let stub = try StubWorker(
            replying: #"{"ok": false, "op": "sync", "state": "database:reader_not_configured", "detail": "x"}"#,
            exitCode: 1)
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: temporaryStore(),
                                              messageStoreURL: temporaryStore())
        let outcome = await runner.sync(source: .visual)
        #expect(outcome == .failed(.sourceUnavailable(state: "database:reader_not_configured")))
        if case .failed(let failure) = outcome {
            #expect(failure.message.contains("No other source was used"))
        }
    }

    @Test
    func anIngestionFailureIsDistinctFromIncompleteCoverage() async throws {
        let stub = try StubWorker(
            replying: #"{"ok": false, "op": "sync", "state": "record_malformed", "detail": "x"}"#,
            exitCode: 1)
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: temporaryStore(),
                                              messageStoreURL: temporaryStore())
        #expect(await runner.sync(source: .visual) == .failed(.ingestionFailed(state: "record_malformed")))
    }

    @Test
    func aMalformedReplyFailsClosed() async throws {
        let stub = try StubWorker(replying: "not json at all")
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: temporaryStore(),
                                              messageStoreURL: temporaryStore())
        #expect(await runner.sync(source: .visual) == .failed(.ingestionFailed(state: "worker_response_malformed")))
    }

    @Test
    func aWorkerThatDiesWithoutReplyingFailsClosed() async throws {
        let stub = try StubWorker(replying: "", exitCode: 3)
        defer { stub.cleanup() }
        let runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: temporaryStore(),
                                              messageStoreURL: temporaryStore())
        #expect(await runner.sync(source: .visual) == .failed(.ingestionFailed(state: "worker_response_malformed")))
    }

    @Test
    func aMissingWorkerIsNotRunnable() async throws {
        let runner = PackagedMemorySyncRunner(
            workerURL: URL(fileURLWithPath: "/nonexistent/memoryworker"),
            storeURL: temporaryStore(), messageStoreURL: temporaryStore())
        #expect(await runner.sync(source: .visual) == .failed(.ingestionFailed(state: "worker_not_runnable")))
    }

    @Test
    func aWedgedWorkerIsBounded() async throws {
        let stub = try StubWorker(replying: successReply, sleepSeconds: 5)
        defer { stub.cleanup() }
        var runner = PackagedMemorySyncRunner(workerURL: stub.executable, storeURL: temporaryStore(),
                                              messageStoreURL: temporaryStore())
        runner.timeout = 0.3
        let outcome = await runner.sync(source: .visual)
        #expect(outcome == .failed(.ingestionFailed(state: "worker_timed_out")))
    }

    /// The Agents kill-switch is scoped to `answer_evidence` alone.
    ///
    /// Asserting only that `answerEvidenceTimeout > answerTimeout` cannot catch
    /// this: the property stays correct even when it is handed to the wrong
    /// operation. These observe the real deadline each operation actually uses:
    /// every one runs against the same slow stub, with a base timeout that
    /// fires well before the stub answers. Anything bounded by the default is
    /// killed and reports `worker_timed_out`. Only `answerEvidence` is allowed
    /// the later safety ceiling, so it must still be waiting when the stub
    /// replies -- and therefore must not report a timeout at all.
    @Test
    func onlyAnswerEvidenceGetsTheLaterWorkerCeiling() async throws {
        // The stub answers after 1s. The default is 0.3s, so the default always
        // fires first. The Agents ceiling is `timeout + 90s`, so it never does.
        // The margin is deliberately not shrunk: shrinking it would make the
        // test pass on a wrong constant instead of on correct wiring.
        let stub = try StubWorker(replying: successReply, sleepSeconds: 1)
        defer { stub.cleanup() }
        let store = temporaryStore()
        let messages = store.deletingLastPathComponent().appendingPathComponent("messages.sqlite")
        var runner = PackagedMemorySyncRunner(
            workerURL: stub.executable, storeURL: store, messageStoreURL: messages
        )
        runner.timeout = 0.3

        let window = (
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600)
        )

        // The sealed consumers keep the default: each one is bounded by
        // `timeout` and none of them inherits the Agents margin.
        #expect(
            await runner.sync(source: .visual)
                == .failed(.ingestionFailed(state: "worker_timed_out"))
        )
        #expect(
            await runner.prepare(source: .archive, start: window.start, end: window.end, messageLimit: 200)
                == .failed(.workerFailed(state: "worker_timed_out"))
        )
        #expect(
            await runner.scan(
                source: .archive, start: window.start, end: window.end, messageLimit: 200,
                candidateLimit: 50
            ) == .failed(.workerFailed(state: "worker_timed_out"))
        )
        // Outlasts the default: still waiting when the stub replies, so it can
        // only fail on its own validation, never on a timeout it outran.
        let evidence = await runner.answerEvidence(
            start: window.start, end: window.end, messageLimit: 200
        )
        if case .failed(.workerFailed(let state)) = evidence {
            #expect(state != "worker_timed_out")
        }
        #expect(runner.answerEvidenceTimeout > runner.timeout)
    }

    // MARK: - Freshness mapping

    @Test
    func coverageIsDerivedOnlyFromTheBoundariesThatExist() {
        func summary(_ entry: [String: Any]) -> MemoryFreshnessSummary? {
            PackagedMemorySyncRunner.summary(from: ["sources": ["visual": entry]], source: .visual)
        }
        #expect(summary(["last_succeeded_at": 2.0, "observed_through": 1.0, "complete_through": 1.0])?
            .coverageSummary == "complete")
        #expect(summary(["last_succeeded_at": 2.0, "observed_through": 1.0])?.coverageSummary == "partial")
        #expect(summary(["last_succeeded_at": 2.0])?.coverageSummary == "unavailable")
        #expect(summary([:])?.coverageSummary == "none")
        // A failed later attempt does not erase the last known-good boundary.
        let afterFailure = summary(["last_succeeded_at": 2.0, "observed_through": 1.0,
                                    "complete_through": 1.0, "last_attempt_state": "failed",
                                    "last_attempt_failure_state": "reader_unavailable"])
        #expect(afterFailure?.lastSuccessfulSync == Date(timeIntervalSince1970: 2))
        #expect(afterFailure?.lastRunState == "failed")
        #expect(afterFailure?.lastRunFailure == "reader_unavailable")
        #expect(afterFailure?.coverageSummary == "complete")
    }

    @Test
    func freshnessForAnUnknownSourceIsAbsentRatherThanInvented() {
        #expect(PackagedMemorySyncRunner.summary(from: ["sources": [:]], source: .visual) == nil)
        #expect(PackagedMemorySyncRunner.summary(from: nil, source: .visual) == nil)
    }

    // MARK: - Location

    @Test @MainActor
    func theCanonicalStoreIsInTheAppsOwnDirectoryAndIsNotAUserSetting() {
        #expect(MemoryStoreLocation.canonical.lastPathComponent == "memory.sqlite")
        #expect(MemoryStoreLocation.canonical.deletingLastPathComponent().lastPathComponent
                == "WeChatCompanion")
        #expect(MemoryStoreLocation.messageStore.deletingLastPathComponent()
                == MemoryStoreLocation.canonical.deletingLastPathComponent())
        #expect(MemoryStoreLocation.relativeDescription
                == "Library/Application Support/WeChatCompanion/memory.sqlite")
        // Nothing in the app persists a memory path as a preference.
        #expect(!AppModel.localPersistenceConsentKey.contains("memory"))
    }
}
