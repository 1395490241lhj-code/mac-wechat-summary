import SwiftUI

@main
struct WeChatCompanionApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup("WeChat Companion") {
            ContentView(model: model)
                .frame(minWidth: 760, minHeight: 520)
                .task {
                    let arguments = ProcessInfo.processInfo.arguments
                    await model.bootstrap(
                        autoRunDiagnostics: arguments.contains("--run-diagnostics"),
                        runObserverValidation: arguments.contains("--run-observer-validation")
                    )
                }
        }
        .defaultSize(width: 920, height: 640)
    }
}
