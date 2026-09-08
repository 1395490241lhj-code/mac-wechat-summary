import Foundation
@testable import WeChatCompanion

/// The local history a test must use unless it is deliberately exercising the
/// filesystem.
///
/// `AppModel`'s `messageHistory` parameter defaults to `.applicationSupport` --
/// correct for the app, catastrophic for a test host. A test that constructs
/// `AppModel(...)` without injecting one, and then enables local persistence,
/// opens and migrates **the operator's real `messages.sqlite`**. That has
/// already happened once: a suite run under one schema version created the v2
/// archive tables in the real file, and a later run of an older build stamped
/// it back to version 1, leaving a database this app now refuses to open.
///
/// `url: nil` is an in-memory database, so there is no file to reach. A test
/// that genuinely needs on-disk behaviour should use `TemporaryDatabase`
/// instead -- never the canonical location.
///
/// This is dependency injection rather than a production-side "am I a test?"
/// check on purpose: the app must keep using its real store, and a runtime
/// environment guess is exactly the kind of thing that is right until the day
/// it is wrong.
func makeTestMessageHistory() -> LocalMessageHistory {
    LocalMessageHistory(url: nil)
}
