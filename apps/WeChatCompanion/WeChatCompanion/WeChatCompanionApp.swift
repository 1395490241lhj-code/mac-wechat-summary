import SwiftUI

@main
struct WeChatCompanionApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup("WeChat Companion") {
            ContentView(model: model)
                .frame(minWidth: 760, minHeight: 520)
                .task {
                    await model.bootstrap(
                        autoRunDiagnostics: ProcessInfo.processInfo.arguments.contains(
                            "--run-diagnostics"
                        )
                    )
                }
        }
        .defaultSize(width: 920, height: 640)
    }
}
