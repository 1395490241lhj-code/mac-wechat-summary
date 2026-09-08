import Foundation

/// What kind of process this is, for decisions that must not depend on a
/// developer remembering something.
///
/// The one question here — "am I running inside an XCTest host?" — has a single
/// definition on purpose. It existed already in `PackagedMemorySyncRunner`,
/// where it stops a test resolving the live memory worker against the
/// operator's own store. Copying that predicate to a second call site would
/// eventually give the app two nearly-identical notions of "test", and the day
/// they disagreed would be the day one of them was wrong about the user's data.
///
/// Deliberately not a launch argument. A scheme flag is forgotten by
/// `xcodebuild`, by another scheme, by CI, and by whoever joins next; a safety
/// property that depends on being remembered is not a safety property. A launch
/// argument may still exist as an explicit debug seam, but never as the
/// boundary itself.
///
/// This inspects only its own process: three well-known XCTest markers, no user
/// path, no file, no preference, no side effect. It does **not** link XCTest.
enum RuntimeEnvironment {
    /// True when this process is hosting an XCTest bundle.
    ///
    /// Three signals rather than one because they fail in different situations:
    /// the two environment variables are set by the test runner, and the
    /// runtime class lookup still answers when a bundle is loaded into an
    /// already-running process.
    static var isUnderTestHost: Bool {
        let environment = ProcessInfo.processInfo.environment
        return environment["XCTestConfigurationFilePath"] != nil
            || environment["XCTestBundlePath"] != nil
            || NSClassFromString("XCTestCase") != nil
    }
}

/// Whether the app's launch path should bring up production services.
///
/// Split out from the SwiftUI scene so the decision is testable without
/// launching a second process. It is a single boolean rule, and keeping it that
/// small is the point: there is no launch architecture here to get wrong.
enum AppBootstrapPolicy {
    /// `false` inside an XCTest host.
    ///
    /// When the test target uses the app as its `TEST_HOST`, `@main` runs for
    /// real: the scene appears, `bootstrap()` restores the stored consent, and
    /// on a machine where the operator granted local persistence the app opens
    /// its **real** `messages.sqlite` before a single test executes. Dependency
    /// injection inside tests cannot prevent that — the app is not a dependency
    /// of the tests, it is the process hosting them.
    ///
    /// Suppressing bootstrap also keeps diagnostics loading, capture, the
    /// extraction coordinator, observer polling and memory-worker resolution
    /// out of a test process, none of which a unit test asked for.
    static func shouldBootstrap(isUnderTestHost: Bool) -> Bool {
        !isUnderTestHost
    }
}
