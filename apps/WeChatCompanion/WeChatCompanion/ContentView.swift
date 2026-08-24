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
            case .chats:
                ChatsView(model: model)
            case .settings:
                SettingsView(model: model)
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
                    }
                }

                GroupBox("WeChat Window") {
                    VStack(spacing: 0) {
                        StatusRow(
                            label: "Status",
                            value: model.captureMetrics.state.label,
                            isPositive: model.captureMetrics.state == .observing
                        )
                        Divider()
                        LabeledContent("Selected Capture") {
                            Text("System Window Share")
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 10)
                        Divider()
                        LabeledContent("Meaningful Frames") {
                            Text(String(model.captureMetrics.meaningfulFramesObserved))
                                .foregroundStyle(.secondary)
                                .monospacedDigit()
                        }
                        .padding(.vertical, 10)
                        Divider()
                        LabeledContent("Duplicates Skipped") {
                            Text(String(model.captureMetrics.duplicateFramesSkipped))
                                .foregroundStyle(.secondary)
                                .monospacedDigit()
                        }
                        .padding(.vertical, 10)
                        Divider()
                        LabeledContent("Last Observed") {
                            Text(model.captureMetrics.lastFrameAt?
                                .formatted(date: .omitted, time: .standard) ?? "Never")
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 10)
                        Divider()
                        HStack {
                            Button(model.needsWindowSelection
                                ? "Select WeChat Window" : "Change Window") {
                                Task { await model.selectWeChatWindow() }
                            }
                            .buttonStyle(.borderedProminent)

                            if model.captureMetrics.state == .paused {
                                Button("Resume") {
                                    Task { await model.resumeObserving() }
                                }
                            } else {
                                Button("Pause") {
                                    Task { await model.pauseObserving() }
                                }
                                .disabled(model.captureMetrics.state != .observing)
                            }
                            Button("Stop Observing") {
                                Task { await model.stopObserving() }
                            }
                            .disabled(model.needsWindowSelection)
                            Spacer()
                        }
                        .padding(.vertical, 10)
                        if model.captureMetrics.state == .selectionLost {
                            Divider()
                            Text("The selected window is no longer available. "
                                + "Select it again to resume observing.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .padding(.vertical, 10)
                        }
                    }
                }

                CapturePreviewSection(model: model)

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
                        Text("Test Passive Capture")
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

/// Development preview of the exact frame handed to extraction. Memory only:
/// no save, export, copy, or pasteboard action exists.
private struct CapturePreviewSection: View {
    @Bindable var model: AppModel

    var body: some View {
        GroupBox("Capture Preview") {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Label("Memory only — never saved", systemImage: "eye.slash")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Clear Preview") {
                        Task { await model.clearCapturePreview() }
                    }
                    .disabled(model.capturePreview == nil)
                }
                .padding(.vertical, 10)

                if let preview = model.capturePreview {
                    Divider()
                    Image(decorative: preview.image, scale: 1)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(maxWidth: .infinity, maxHeight: 420)
                        .background(.quaternary)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                        .padding(.vertical, 10)
                    Divider()
                    LabeledContent("Frame") {
                        Text("\(preview.image.width) × \(preview.image.height) px · "
                            + preview.captureMode.label)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 10)
                } else {
                    Divider()
                    Text("No meaningful frame yet.")
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 10)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
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
                    Button("Test Passive Capture") {
                        Task { await model.runDiagnostics() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isRunningDiagnostics)
                }

                if model.isRunningDiagnostics {
                    ProgressView("Testing passive in-memory capture…")
                }

                PermissionRow(
                    label: "Screen Recording Permission",
                    isGranted: model.systemStatus.screenRecordingGranted,
                    settingsAnchor: "Privacy_ScreenCapture"
                )

                if let result = model.lastDiagnostic {
                    GroupBox("Passive Capture") {
                        Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 10) {
                            DiagnosticMetric("Status", result.status.label)
                            DiagnosticMetric("Timestamp", result.timestamp.formatted())
                            DiagnosticMetric("Screen Recording", result.screenRecordingGranted)
                            DiagnosticMetric("WeChat Running", result.wechatRunning)
                            DiagnosticMetric("Window Found", result.wechatWindowFound)
                            DiagnosticMetric(
                                "Visibility",
                                result.wechatFrontmost ? "Frontmost" : "Background"
                            )
                            DiagnosticMetric("Window On Screen", result.windowOnScreen)
                            DiagnosticMetric(
                                "Capture Source",
                                result.selectedCaptureMode?.label
                                    ?? (result.waitingForVisibleWeChat ? "Waiting" : "Unavailable")
                            )
                            DiagnosticMetric(
                                "Image",
                                result.imageAppearsNonEmpty ? "Readable" : "Empty"
                            )
                            DiagnosticMetric(
                                "Capture Size",
                                "\(result.captureWidth) × \(result.captureHeight)"
                            )
                            DiagnosticMetric("Window Capture Attempted", result.windowCaptureAttempted)
                            DiagnosticMetric("Window Capture Non-empty", result.windowCaptureNonEmpty)
                            DiagnosticMetric(
                                "Display Region Attempted",
                                result.displayRegionCaptureAttempted
                            )
                            DiagnosticMetric(
                                "Display Region Non-empty",
                                result.displayRegionCaptureNonEmpty
                            )
                            DiagnosticMetric(
                                "Text Observations",
                                String(result.recognizedTextObservationCount)
                            )
                            DiagnosticMetric(
                                "Character Count",
                                String(result.totalRecognizedCharacterCount)
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

                if model.persistenceFailed {
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

private struct ChatsView: View {
    @Bindable var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Chats")
                    .font(.largeTitle.weight(.semibold))

                GroupBox("Message Extraction") {
                    VStack(spacing: 0) {
                        StatusRow(
                            label: "Status",
                            value: model.extractionMetrics.status.label,
                            isPositive: model.extractionMetrics.status == .ready
                                || model.extractionMetrics.status == .processing
                        )
                        Divider()
                        StatusRow(
                            label: "Remote Processing",
                            value: model.allowsRemoteProcessing ? "Enabled" : "Disabled",
                            isPositive: model.allowsRemoteProcessing
                        )
                        Divider()
                        ExtractionMetric(
                            label: "Meaningful Frames Received",
                            value: model.extractionMetrics.framesReceived
                        )
                        Divider()
                        ExtractionMetric(
                            label: "Successful Extractions",
                            value: model.extractionMetrics.extractionsSucceeded
                        )
                        Divider()
                        ExtractionMetric(
                            label: "Dropped While Busy",
                            value: model.extractionMetrics.framesDroppedWhileBusy
                        )
                        Divider()
                        ExtractionMetric(
                            label: "Failures",
                            value: model.extractionMetrics.extractionsFailed
                        )
                        Divider()
                        // Shown separately: pausing cancels work, which is
                        // expected lifecycle rather than an error.
                        ExtractionMetric(
                            label: "Cancelled",
                            value: model.extractionMetrics.extractionsCancelled
                        )
                        Divider()
                        // Makes a consent misconfiguration obvious rather than
                        // silently looking like "nothing is happening".
                        ExtractionMetric(
                            label: "Withheld Pending Consent",
                            value: model.extractionMetrics.framesWithheldPendingConsent
                        )
                        Divider()
                        LabeledContent("Last Extraction") {
                            Text(
                                model.extractionMetrics.lastExtractionAt?
                                    .formatted(date: .omitted, time: .standard) ?? "Never"
                            )
                            .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 10)
                    }
                }

                Text(
                    model.extractionMetrics.status == .notConfigured
                        ? "No extraction provider is configured, so no message content is read. "
                            + "Frames are observed and released."
                        : "Extracted message content stays in memory and is not stored yet."
                )
                .font(.callout)
                .foregroundStyle(.secondary)

                if let failure = model.extractionMetrics.lastFailure {
                    GroupBox("Last Failure Diagnosis") {
                        VStack(spacing: 0) {
                            LabeledContent("Category") {
                                Text(failure.category.label).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 10)
                            Divider()
                            FailureDetail("HTTP Status", failure.httpStatus.map(String.init))
                            Divider()
                            FailureDetail("URL Error Code", failure.urlErrorCode.map(String.init))
                            Divider()
                            FailureDetail("Keychain Status", failure.keychainStatus.map(String.init))
                            Divider()
                            FailureDetail("Finish Reason", failure.finishReason?.label)
                            Divider()
                            FailureDetail("Block Reason", failure.blockReason?.label)
                            Divider()
                            FailureDetail(
                                "Output Characters",
                                failure.outputCharacterCount.map(String.init)
                            )
                            Divider()
                            FailureDetail(
                                "Prompt Tokens", failure.promptTokenCount.map(String.init)
                            )
                            Divider()
                            FailureDetail(
                                "Candidate Tokens", failure.candidatesTokenCount.map(String.init)
                            )
                            Divider()
                            FailureDetail(
                                "Thinking Tokens", failure.thoughtsTokenCount.map(String.init)
                            )
                            Divider()
                            FailureDetail(
                                "Total Tokens", failure.totalTokenCount.map(String.init)
                            )
                            Divider()
                            FailureDetail(
                                "Occurred",
                                failure.occurredAt?.formatted(date: .omitted, time: .standard)
                            )
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Text("Diagnosis is aggregate metadata only — no response body, "
                        + "message text, or image data is recorded.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                LatestExtractionSection(extraction: model.latestExtraction)

                Spacer(minLength: 0)
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Chats")
    }
}

/// Development-stage preview of the most recent extraction. Memory only:
/// nothing here is written to disk, and it disappears when the app exits.
private struct LatestExtractionSection: View {
    let extraction: ExtractedConversationFrame?

    var body: some View {
        GroupBox("Latest Extraction") {
            VStack(alignment: .leading, spacing: 0) {
                Label("Not saved — live extraction preview", systemImage: "eye.trianglebadge.exclamationmark")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 10)

                if let extraction {
                    Divider()
                    LabeledContent("Chat") {
                        Text(extraction.chat?.title ?? "Unknown")
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    .padding(.vertical, 10)
                    Divider()
                    ExtractionMetric(
                        label: "Visible Messages",
                        value: extraction.messages.count
                    )
                    Divider()
                    LabeledContent("Captured") {
                        Text(extraction.capturedAt.formatted(date: .omitted, time: .standard))
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 10)

                    if extraction.messages.isEmpty {
                        Divider()
                        Text("No legible messages in this frame.")
                            .foregroundStyle(.secondary)
                            .padding(.vertical, 10)
                    } else {
                        ForEach(Array(extraction.messages.enumerated()), id: \.offset) { _, message in
                            Divider()
                            ExtractedMessageRow(message: message)
                        }
                    }
                } else {
                    Divider()
                    Text("No extraction yet.")
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 10)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct ExtractedMessageRow: View {
    let message: ExtractedVisibleMessage

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(message.sender ?? "Unknown sender")
                    .font(.callout.weight(.medium))
                Text(message.ownership.rawValue)
                    .font(.caption)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary, in: Capsule())
                Text(message.kind.rawValue)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Text(message.visibleTime ?? "—")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(message.text ?? "(no visible text)")
                .font(.callout)
                .foregroundStyle(message.text == nil ? .secondary : .primary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
    }
}

private struct SettingsView: View {
    @Bindable var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Settings")
                    .font(.largeTitle.weight(.semibold))

                GroupBox("Gemini Extraction Provider") {
                    VStack(alignment: .leading, spacing: 0) {
                        StatusRow(
                            label: "API Key",
                            value: model.hasProviderCredential
                                ? "Stored in Keychain"
                                : "Not configured",
                            isPositive: model.hasProviderCredential
                        )
                        Divider()
                        HStack {
                            SecureField("Gemini API key", text: $model.apiKeyInput)
                                .textFieldStyle(.roundedBorder)
                            Button("Save to Keychain") {
                                Task { await model.saveProviderAPIKey() }
                            }
                            .disabled(model.apiKeyInput.trimmingCharacters(
                                in: .whitespacesAndNewlines
                            ).isEmpty)
                            Button("Remove") {
                                Task { await model.removeProviderAPIKey() }
                            }
                            .disabled(!model.hasProviderCredential)
                        }
                        .padding(.vertical, 10)
                        if model.credentialErrorOccurred {
                            Divider()
                            Text("The Keychain rejected that change. Nothing was stored.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .padding(.vertical, 10)
                        }
                        Divider()
                        Text("The key is stored only in the macOS Keychain. It is never "
                            + "written to preferences, files, or logs.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .padding(.vertical, 10)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                GroupBox("Remote Processing") {
                    VStack(alignment: .leading, spacing: 0) {
                        Toggle(
                            "Allow remote AI processing of visible WeChat frames",
                            isOn: Binding(
                                get: { model.allowsRemoteProcessing },
                                set: { newValue in
                                    Task { await model.setAllowsRemoteProcessing(newValue) }
                                }
                            )
                        )
                        .padding(.vertical, 10)
                        Divider()
                        Text("When enabled, meaningful WeChat frames may be sent to the "
                            + "configured AI provider for message extraction. Off by "
                            + "default. Saving an API key does not enable this.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .padding(.vertical, 10)
                        Divider()
                        StatusRow(
                            label: "Extraction Status",
                            value: model.extractionMetrics.status.label,
                            isPositive: model.extractionMetrics.status == .ready
                                || model.extractionMetrics.status == .processing
                        )
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                Spacer(minLength: 0)
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Settings")
    }
}

/// Shows an em dash when a diagnostic value is not applicable or absent.
private struct FailureDetail: View {
    let label: String
    let value: String?

    init(_ label: String, _ value: String?) {
        self.label = label
        self.value = value
    }

    var body: some View {
        LabeledContent(label) {
            Text(value ?? "—")
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
        .padding(.vertical, 10)
    }
}

private struct ExtractionMetric: View {
    let label: String
    let value: Int

    var body: some View {
        LabeledContent(label) {
            Text(String(value))
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
        .padding(.vertical, 10)
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
