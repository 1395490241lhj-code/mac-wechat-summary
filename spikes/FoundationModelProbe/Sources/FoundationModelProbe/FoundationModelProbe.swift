// Feasibility probe: can Apple's on-device model turn already-extracted chat
// text into the structure a digest would want?
//
// Answer, measured 2026-08-28 on macOS 27.0: yes, for TEXT. See the vault's
// Experiments note. The companion question -- can it read the text out of a
// WeChat screenshot -- was answered NO and is not reproduced here; that probe
// is recorded in the vault as a measured failure.
//
// Deliberately standalone. This is not part of the app: per-frame local triage
// was reviewed and rejected (no consumer, wrong granularity, blocks the
// extraction path). Kept only so the capability claim stays reproducible.
//
// Run: swift run FoundationModelProbe
import Foundation
import FoundationModels

@Generable
struct ChatTriage {
    @Guide(description: "One short sentence in the SAME language as the chat. A Chinese chat must be summarised in Chinese.")
    var summary: String
    @Guide(description: "True only if someone is asked to do something concrete.")
    var hasActionItem: Bool
    @Guide(description: "The action in a few words, in the SAME language as the chat. Empty when there is none.")
    var actionItem: String
    @Guide(description: "True only if the action has a deadline or scheduled time.")
    var reminderNeeded: Bool
    @Guide(description: "Time expressions exactly as written, e.g. 明天下午三点. Empty if none.")
    var timeReferences: [String]
}

let instructions = """
    You read one screenful of an already-transcribed chat and report only what \
    it literally says.

    - Never invent a fact, a time, or a task that is not in the text.
    - Answer in the same language the chat is written in. A Chinese chat must \
    produce Chinese output. Never translate into English.
    - hasActionItem is false for small talk and acknowledgements.
    - reminderNeeded requires both an action and a time.
    - Prefer reporting nothing over guessing.
    """

/// Entirely fabricated. No real conversation is used anywhere in this spike.
let transcript = """
    王经理: 明天下午三点前把季度报告发给我
    小李: 收到
    张伟: 晚上有人问周末要不要吃火锅
    """

@main
struct Probe {
    static func main() async {
        let model = SystemLanguageModel.default
        print("availability:", model.availability)
        guard model.isAvailable else {
            // Most likely cause, learned the hard way: the Siri language must
            // match the macOS language. See the vault's Experiments note.
            print("unavailable — stopping rather than reporting a fabricated result")
            return
        }

        for run in 1...2 {
            let started = Date()
            do {
                let session = LanguageModelSession(instructions: instructions)
                let result = try await session.respond(
                    to: transcript, generating: ChatTriage.self
                ).content
                let elapsed = Date().timeIntervalSince(started)
                print("""

                    run \(run) — \(String(format: "%.2f", elapsed))s
                      summary:        \(result.summary)
                      hasActionItem:  \(result.hasActionItem)
                      actionItem:     \(result.actionItem)
                      reminderNeeded: \(result.reminderNeeded)
                      timeReferences: \(result.timeReferences)
                    """)
            } catch {
                print("run \(run) failed: \(error)")
            }
        }
    }
}
