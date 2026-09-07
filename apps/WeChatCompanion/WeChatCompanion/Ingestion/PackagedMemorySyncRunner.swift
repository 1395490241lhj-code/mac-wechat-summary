import Foundation

/// Runs the memory sync through the worker bundled inside the app (M2.2d).
///
/// The memory layer is Python and this app ships no interpreter for a user to
/// install: the worker is frozen into a self-contained executable and copied
/// into the app bundle at build time. This runner starts it as a one-shot
/// child process, writes one JSON request, reads one JSON reply, and returns.
///
/// The narrow parts are deliberate:
///
/// * **Fixed path.** The executable is resolved relative to the app bundle and
///   nowhere else. There is no `PATH` lookup, so nothing a user has installed
///   can be run in its place, and no shell, so there is no command string to
///   get wrong.
/// * **Environment built from scratch.** `HOME` (so the worker can read the
///   app's own consent state), a fixed `PATH` for `defaults`, and a locale.
///   Nothing is inherited: the parent's variables cannot select a different
///   source or a different store behind the app's back.
/// * **Bounded.** The child is given a deadline and terminated if it passes
///   it, so a wedged worker cannot hold the action open.
/// * **Sanitized.** Failures are the worker's fixed state tokens or a fixed
///   local one. A path, a chat, or an interpreter traceback never becomes a
///   user-visible string.
/// One mutable bit shared with the watchdog queue.
private final class Expiry: @unchecked Sendable {
    var fired = false
}

struct PackagedMemorySyncRunner: MemorySyncRunning {
    /// Where the worker sits inside the bundle. It is a nested *bundle*, not a
    /// loose directory: codesign refuses to seal an app that contains an
    /// unsigned tree of plain files, and a helper .app carries its own seal.
    static let bundleSubpath = "Helpers/MemoryWorker.app/Contents/MacOS/MemoryWorker"

    let workerURL: URL
    let storeURL: URL
    let messageStoreURL: URL
    var timeout: TimeInterval = 120

    /// The runner for this build, or nil when no worker was bundled.
    ///
    /// Returning nil rather than a runner that always fails keeps the honest
    /// M2.2c behaviour available: a build without the worker still reports the
    /// packaging gap instead of pretending a sync was attempted.
    static func bundled(
        in bundle: Bundle = .main,
        store: URL = MemoryStoreLocation.canonical,
        messageStore: URL = MemoryStoreLocation.messageStore
    ) -> PackagedMemorySyncRunner? {
        let executable = URL(fileURLWithPath: bundle.bundlePath)
            .appendingPathComponent("Contents")
            .appendingPathComponent(bundleSubpath)
        guard FileManager.default.isExecutableFile(atPath: executable.path) else { return nil }
        return PackagedMemorySyncRunner(
            workerURL: executable, storeURL: store, messageStoreURL: messageStore
        )
    }

    // MARK: - MemorySyncRunning

    func sync(source: MemorySource) async -> MemorySyncOutcome {
        do {
            try MemoryStoreLocation.prepareDirectory(for: storeURL)
        } catch {
            return .failed(.ingestionFailed(state: "store_directory_unavailable"))
        }
        var request = baseRequest(op: "sync")
        // The app can only offer the store it fills itself. Naming the source
        // explicitly pins it: the worker refuses a selection it cannot honour
        // rather than reading a different one.
        request["message_source"] = source.rawValue
        switch invoke(request) {
        case .failure(let failure):
            return .failed(failure)
        case .success(let reply):
            guard reply["ok"] as? Bool == true else {
                return .failed(failureFrom(reply))
            }
            let counts = reply["counts"] as? [String: Any] ?? [:]
            return .succeeded(
                MemorySyncCounts(
                    conversationsSeen: counts["conversations_seen"] as? Int ?? 0,
                    messagesSeen: counts["messages_seen"] as? Int ?? 0,
                    messagesInserted: counts["messages_inserted"] as? Int ?? 0,
                    messagesUpdated: counts["messages_updated"] as? Int ?? 0
                ),
                Self.summary(from: reply["freshness"], source: source)
            )
        }
    }

    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? {
        guard case .success(let reply) = invoke(baseRequest(op: "status")),
              reply["ok"] as? Bool == true else { return nil }
        return Self.summary(from: reply["freshness"], source: source)
    }

