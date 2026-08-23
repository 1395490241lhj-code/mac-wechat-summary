import AppKit
import SwiftUI

struct ContentView: View {
    @Bindable var model: AppModel

    var body: some View {
        NavigationSplitView {
            List(Destination.allCases, selection: $model.selectedDestination) { destination in
                Label(destination.rawValue, systemImage: destination.systemImage)
                    .tag(destination)
            }
            .navigationTitle("WeChat Companion")
            .navigationSplitViewColumnWidth(min: 190, ideal: 210)
        } detail: {
            switch model.selectedDestination ?? .overview {
            case .overview:
                OverviewView(model: model)
            case .diagnostics:
                DiagnosticsView(model: model)
            case let destination:
                PlaceholderView(destination: destination)
            }
        }
    }
}

private struct OverviewView: View {
    @Bindable var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Overview")
                    .font(.largeTitle.bold())

                GroupBox("WeChat") {
                    VStack(spacing: 0) {
                        StatusRow(
                            label: "Application",
                            value: model.systemStatus.wechatInstalled ? "Installed" : "Not Installed",
                            isPositive: model.systemStatus.wechatInstalled
                        )
                        Divider()
                        StatusRow(
                            label: "Process",
                            value: model.systemStatus.wechatRunning ? "Running" : "Not Running",
                            isPositive: model.systemStatus.wechatRunning
                        )
                    }
                }

                GroupBox("Permissions") {
                    VStack(spacing: 0) {
                        StatusRow(
                            label: "Screen Recording",
                            value: model.systemStatus.screenRecordingGranted ? "Granted" : "Required",
                            isPositive: model.systemStatus.screenRecordingGranted
                        )
                        Divider()
                        StatusRow(
                            label: "Accessibility",
                            value: model.systemStatus.accessibilityGranted ? "Granted" : "Not Granted",
                            isPositive: model.systemStatus.accessibilityGranted
                        )
                    }
                }

                GroupBox("Diagnostics") {
                    VStack(spacing: 0) {
                        StatusRow(
                            label: "Last Status",
                            value: model.lastDiagnosticStatus.label,
                            isPositive: model.lastDiagnosticStatus == .succeeded
                        )
                        Divider()
                        LabeledContent("Last Run") {
                            Text(model.lastDiagnostic?.timestamp.formatted() ?? "Never")
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 10)
                    }
                }

                if !model.systemStatus.screenRecordingGranted {
                    Label(
                        "Screen Recording permission must be approved manually in System Settings before capture diagnostics can run.",
                        systemImage: "exclamationmark.triangle"
                    )
                    .foregroundStyle(.secondary)
                }

                Button {
                    Task { await model.runDiagnostics() }
                } label: {
                    if model.isRunningDiagnostics {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Text("Run Diagnostics")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.isRunningDiagnostics)
            }
            .padding(28)
            .frame(maxWidth: 720, alignment: .leading)
        }
        .navigationTitle("Overview")
    }
}

