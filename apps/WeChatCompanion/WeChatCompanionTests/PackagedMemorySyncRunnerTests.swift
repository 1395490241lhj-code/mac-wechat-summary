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