    // MARK: - Protocol

    private func baseRequest(op: String) -> [String: Any] {
        ["op": op, "store_path": storeURL.path, "message_store_path": messageStoreURL.path]
    }

    private func failureFrom(_ reply: [String: Any]) -> MemorySyncFailure {
        let state = reply["state"] as? String ?? "unknown"
        // A source that could not be built is reported as such, so the user
        // sees "the selected source is unavailable" and never a bare failure
        // that might be mistaken for incomplete coverage.
        if state.contains(":") { return .sourceUnavailable(state: state) }
        if state == "consent_withheld" || state == "consent_state_missing"
            || state == "consent_state_malformed" || state == "consent_unobservable" {
            return .consentWithheld
        }
        return .ingestionFailed(state: state)
    }

    private func invoke(_ request: [String: Any]) -> Result<[String: Any], MemorySyncFailure> {
        guard let payload = try? JSONSerialization.data(withJSONObject: request) else {
            return .failure(.ingestionFailed(state: "request_not_encodable"))
        }
        let process = Process()
        process.executableURL = workerURL          // fixed path; never a PATH lookup
        process.arguments = []
        process.environment = Self.childEnvironment
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        do {
            try process.run()
        } catch {
            return .failure(.ingestionFailed(state: "worker_not_runnable"))
        }
        input.fileHandleForWriting.write(payload)
        try? input.fileHandleForWriting.close()

        // The deadline has to cover the read, not just the wait. Draining the
        // pipe blocks until the child closes it, so a worker that hangs while
        // holding stdout open would otherwise sit here past any timeout: the
        // watchdog terminates it, which is what ends the read.
        let expiry = Expiry()
        let watchdog = DispatchWorkItem {
            if process.isRunning {
                expiry.fired = true
                process.terminate()
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: watchdog)
        defer { watchdog.cancel() }

        // Read before waiting: a reply larger than the pipe buffer would
        // otherwise deadlock the child against a parent that is not draining.
        let data = output.fileHandleForReading.readDataToEndOfFile()
        _ = errors.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        if expiry.fired {
            return .failure(.ingestionFailed(state: "worker_timed_out"))
        }
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let reply = object as? [String: Any] else {
            // Includes the case where the worker died without a reply.
            return .failure(.ingestionFailed(state: "worker_response_malformed"))
        }
        return .success(reply)
    }

    /// Built from scratch, never inherited.
    private static var childEnvironment: [String: String] {
        [
            "HOME": NSHomeDirectory(),
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
        ]
    }

    // MARK: - Freshness mapping

    /// Maps the worker's freshness for one source into the app's summary.
    ///
    /// `coverageSummary` is *derived*, and only from what the boundaries
    /// actually say: a complete-through boundary means complete, an
    /// observed-through boundary without one means partial, a successful run
    /// that observed no window at all means unavailable, and no successful run
    /// means none. It is never a restatement of the sync's own success.
    static func summary(from freshness: Any?, source: MemorySource) -> MemoryFreshnessSummary? {
        guard let root = freshness as? [String: Any],
              let sources = root["sources"] as? [String: Any],
              let entry = sources[source.rawValue] as? [String: Any] else { return nil }
        let succeeded = entry["last_succeeded_at"] as? Double
        let observed = entry["observed_through"] as? Double
        let complete = entry["complete_through"] as? Double
        let coverage: String
        if complete != nil { coverage = "complete" }
        else if observed != nil { coverage = "partial" }
        else if succeeded != nil { coverage = "unavailable" }
        else { coverage = "none" }
        return MemoryFreshnessSummary(
            source: source,
            lastSuccessfulSync: succeeded.map(Date.init(timeIntervalSince1970:)),
            observedThrough: observed.map(Date.init(timeIntervalSince1970:)),
            completeThrough: complete.map(Date.init(timeIntervalSince1970:)),
            latestMessageAt: (entry["latest_message_at"] as? Double).map(Date.init(timeIntervalSince1970:)),
            lastRunState: entry["last_attempt_state"] as? String ?? "never",
            lastRunFailure: entry["last_attempt_failure_state"] as? String,
            coverageSummary: coverage
        )
    }
}