private struct DiagnosticsView: View {
    @Bindable var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Diagnostics")
                            .font(.largeTitle.bold())
                        Text("Only privacy-safe aggregate metadata is stored.")
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Run Diagnostics") {
                        Task { await model.runDiagnostics() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isRunningDiagnostics)
                }

                if model.isRunningDiagnostics {
                    ProgressView("Capturing one in-memory frame and measuring OCR…")
                }

                PermissionRow(
                    label: "Screen Recording Permission",
                    isGranted: model.systemStatus.screenRecordingGranted,
                    settingsAnchor: "Privacy_ScreenCapture"
                )

                if let result = model.lastDiagnostic {
                    GroupBox(result.status.label) {
                        Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 10) {
                            DiagnosticMetric("Timestamp", result.timestamp.formatted())
                            DiagnosticMetric("Screen Recording", result.screenRecordingGranted)
                            DiagnosticMetric("WeChat Running", result.wechatRunning)
                            DiagnosticMetric("Window Found", result.wechatWindowFound)
                            DiagnosticMetric("Frontmost Before", result.wechatWasFrontmostBefore)
                            DiagnosticMetric("Frontmost After", result.wechatWasFrontmostAfter)
                            DiagnosticMetric("Capture Succeeded", result.captureSucceeded)
                            DiagnosticMetric(
                                "Capture Size",
                                "\(result.captureWidth) × \(result.captureHeight)"
                            )
                            DiagnosticMetric("Image Non-empty", result.imageAppearsNonEmpty)
                            DiagnosticMetric("OCR Succeeded", result.ocrSucceeded)
                            DiagnosticMetric(
                                "Text Observations",
                                String(result.recognizedTextObservationCount)
                            )
                            DiagnosticMetric(
                                "Character Count",
                                String(result.totalRecognizedCharacterCount)
                            )
                            DiagnosticMetric(
                                "Average Confidence",
                                result.averageConfidence.formatted(.percent.precision(.fractionLength(1)))
                            )
                            DiagnosticMetric("UI Interaction", result.uiInteractionPerformed)
                            DiagnosticMetric("Private Content Persisted", result.privateContentPersisted)
                        }
                        .padding(.vertical, 8)
                    }
                } else {
                    ContentUnavailableView(
                        "No Diagnostics Yet",
                        systemImage: "stethoscope",
                        description: Text("Run diagnostics to record privacy-safe capability metadata.")
                    )
                }

                GroupBox("Interaction") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("This test temporarily scrolls the currently open WeChat conversation and restores the previous position.")
                            .foregroundStyle(.secondary)

                        PermissionRow(
                            label: "Accessibility Permission",
                            isGranted: model.systemStatus.accessibilityGranted,
                            settingsAnchor: "Privacy_Accessibility"
                        )

                        if let result = model.lastInteractionDiagnostic {
                            Divider()
                            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 10) {
                                DiagnosticMetric("Status", result.status.label)
                                DiagnosticMetric("WeChat Running", result.wechatRunning)
                                DiagnosticMetric("Window Found", result.wechatWindowFound)
                                DiagnosticMetric("Background Scroll", result.backgroundScrollSucceeded)
                                DiagnosticMetric("Foreground Scroll", result.foregroundScrollSucceeded)
                                DiagnosticMetric("Visual Change", result.imageDifferenceDetected)
                                DiagnosticMetric(
                                    "Changed Pixels",
                                    result.changedPixelRatio.formatted(
                                        .percent.precision(.fractionLength(1))
                                    )
                                )
                                DiagnosticMetric("Restore Attempted", result.restoreAttempted)
                                DiagnosticMetric("Restore Verified", result.restoreAppearsSuccessful)
                                DiagnosticMetric("Message Sent", result.messageWasSent)
                                DiagnosticMetric("Private Content Persisted", result.privateContentPersisted)
                            }
                        }

                        Button("Test Safe Scroll") {
                            Task { await model.runInteractionDiagnostics() }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isRunningInteractionDiagnostics)

                        if model.isRunningInteractionDiagnostics {
                            ProgressView("Testing one safe scroll and restoration…")
                        }
                    }
                    .padding(.vertical, 8)
                }

                if model.persistenceFailed || model.interactionPersistenceFailed {
                    Label("Diagnostic metadata could not be saved.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                }
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .navigationTitle("Diagnostics")
    }
}

private struct StatusRow: View {
    let label: String
    let value: String
    let isPositive: Bool

    var body: some View {
        LabeledContent(label) {
            Label(value, systemImage: isPositive ? "checkmark.circle.fill" : "circle.dashed")
                .foregroundStyle(isPositive ? .green : .secondary)
        }
        .padding(.vertical, 10)
    }
}

private struct PermissionRow: View {
    let label: String
    let isGranted: Bool
    let settingsAnchor: String

    var body: some View {
        LabeledContent(label) {
            HStack(spacing: 12) {
                Label(
                    isGranted ? "Granted" : "Required",
                    systemImage: isGranted ? "checkmark.circle.fill" : "circle.dashed"
                )
                .foregroundStyle(isGranted ? .green : .secondary)

                if !isGranted {
                    Button("Open System Settings") {
                        guard let url = URL(
                            string: "x-apple.systempreferences:com.apple.preference.security?\(settingsAnchor)"
                        ) else { return }
                        NSWorkspace.shared.open(url)
                    }
                }
            }
        }
        .padding(.vertical, 10)
    }
}

private struct DiagnosticMetric: View {
    let label: String
    let value: String

    init(_ label: String, _ value: String) {
        self.label = label
        self.value = value
    }

    init(_ label: String, _ value: Bool) {
        self.init(label, value ? "Yes" : "No")
    }

    var body: some View {
        GridRow {
            Text(label)
                .foregroundStyle(.secondary)
            Text(value)
                .textSelection(.enabled)
        }
    }
}

private struct PlaceholderView: View {
    let destination: Destination

    var body: some View {
        ContentUnavailableView(
            destination.rawValue,
            systemImage: destination.systemImage,
            description: Text("This area will be added in a later reviewed phase.")
        )
        .navigationTitle(destination.rawValue)
    }
}
