import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @Bindable var model: AppModel

    var body: some View {
        NavigationSplitView {
            List(Destination.primary, selection: Binding<Destination?>(
                get: { model.primaryNavigationDestination },
                set: { model.selectedDestination = $0 ?? .overview }
            )) { destination in
                Label(destination.rawValue, systemImage: destination.systemImage)
                    .tag(destination)
            }
            .navigationTitle("WeChat Companion")
            .navigationSplitViewColumnWidth(min: 190, ideal: 210)
        } detail: {
            switch model.selectedDestination ?? .overview {
            case .overview:
                HomeView(model: model)
            case .chats:
                ChatsView(model: model)
            case .search:
                SearchView(model: model)
            case .dailySummary:
                DailySummaryView(model: model)
            case .reminders:
                FollowUpsView(model: model)
            case .agents:
                AgentsView(model: model)
            case .settings:
                SettingsView(model: model)
            case .diagnostics:
                DiagnosticsView(model: model)
            case let destination:
                PlaceholderView(destination: destination)
            }
        }
        .toolbar {
            if let destination = model.selectedDestination,
               !Destination.primary.contains(destination) {
                ToolbarItem(placement: .navigation) {
                    Button("Back to \(model.primaryNavigationDestination.rawValue)", systemImage: "chevron.left") {
                        model.selectedDestination = model.primaryNavigationDestination
                    }
                }
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

                GroupBox("Safe Share → Archive") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Share a supported WeChat conversation export to WeChat Companion using the Share menu. Read and search the imported evidence in Archive.")
                        Text("Safe Share does not require Screen Recording or a Gemini API key.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        Button("Open Archive", systemImage: "tray.full") {
                            Task { await model.openArchiveBrowser() }
                        }
                        .buttonStyle(.borderedProminent)
                        Text("Then explicitly sync eligible attributed Archive evidence into Memory for Daily Summary and Follow-ups. Preparation, candidate scanning and saving remain explicit.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        Divider()
                        switch model.archiveEvidence.storeState {
                        case .disabled:
                            Text("Local message storage is off. Enable it in Settings to persist Archive imports; enabling it can resume queued shares.")
                        case .unavailable:
                            Text("Local message storage is unavailable. Archive imports cannot be persisted until the store is available.")
                        case .ready:
                            Text("Local message storage is available. Queued shares can be imported; Memory sync remains a separate explicit action.")
                        }
                        Button("Open Local Storage Settings") {
                            model.selectedDestination = .settings
                        }
                        .help("Open Settings, then Local Message Storage. This does not change consent.")
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                DisclosureGroup("Visual Capture — Optional / Experimental") {
                    Text("Visual capture uses Screen Recording and a separately configured extraction provider. It is not required for Safe Share.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
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
                                value: model.systemStatus.screenRecordingGranted ? "Granted" : "Required for Visual capture",
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

#if DEBUG
                VisualQualityGateSection(model: model)
#endif

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

#if DEBUG
private struct VisualQualityGateSection: View {
    @Bindable var model: AppModel

    private var canBegin: Bool {
        model.allowsRemoteProcessing
            && model.hasProviderCredential
    }

    private var unavailableReason: String? {
        if !model.allowsRemoteProcessing {
            "Remote Processing consent is off. Enable the existing consent in Settings first."
        } else if !model.hasProviderCredential {
            "A Gemini credential is not configured."
        } else {
            nil
        }
    }

    var body: some View {
        GroupBox("Real Visual Extraction Quality Gate · Debug only") {
            VStack(alignment: .leading, spacing: 12) {
                Text("Frames are sent through the existing Gemini extractor only while Remote Processing consent is on. Nothing is written to the message store or diagnostics. Review content stays in memory and is cleared when the evaluation ends.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                if let modelID = model.visualQualityGateModelID {
                    Text("Frozen Gemini model: \(modelID)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if model.visualQualityGateIsActive {
                    switch model.visualQualityGatePhase {
                    case .selectingWindow:
                        Text("Normal extraction and local ingestion are isolated. Select the WeChat window to begin the evaluation.")
                            .foregroundStyle(.secondary)
                        Button("Select WeChat Window") {
                            Task { await model.selectWindowForVisualQualityGate() }
                        }
                        .buttonStyle(.borderedProminent)
                    case .capturing:
                        ProgressView("Capturing and extracting one frame…")
                    case .reviewing:
                        if let review = model.visualQualityGateReview {
                            VisualQualityFrameReviewView(model: model, review: review)
                        }
                    case .failed:
                        Label(
                            "Extraction failed: \(model.visualQualityGateMetrics.lastFailureCategory?.label ?? "Unknown")",
                            systemImage: "exclamationmark.triangle"
                        )
                        .foregroundStyle(.secondary)
                        Button("Capture Another Frame") {
                            Task { await model.captureNextVisualQualityFrame() }
                        }
                    case .readyForNext:
                        Text("Review recorded. Change the WeChat view manually if needed, then capture the next frame.")
                            .foregroundStyle(.secondary)
                        Button("Capture Next Frame") {
                            Task { await model.captureNextVisualQualityFrame() }
                        }
                    case .inactive, .finished:
                        EmptyView()
                    }

                    HStack {
                        if model.visualQualityGatePhase == .reviewing {
                            Button("Finish and Discard This Frame") {
                                Task { await model.finishVisualQualityGate() }
                            }
                        }
                        Button("Finish Evaluation") {
                            Task { await model.finishVisualQualityGate() }
                        }
                        .buttonStyle(.bordered)
                    }
                } else {
                    if model.visualQualityGatePhase == .finished {
                        Text("Evaluation ended. Images, extracted rows, and reconciliation keys were cleared.")
                            .foregroundStyle(.secondary)
                    }
                    if let unavailableReason {
                        Text(unavailableReason)
                            .foregroundStyle(.secondary)
                    }
                    Button("Begin Evaluation") {
                        Task { await model.beginVisualQualityGate() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canBegin)
                }

                VisualQualityMetricsView(metrics: model.visualQualityGateMetrics)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct VisualQualityFrameReviewView: View {
    @Bindable var model: AppModel
    let review: VisualQualityFrameReview

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Frame \(model.visualQualityGateMetrics.totalFrames) · \(review.extraction.messages.count) extracted rows")
                .font(.headline)
            Image(decorative: review.frame.image, scale: 1)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxHeight: 420)
                .clipShape(RoundedRectangle(cornerRadius: 6))

            LabeledContent("Extracted conversation title") {
                Text(review.extraction.chat?.title ?? "No title returned")
                    .multilineTextAlignment(.trailing)
                qualityPicker(
                    "Review",
                    selection: Binding(
                        get: { model.visualQualityGateReview?.titleRating },
                        set: { model.setVisualQualityTitleRating($0) }
                    ),
                    choices: VisualQualityTitleRating.allCases,
                    label: \.label
                )
            }

            LabeledContent("Reconciliation") {
                Text("\(review.reconciliation.action.label) · overlap \(review.reconciliation.overlapCount) · new \(review.reconciliation.newMessageCount)")
                    .foregroundStyle(.secondary)
            }

            ForEach(review.extraction.messages.indices, id: \.self) { index in
                let message = review.extraction.messages[index]
                GroupBox("Message \(index + 1)") {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledContent("Sender", value: message.sender ?? "No sender returned")
                        LabeledContent("Visible time", value: message.visibleTime ?? "No time returned")
                        LabeledContent("Message kind", value: message.kind.qualityLabel)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Extracted text")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(message.text ?? "No text returned")
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }

                        qualityPicker(
                            "Detection",
                            selection: messageBinding(index, keyPath: \.detection),
                            choices: [.correct, .hallucinated, .duplicate],
                            label: \.label
                        )
                        qualityPicker(
                            "Sender",
                            selection: messageBinding(index, keyPath: \.sender),
                            choices: VisualQualitySenderRating.allCases,
                            label: \.label
                        )
                        qualityPicker(
                            "Text",
                            selection: messageBinding(index, keyPath: \.text),
                            choices: VisualQualityTextRating.allCases,
                            label: \.label
                        )
                        qualityPicker(
                            "Time",
                            selection: messageBinding(index, keyPath: \.time),
                            choices: VisualQualityTimeRating.allCases,
                            label: \.label
                        )
                        qualityPicker(
                            "Kind",
                            selection: messageBinding(index, keyPath: \.kind),
                            choices: VisualQualityKindRating.allCases,
                            label: \.label
                        )
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }

            HStack {
                Button("−") { model.removeMissingVisibleMessage() }
                    .disabled(review.missingVisibleMessages == 0)
                    .accessibilityLabel("Remove one missing visible message")
                Text("Missing visible messages: \(review.missingVisibleMessages)")
                Button("+") { model.addMissingVisibleMessage() }
                    .accessibilityLabel("Add one missing visible message")
            }
            Text("Mark a message Duplicate only when that same visible message appeared in an earlier reviewed frame. Correct means a distinct visible message. Missing messages are counted above without retyping their content.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Button("Record Review") {
                Task { await model.recordVisualQualityReview() }
            }
            .buttonStyle(.borderedProminent)
            .disabled(!review.isComplete)
        }
    }

    private func messageBinding<Value: Hashable>(
        _ index: Int,
        keyPath: WritableKeyPath<VisualQualityMessageAssessment, Value?>
    ) -> Binding<Value?> {
        Binding(
            get: {
                guard let review = model.visualQualityGateReview,
                      review.messageAssessments.indices.contains(index) else { return nil }
                return review.messageAssessments[index][keyPath: keyPath]
            },
            set: { value in
                model.setVisualQualityMessageRating(at: index) {
                    $0[keyPath: keyPath] = value
                }
            }
        )
    }

    private func qualityPicker<Value: Hashable & Identifiable>(
        _ title: String,
        selection: Binding<Value?>,
        choices: [Value],
        label: @escaping (Value) -> String
    ) -> some View where Value.ID == Value {
        Picker(title, selection: selection) {
            Text("Select…").tag(nil as Value?)
            ForEach(choices, id: \.self) { choice in
                Text(label(choice)).tag(Optional(choice))
            }
        }
    }
}

private struct VisualQualityMetricsView: View {
    let metrics: VisualQualityMetrics

    var body: some View {
        GroupBox("Aggregate Results · Memory Only") {
            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 8) {
                row("Frames attempted / reviewed", "\(metrics.totalFrames) / \(metrics.reviewedFrames)")
                row("Conversation-title correctness", fraction(metrics.conversationTitleAccuracy))
                row("Message detection precision", fraction(metrics.messageDetectionPrecision))
                row("Message detection recall", fraction(metrics.messageDetectionRecall))
                row("Sender correctness", fraction(metrics.senderAccuracy))
                row("Text exact rate", fraction(metrics.textExactRate))
                row("Text exact or minor error", fraction(metrics.textExactOrMinorRate))
                row("Visible-time correctness", fraction(metrics.visibleTimeAccuracy))
                row("Message-kind correctness", fraction(metrics.messageKindAccuracy))
                row("Visible messages", String(metrics.visibleMessages))
                row("Unknown title/sender", String(metrics.unknownCount))
                row("Text visually unreadable", String(metrics.textVisuallyUnreadable))
                row("Time not shown / unreadable", "\(metrics.timeNotShown) / \(metrics.timeUnreadable)")
                row("Hallucinated messages", String(metrics.hallucinatedMessageCount))
                row("Hallucinated message content", String(metrics.hallucinatedContentCount))
                row("All hallucinated fields", String(metrics.hallucinationCount))
                row("Duplicate rows after reconciliation", String(metrics.duplicateAfterReconciliationCount))
                row("Lost rows across overlap", String(metrics.lostMessageCountAcrossOverlap))
                row("Append / prepend / gaps / unchanged", "\(metrics.appendFrames) / \(metrics.prependFrames) / \(metrics.gapFrames) / \(metrics.unchangedFrames)")
                row("Extraction failures", String(metrics.extractionFailures))
                if metrics.totalFrames > 0 {
                    row(
                        "Fabricated-message hard gate",
                        metrics.reviewedFrames == 0
                            ? "Not Established"
                            : (metrics.fabricatedMessageHardGatePasses ? "0 observed" : "FAIL")
                    )
                }
            }
            if !metrics.failureCategories.isEmpty {
                Text(metrics.failureCategories.keys.sorted { $0.rawValue < $1.rawValue }
                    .map { "\($0.label): \(metrics.failureCategories[$0, default: 0])" }
                    .joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func row(_ title: String, _ value: String) -> some View {
        GridRow {
            Text(title).foregroundStyle(.secondary)
            Text(value).monospacedDigit()
        }
    }

    private func fraction(_ rate: VisualQualityRate) -> String {
        "\(rate.numerator) / \(rate.denominator)"
    }
}

private extension VisibleMessageKind {
    var qualityLabel: String {
        switch self {
        case .text: "Text"
        case .image: "Image"
        case .file: "File"
        case .link: "Link"
        case .voice: "Voice"
        case .system: "System"
        case .unknown: "Unknown"
        }
    }
}
#endif

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
    @State private var isChoosingArchive = false
    @State private var isShowingShareHelp = false
    @State private var highlightedAnchor: ContextRevealAnchor?
    @State private var highlightGeneration: UInt64 = 0

    var body: some View {
        HSplitView {
            ConsumerConversationList(model: model)
                .frame(minWidth: 210, idealWidth: 250, maxWidth: 320)
            ScrollViewReader { scroll in
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Chats")
                    .font(.largeTitle.weight(.semibold))

                ImportAttentionView(model: model)

                if model.selectedVisualConversationID != nil || model.visualContextUnavailable {
                    CaptureLedgerSection(model: model, highlightedAnchor: highlightedAnchor, readerOnly: true)
                        .id("visual-context")
                } else {
                    ArchiveEvidenceBrowser(
                        model: model,
                        isChoosingArchive: $isChoosingArchive,
                        highlightedAnchor: highlightedAnchor,
                        consumerMode: true
                    )
                    .id("archives")
                }

                Spacer(minLength: 0)
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Chats")
        .toolbar {
            ToolbarItem {
                Button("Search messages", systemImage: "magnifyingglass") { model.selectedDestination = .search }
                    .keyboardShortcut("f", modifiers: .command)
            }
            ToolbarItem {
                Menu("More", systemImage: "ellipsis") {
                    Button("Summary of prepared conversations") { model.openArchiveDailySummary() }
                    Button("Safe Share — add from WeChat") { isShowingShareHelp = true }
                    Button("Add saved conversation…") { isChoosingArchive = true }
                        .disabled(model.archiveImportStatus == .importing)
                    Button("Advanced…") { model.selectedDestination = .settings }
                }
                .accessibilityIdentifier("chats.more")
            }
        }
        .alert("Add a conversation with Safe Share", isPresented: $isShowingShareHelp) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("In WeChat, share a supported conversation export to WeChat Companion. Local storage consent is required. This adds a saved copy; it does not send messages or change WeChat.")
        }
        .onAppear {
            guard model.contextRevealRequest == nil else { return }
            switch model.contextNavigationTarget {
            case .archiveBrowser, .archiveImport: scroll.scrollTo("archives", anchor: .top)
            case .visualConversation: scroll.scrollTo("visual-context", anchor: .top)
            case nil: break
            }
        }
        .onChange(of: model.contextNavigationTarget) { _, target in
            guard model.contextRevealRequest == nil else { return }
            switch target {
            case .archiveBrowser, .archiveImport: scroll.scrollTo("archives", anchor: .top)
            case .visualConversation: scroll.scrollTo("visual-context", anchor: .top)
            case nil: break
            }
        }
        .onChange(of: model.visualContextUnavailable) { _, unavailable in
            if unavailable { scroll.scrollTo("visual-context", anchor: .top) }
        }
        .onChange(of: model.directContextSelectionGeneration) { _, _ in
            highlightedAnchor = nil
        }
        .task(id: model.contextRevealRequest) {
            guard let request = model.contextRevealRequest else { return }
            // A task on the updated view runs after SwiftUI has installed the
            // canonical row's typed .id in this ScrollViewReader.
            await Task.yield()
            guard model.contextRevealRequest == request else { return }
            let isRendered: Bool
            switch request.anchor {
            case .visualMessage(let id):
                isRendered = model.selectedVisualMessages.contains(where: { $0.id == id })
            case .archiveRecord(let importID, let sequence):
                isRendered = model.selectedArchiveRecords.contains(where: {
                    $0.importID == importID && $0.sequence == sequence
                })
            }
            guard isRendered else { return }
            highlightedAnchor = request.anchor
            highlightGeneration = request.generation
            scroll.scrollTo(request.anchor, anchor: .center)
            AccessibilityNotification.Announcement("Search result revealed in Chats").post()
            model.consumeContextReveal(generation: request.generation)
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(2))
                if highlightGeneration == request.generation { highlightedAnchor = nil }
            }
        }
        .fileImporter(
            isPresented: $isChoosingArchive,
            allowedContentTypes: [.zip],
            allowsMultipleSelection: false
        ) { result in
            guard case .success(let urls) = result, let url = urls.first else { return }
            Task { await model.importWeChatArchive(from: url) }
        }
        }
        }
    }
}

private struct AdvancedChatsView: View {
    @Bindable var model: AppModel
    @State private var isChoosingArchive = false

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            CaptureLedgerSection(model: model, highlightedAnchor: nil)
            ArchiveEvidenceBrowser(model: model, isChoosingArchive: $isChoosingArchive, highlightedAnchor: nil)
            CaptureStatusDisclosure(model: model, ledger: CaptureLedgerPresentation(ledger: model.captureLedger))
            DiagnosticsDisclosure(model: model)
        }
        .fileImporter(isPresented: $isChoosingArchive, allowedContentTypes: [.zip], allowsMultipleSelection: false) { result in
            guard case .success(let urls) = result, let url = urls.first else { return }
            Task { await model.importWeChatArchive(from: url) }
        }
    }
}

private struct ArchiveEvidenceBrowser: View {
    @Bindable var model: AppModel
    @State private var searchText = ""
    @Binding var isChoosingArchive: Bool
    let highlightedAnchor: ContextRevealAnchor?
    var consumerMode = false

    private var trimmedSearch: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !consumerMode {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("Archive Conversations")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button("Daily Summary", systemImage: "text.document") {
                    model.openArchiveDailySummary()
                }
                .disabled(model.dailySummaryPhase.isRunning)
                .help("Open Daily Summary with Archive selected. Sync and preparation remain explicit.")
                Button("Choose WeChat Export ZIP…") {
                    isChoosingArchive = true
                }
                .disabled(model.archiveImportStatus == .importing)
            }

            Text("Read and search imported evidence here. For Daily Summary and Follow-ups, explicitly sync eligible attributed Archive evidence in Settings → Memory. Their selected source and time window can span applicable imports. Unattributed exports remain available for reading/search, not Memory. Imports are not complete WeChat history.")
                .font(.callout)
                .foregroundStyle(.secondary)

            }
            if model.archiveContextUnavailable {
                TranscriptStateRow(state: .contextVanished)
            }
            switch model.archiveEvidence.storeState {
            case .disabled:
                TranscriptStateRow(state: .storageOff)
            case .unavailable:
                TranscriptStateRow(state: .storeUnavailable)
            case .ready:
                readyContent
            }

            if model.archiveEvidence.storeState == .ready && !consumerMode {
                Text(model.archiveImportStatus.message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let attachmentMessage = model.archiveAttachmentImportStatus.message,
                model.archiveEvidence.storeState == .ready,
                !consumerMode {
                Text(attachmentMessage)
                    .font(.caption)
                    .foregroundStyle(
                        model.archiveAttachmentImportStatus == .unavailable
                            ? Color.orange : Color.secondary
                    )
            }
        }
        .padding(.top, 4)
    }

    @ViewBuilder
    private var readyContent: some View {
        if model.archiveEvidence.imports.isEmpty {
            if consumerMode {
                ContentUnavailableView("Your conversations, in one place", systemImage: "bubble.left.and.bubble.right", description: Text("Share a conversation from WeChat to WeChat Companion, or add a saved conversation from the More menu."))
            } else {
                TranscriptStateRow(state: .neverCaptured)
            }
        } else {
            if !consumerMode {
            HStack(spacing: 8) {
                TextField("Search imported messages", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { submitSearch() }
                Button("Search") { submitSearch() }
                    .disabled(trimmedSearch.isEmpty)
            }

            }
            importsAndRecords
        }
    }

    @ViewBuilder
    private var importsAndRecords: some View {
        if !consumerMode {
        Text("Imports")
            .font(.callout.weight(.medium))
        ForEach(model.archiveEvidence.imports) { summary in
            ArchiveImportRow(
                summary: summary,
                isSelected: summary.id == model.selectedArchiveImportID
            ) {
                Task { await model.selectArchiveImport(summary.id) }
            }
        }

        }
        if let selected = model.archiveEvidence.imports.first(where: {
            $0.id == model.selectedArchiveImportID
        }) {
            if !consumerMode { Divider() }
            VStack(alignment: .leading, spacing: 4) {
                TranscriptContextHeader(
                    title: selected.displayName ?? (consumerMode ? "Saved conversation" : "Imported WeChat Archive"),
                    summary: (consumerMode ? "Saved " : "Archive · imported ")
                        + selected.importedAt.formatted(date: .abbreviated, time: .shortened)
                        + (consumerMode ? "" : " · \(selected.recordCount) records")
                        + (selected.attachmentCount > 0
                            ? " · \(selected.attachmentCount) attachments" : "")
                        + (!consumerMode && selected.isAnonymous
                            ? (selected.link == nil ? " · Unlinked export" : " · Explicitly linked")
                            : ""),
                    caveat: transcriptWindowNote(summary: selected)
                )
            }

            // Naming and linking are real controls, but they are not what the
            // reader came for. They stay one disclosure away instead of
            // sitting between the header and the transcript at full weight.
            DisclosureGroup("Details") {
                ArchiveLinkControls(model: model, summary: selected)
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            if model.searchHitUnavailable {
                TranscriptStateRow(state: .searchHitVanished)
            } else if model.selectedArchiveRecords.isEmpty {
                TranscriptStateRow(state: .emptyImport)
            } else {
                TranscriptList(
                    rows: model.selectedArchiveRecords.map(TranscriptRow.init(archive:)),
                    highlightedAnchor: highlightedAnchor
                )
            }

            if selected.attachmentCount > 0 {
                Divider()
                ArchiveAttachmentSection(
                    batches: model.selectedArchiveAttachmentBatches,
                    model: model
                )
            }
        }
    }

    private func submitSearch() {
        guard !trimmedSearch.isEmpty else { return }
        model.beginArchiveSearch(searchText)
    }

    /// The one honest thing the reader still needs to know about how much of
    /// the import is on screen, said once in the header instead of under every
    /// row.
    private func transcriptWindowNote(summary: ArchiveEvidenceImportSummary) -> String {
        if model.selectedArchiveIsHitWindow {
            return consumerMode ? "Showing part of this conversation around your search result." : "Showing a window of up to 500 records around the search result."
        }
        if summary.recordCount > model.selectedArchiveRecords.count {
            if consumerMode { return "Showing the beginning of this saved conversation; more messages are not shown here." }
            return "Showing the first \(model.selectedArchiveRecords.count) of "
                + "\(summary.recordCount) records."
        }
        return consumerMode
            ? "This saved copy may include only part of the conversation."
            : summary.shape.label + " import · read-only local evidence."
    }
}

private struct ArchiveLinkControls: View {
    @Bindable var model: AppModel
    let summary: ArchiveEvidenceImportSummary
    @State private var isEditingDisplayName = false
    @State private var displayNameDraft = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Archive display name")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(summary.displayName ?? "Not set")
                        .font(.callout.weight(.medium))
                }
                Spacer()
                Button(summary.displayName == nil ? "Set Name…" : "Rename…") {
                    displayNameDraft = summary.displayName ?? ""
                    isEditingDisplayName = true
                }
                .disabled(model.archiveDisplayNameStatus == .saving)
                if summary.displayName != nil {
                    Button("Clear") {
                        Task { await model.clearArchiveImportDisplayName(summary.id) }
                    }
                    .disabled(model.archiveDisplayNameStatus == .saving)
                }
            }

            Text("Display name is a label you confirm. It does not link or merge conversations.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Divider()

            if let link = summary.link {
                HStack {
                    Label("Linked to \(link.visualConversationTitle)", systemImage: "link")
                        .font(.callout.weight(.medium))
                    Spacer()
                    Button("Unlink") {
                        Task { await model.unlinkArchiveImport(summary.id) }
                    }
                    .disabled(model.archiveLinkStatus == .linking)
                }
            } else if model.captureLedger.conversations.isEmpty {
                Text("Not linked to a visually captured conversation.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                HStack {
                    Text("Not linked to a visually captured conversation.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Menu("Link…") {
                        ForEach(model.captureLedger.conversations) { conversation in
                            Button(conversation.title) {
                                Task {
                                    await model.linkArchiveImport(
                                        summary.id,
                                        toVisualConversationID: conversation.id
                                    )
                                }
                            }
                        }
                    }
                    .disabled(model.archiveLinkStatus == .linking)
                }
            }

            Text("Links are explicit assertions. WeChat Companion never links chats from matching text or timing alone.")
                .font(.caption)
                .foregroundStyle(.secondary)

            if let message = model.archiveDisplayNameStatus.message {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(
                        model.archiveDisplayNameStatus == .invalid
                            || model.archiveDisplayNameStatus == .unavailable
                            ? Color.red : Color.secondary
                    )
            }

            if let message = model.archiveLinkStatus.message {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(
                        model.archiveLinkStatus == .conflict
                            || model.archiveLinkStatus == .unavailable
                            ? Color.red : Color.secondary
                    )
            }
        }
        .padding(.vertical, 4)
        .alert("Archive Display Name", isPresented: $isEditingDisplayName) {
            TextField("Group or chat name", text: $displayNameDraft)
            Button("Cancel", role: .cancel) {}
            Button("Save") {
                let value = displayNameDraft
                Task {
                    await model.setArchiveImportDisplayName(
                        summary.id,
                        displayName: value
                    )
                }
            }
        } message: {
            Text("This name is only a display label you confirm. It will not link or merge conversations.")
        }
    }
}


private struct ArchiveAttachmentSection: View {
    let batches: [ArchiveEvidenceAttachmentBatch]
    let model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Attachments")
                .font(.callout.weight(.medium))
            Text("Import-level evidence only · Unlinked to message")
                .font(.caption)
                .foregroundStyle(.secondary)

            Group {
                if batches.isEmpty {
                Text("Attachment metadata is unavailable for this import.")
                    .foregroundStyle(.secondary)
                } else {
                    ForEach(batches) { batch in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(batch.observedAt.formatted(date: .abbreviated, time: .shortened))
                                .font(.callout.weight(.medium))
                            Spacer()
                            Text("\(batch.attachmentCount) items · \(batch.materializedCount) stored")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        ForEach(batch.attachments) { attachment in
                            ArchiveAttachmentRow(
                                attachment: attachment,
                                onPreview: { Task { await model.previewArchiveAttachment(attachment) } },
                                onReveal: { Task { await model.revealArchiveAttachment(attachment) } }
                            )
                        }
                    }
                    .padding(.vertical, 6)

                    if batch.id != batches.last?.id {
                        Divider()
                    }
                }
                .frame(maxWidth: TranscriptLayout.readableWidth, alignment: .leading)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if let message = model.attachmentPreviewStatus.message {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct ArchiveAttachmentRow: View {
    let attachment: ArchiveEvidenceAttachment
    /// Only ever reached for a `.materialized` row: the caller in
    /// `ArchiveAttachmentSection` hides both actions for every other state.
    let onPreview: () -> Void
    let onReveal: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: systemImage)
                .foregroundStyle(.secondary)
                .frame(width: 18)

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.callout)
                Text(statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            // The two text lines read as one item, but the Preview and Reveal
            // buttons beside them keep their own identities.
            .accessibilityElement(children: .combine)
            .accessibilityLabel(
                "\(title). \(statusText). Import-level evidence, unlinked to message."
            )

            Spacer(minLength: 12)

            Text(ByteCountFormatter.string(
                fromByteCount: Int64(attachment.byteCount),
                countStyle: .file
            ))
            .font(.caption)
            .foregroundStyle(.secondary)

            if attachment.isMaterialized {
                Button("Preview", action: onPreview)
                    .buttonStyle(.link)
                    .font(.caption)
                Button("Reveal in Finder", action: onReveal)
                    .buttonStyle(.link)
                    .font(.caption)
            }
        }
        .padding(.vertical, 4)
    }

    private var title: String {
        let ext = attachment.pathExtension == "(none)"
            ? "No extension"
            : attachment.pathExtension.uppercased()
        if let kind = attachment.kind {
            return "\(kindLabel(kind)) · \(ext)"
        }
        return ext
    }

    private var statusText: String {
        switch attachment.storageState {
        case .materialized:
            "Stored locally"
        case .unsupportedType:
            "Metadata only · unsupported type"
        case .typeMismatch:
            "Metadata only · type could not be verified"
        case .oversized:
            "Metadata only · exceeds per-file limit"
        case .budgetExceeded:
            "Metadata only · batch materialization limit reached"
        }
    }

    private var systemImage: String {
        switch attachment.kind {
        case .image: "photo"
        case .video: "video"
        case .document: "doc"
        case nil: "paperclip"
        }
    }

    private func kindLabel(_ kind: WeChatNativeAttachmentKind) -> String {
        switch kind {
        case .image: "Image"
        case .video: "Video"
        case .document: "Document"
        }
    }
}

private struct ArchiveImportRow: View {
    let summary: ArchiveEvidenceImportSummary
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(
                        summary.displayName
                            ?? summary.importedAt.formatted(date: .abbreviated, time: .shortened)
                    )
                    .font(.body.weight(.medium))
                    Text(
                        (summary.displayName == nil
                            ? ""
                            : "\(summary.importedAt.formatted(date: .abbreviated, time: .shortened)) · ")
                        + summary.shape.label + " · \(summary.recordCount) records"
                    )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color.accentColor)
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 7)
            .background(
                isSelected ? Color.accentColor.opacity(0.12) : Color.clear,
                in: RoundedRectangle(cornerRadius: 6)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Shared transcript presentation

/// One canonical row, rendered.
///
/// Visual and Archive rows share their typography, spacing, reading width and
/// highlight container, but the source-specific wording is resolved upstream
/// in `TranscriptRow`, so an unattributed archive record can never read like
/// an attributed one that simply lost its sender.
///
/// The row carries its own reveal anchor. Grouping wraps rows from the
/// outside and never moves that anchor onto a container.
private struct TranscriptRowView: View {
    let row: TranscriptRow
    /// Optional extra secondary line. Archive search results use it to say
    /// when the import happened; the reader does not otherwise repeat it.
    var caption: String?
    var isHighlighted = false
    var showsSender = true
    var action: (() -> Void)?

    var body: some View {
        Group {
            if let action {
                Button(action: action) { content }
                    .buttonStyle(.plain)
            } else {
                content
            }
        }
        .id(row.id)
        .background(
            isHighlighted
                ? Color.accentColor.opacity(0.16) : Color.clear,
            in: RoundedRectangle(cornerRadius: 6)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            [row.sender, row.literalTime, row.shortTime, row.body]
                .compactMap { $0 }
                .joined(separator: ". ")
                + ". " + row.source.rowProvenance
        )
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if showsSender {
                    Text(row.sender)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 12)
                if let literalTime = row.literalTime {
                    Text(row.timeLabel ?? literalTime)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                } else if let shortTime = row.shortTime {
                    Text(shortTime)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Text(row.body)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let caption {
                Text(caption)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 6)
        .frame(maxWidth: TranscriptLayout.readableWidth, alignment: .leading)
        .contentShape(Rectangle())
    }
}

/// A transcript is a column of prose, not a dashboard: the reading width is
/// bounded for the eye, but a narrow window simply gets a narrower column
/// rather than clipping.
private enum TranscriptLayout {
    static let readableWidth: CGFloat = 680
}

/// A small header that answers "which conversation am I reading?" in three
/// lines: what it is, how much of it is here, and what the times mean.
/// Provenance is stated once here instead of on every row.
private struct TranscriptContextHeader: View {
    let title: String
    let summary: String
    var caveat: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            Text(summary)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let caveat {
                Text(caveat)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: TranscriptLayout.readableWidth, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// One line for a state where the transcript has nothing to show. The wording
/// says why, because "storage off", "never captured" and "removed by
/// retention" are three different problems with three different fixes.
private struct TranscriptStateRow: View {
    let state: TranscriptState

    var body: some View {
        Text(state.text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: TranscriptLayout.readableWidth, alignment: .leading)
            .padding(.vertical, 4)
    }
}

/// The grouped transcript itself.
///
/// Groups are presentation only. Every row keeps its own `.id`, so a search
/// hit still scrolls to the exact canonical row and highlights only that row,
/// even when it sits in the middle of a sender run.
private struct TranscriptList: View {
    let rows: [TranscriptRow]
    let highlightedAnchor: ContextRevealAnchor?
    var now: Date = Date()

    private var groups: [TranscriptGroup] {
        TranscriptGrouping.groups(rows)
    }

    var body: some View {
        let separated = TranscriptGrouping.dateSeparatorIndices(in: groups)
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(groups.enumerated()), id: \.element.id) { index, group in
                VStack(alignment: .leading, spacing: 0) {
                    if separated.contains(index), let label = group.dateLabel(reference: now) {
                        Text(label)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .padding(.top, 12)
                            .padding(.bottom, 6)
                            .accessibilityAddTraits(.isHeader)
                    }
                    ForEach(group.rows) { row in
                        TranscriptRowView(
                            row: row,
                            isHighlighted: highlightedAnchor == row.id,
                            showsSender: row.id == group.rows.first?.id
                        )
                    }
                }
                .padding(.bottom, 16)
            }
        }
        .frame(maxWidth: TranscriptLayout.readableWidth, alignment: .leading)
    }
}
/// Which conversations have actually been captured, how much of each is still
/// kept, and whether capture has obvious gaps.
///
/// The ledger stays aggregate-only; selecting a row opens bounded retained
/// text from that captured conversation.
private struct CaptureLedgerSection: View {
    @Bindable var model: AppModel
    let highlightedAnchor: ContextRevealAnchor?
    var readerOnly = false
    @State private var hoveredConversationID: Int64?

    private var presentation: CaptureLedgerPresentation {
        CaptureLedgerPresentation(ledger: model.captureLedger)
    }

    var body: some View {
        let shown = presentation
        VStack(alignment: .leading, spacing: 12) {
            if !readerOnly {
            Text("Captured Conversations")
                .font(.title3.weight(.semibold))

            VStack(alignment: .leading, spacing: 0) {
                if let message = shown.emptyMessage {
                    Text(message)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    ForEach(Array(shown.conversations.enumerated()), id: \.element.id) { index, row in
                        if index > 0 { Divider() }
                        Button {
                            Task { await model.selectVisualConversation(row.id) }
                        } label: {
                            CapturedConversationRow(row: row)
                                .padding(.horizontal, 8)
                                .background(
                                    hoveredConversationID == row.id
                                        || model.selectedVisualConversationID == row.id
                                        ? Color.accentColor.opacity(0.12) : Color.clear,
                                    in: RoundedRectangle(cornerRadius: 6)
                                )
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Open captured conversation \(row.title), \(row.retainedCount)")
                        .accessibilityHint("Shows retained Visual capture messages")
                        .onHover { hovering in
                            hoveredConversationID = hovering ? row.id : nil
                            let cursor = hovering ? NSCursor.pointingHand : NSCursor.arrow
                            cursor.set()
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            }
            Group {
                if model.visualContextUnavailable {
                    TranscriptStateRow(state: .contextVanished)
                } else if let selected = model.captureLedger.conversations.first(where: {
                    $0.id == model.selectedVisualConversationID
                }) {
                    if !readerOnly { Divider() }
                    VStack(alignment: .leading, spacing: 10) {
                        let count = model.selectedVisualMessages.count
                        let windowNote = model.selectedVisualIsHitWindow
                            ? "Showing a window of up to 100 messages around the search result."
                            : "Showing the newest retained messages."
                        let summary = (readerOnly ? "\(count) saved messages · first seen " : "Visual capture · \(count) retained messages · captured ")
                            + firstCapturedRange(selected)
                        TranscriptContextHeader(
                            title: selected.title,
                            summary: summary,
                            caveat: (readerOnly ? "This is part of the conversation. Times show when messages were seen, not sent. " : "Times are first observed, not sent times. ") + windowNote
                        )
                        if model.searchHitUnavailable {
                            TranscriptStateRow(state: .searchHitVanished)
                        } else if model.selectedVisualMessages.isEmpty {
                            TranscriptStateRow(state: .noRetainedRows)
                        } else {
                            TranscriptList(
                                rows: model.selectedVisualMessages.map(TranscriptRow.init(visual:)),
                                highlightedAnchor: highlightedAnchor
                            )
                        }
                    }
                }
            }
            .id("visual-context")

            if let note = shown.retentionNote {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.top, 4)
    }

    /// When this conversation was first and last seen on screen. Observation
    /// metadata, not message send times.
    private func firstCapturedRange(_ summary: CapturedConversationSummary) -> String {
        let first = summary.firstCapturedAt.formatted(date: .abbreviated, time: .shortened)
        let last = summary.lastCapturedAt.formatted(date: .abbreviated, time: .shortened)
        return first == last ? first : first + " – " + last
    }
}

/// Capture runtime state, kept out of the reader's way. The whole point of
/// the Chats page is the conversations; whether a frame was dropped because
/// the extractor was busy is a question the reader only has when something
/// looks wrong.
private struct CaptureStatusDisclosure: View {
    @Bindable var model: AppModel
    let ledger: CaptureLedgerPresentation

    var body: some View {
        DisclosureGroup("Capture status") {
            VStack(alignment: .leading, spacing: 0) {
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
                LabeledContent("Selected Gemini Model") {
                    Text(model.selectedGeminiModel.label).foregroundStyle(.secondary)
                }
                .padding(.vertical, 10)
                Divider()
                Text(
                    model.extractionMetrics.status == .notConfigured
                        ? "No extraction provider is configured, so no message content is read. "
                            + "Frames are observed and released."
                        : "Extracted message content stays in memory and is not stored yet."
                )
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)

                if !ledger.healthRows.isEmpty {
                    Divider()
                    Text("Capture health")
                        .font(.callout.weight(.medium))
                        .frame(maxWidth: .infinity, alignment: .leading)
                    ForEach(Array(ledger.healthRows.enumerated()), id: \.element.id) { index, row in
                        if index > 0 { Divider() }
                        LabeledContent(row.label) {
                            Text(row.value).foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 8)
                    }
                }

                ForEach(ledger.guidance, id: \.self) { advice in
                    Divider()
                    Text(advice)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 8)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.title3.weight(.semibold))
    }
}

/// Failure diagnosis, frame counters and the live extraction preview. All of
/// it stays available; none of it is on the first screen.
private struct DiagnosticsDisclosure: View {
    @Bindable var model: AppModel

    var body: some View {
        DisclosureGroup("Diagnostics") {
            VStack(alignment: .leading, spacing: 12) {
                VStack(spacing: 0) {
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

                if let failure = model.extractionMetrics.lastFailure {
                    Divider()
                    Text("Last failure diagnosis")
                        .font(.callout.weight(.medium))
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
                        FailureDetail("Prompt Tokens", failure.promptTokenCount.map(String.init))
                        Divider()
                        FailureDetail(
                            "Candidate Tokens", failure.candidatesTokenCount.map(String.init)
                        )
                        Divider()
                        FailureDetail(
                            "Thinking Tokens", failure.thoughtsTokenCount.map(String.init)
                        )
                        Divider()
                        FailureDetail("Total Tokens", failure.totalTokenCount.map(String.init))
                        Divider()
                        FailureDetail(
                            "Occurred",
                            failure.occurredAt?.formatted(date: .omitted, time: .standard)
                        )
                    }
                    Text("Diagnosis is aggregate metadata only — no response body, "
                        + "message text, or image data is recorded.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                LatestExtractionSection(extraction: model.latestExtraction)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.title3.weight(.semibold))
    }
}

private struct CapturedConversationRow: View {
    let row: CaptureLedgerPresentation.ConversationRow

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(row.title)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 12)
                Text(row.retainedCount)
                    .foregroundStyle(.secondary)
            }
            Text("Captured \(row.firstCaptured) – \(row.lastCaptured)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 7)
        .frame(maxWidth: .infinity, alignment: .leading)
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

private struct AdvancedSettingsView: View {
    @Bindable var model: AppModel

    var body: some View {
            VStack(alignment: .leading, spacing: 20) {
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
                        Picker("Model", selection: Binding(
                            get: { model.selectedGeminiModel },
                            set: { newValue in
                                Task { await model.setGeminiModel(newValue) }
                            }
                        )) {
                            ForEach(GeminiModel.allCases) { option in
                                Text(option.label).tag(option)
                            }
                        }
                        // Disabled mid-extraction so a model change cannot race
                        // an in-flight request.
                        .disabled(model.isExtractionProcessing)
                        .padding(.vertical, 10)
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

                MemorySection(model: model)
                    .id("memory")

                Spacer(minLength: 0)
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Memory status and the explicit Sync Now action (M2.2c).
///
/// Three different facts are shown as three different rows -- last successful
/// sync, observed through, latest message -- because they answer three
/// different questions, and a sync failure is shown apart from incomplete
/// coverage for the same reason.
private struct MemorySection: View {
    @Bindable var model: AppModel

    var body: some View {
        GroupBox("Memory") {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Text("Source")
                    Spacer()
                    Menu(model.memorySource.label) {
                        ForEach(MemorySource.appSelectable, id: \.rawValue) { source in
                            Button {
                                Task { await model.setMemorySource(source) }
                            } label: {
                                if source == model.memorySource {
                                    Label(source.label, systemImage: "checkmark")
                                } else {
                                    Text(source.label)
                                }
                            }
                        }
                    }
                    .disabled(model.memorySyncPhase.isRunning)
                }
                .padding(.vertical, 8)

                if model.memorySource == .archive {
                    Text("Only attributed archive messages are promoted into Memory. "
                        + "Unattributed exports remain archive-only until they have trustworthy time/attribution semantics.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.bottom, 8)
                }
                Divider()
                if model.isMemoryAvailable {
                    MemoryRow(label: "Last successful sync", value: stamp(model.memoryFreshness?.lastSuccessfulSync))
                    Divider()
                    MemoryRow(label: "Observed through", value: stamp(model.memoryFreshness?.observedThrough))
                    Divider()
                    MemoryRow(label: "Complete through", value: stamp(model.memoryFreshness?.completeThrough))
                    Divider()
                    MemoryRow(label: "Latest stored message", value: stamp(model.memoryFreshness?.latestMessageAt))
                    Divider()
                    MemoryRow(label: "Coverage", value: model.memoryFreshness?.coverageSummary ?? "—")
                    Divider()
                    MemoryRow(label: "Last sync state", value: lastRunState)
                    Divider()
                } else {
                    Text("Memory persistence and sync are unavailable while local message "
                        + "storage is off. Turning storage off does not delete existing memory.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 10)
                    Divider()
                }
                HStack(alignment: .firstTextBaseline) {
                    Button(model.memorySyncPhase.isRunning ? "Syncing…" : "Sync Now") {
                        Task { await model.syncMemoryNow() }
                    }
                    .disabled(!model.canSyncMemory)
                    if model.memorySyncPhase.isRunning {
                        ProgressView().controlSize(.small)
                    }
                    Text(phaseText)
                        .font(.callout)
                        .foregroundStyle(phaseIsFailure ? .red : .secondary)
                }
                .padding(.vertical, 10)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var lastRunState: String {
        guard let freshness = model.memoryFreshness else { return "—" }
        if let failure = freshness.lastRunFailure { return "\(freshness.lastRunState) (\(failure))" }
        return freshness.lastRunState
    }

    private var phaseIsFailure: Bool {
        if case .failed = model.memorySyncPhase { return true }
        return false
    }

    private var phaseText: String {
        switch model.memorySyncPhase {
        case .idle:
            return model.isMemoryAvailable ? "Runs once, in the foreground, when you ask." : ""
        case .running:
            return "Reading the selected source into Memory."
        case .succeeded(let counts):
            return "Done: \(counts.messagesInserted) new, \(counts.messagesUpdated) re-observed, "
                + "\(counts.conversationsSeen) conversation(s)."
        case .failed(let failure):
            return failure.message
        }
    }

    private func stamp(_ date: Date?) -> String {
        guard let date else { return "—" }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}

private struct MemoryRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
            Spacer()
            Text(value).foregroundStyle(.secondary).monospacedDigit()
        }
        .padding(.vertical, 8)
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


private struct DailySummaryView: View {
    @Bindable var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Daily Summary")
                        .font(.largeTitle.bold())
                    Text("A local evidence digest from Memory. Preparing it makes no network request and does not run an AI model.")
                        .foregroundStyle(.secondary)
                }

                GroupBox("Summary Window") {
                    VStack(alignment: .leading, spacing: 12) {
                        DisclosureGroup("Advanced source options") {
                        HStack {
                            Text("Source")
                            Spacer()
                            Menu(model.dailySummarySource.label) {
                                ForEach(MemorySource.appSelectable, id: \.rawValue) { source in
                                    Button {
                                        model.setDailySummarySource(source)
                                    } label: {
                                        if source == model.dailySummarySource {
                                            Label(source.label, systemImage: "checkmark")
                                        } else {
                                            Text(source.label)
                                        }
                                    }
                                }
                            }
                            .disabled(model.dailySummaryPhase.isRunning)
                        }

                        }
                        Divider()

                        HStack {
                            Text("Window")
                            Spacer()
                            Menu(model.dailySummaryWindow.label) {
                                ForEach(DailySummaryWindow.allCases) { window in
                                    Button {
                                        model.setDailySummaryWindow(window)
                                    } label: {
                                        if window == model.dailySummaryWindow {
                                            Label(window.label, systemImage: "checkmark")
                                        } else {
                                            Text(window.label)
                                        }
                                    }
                                }
                            }
                            .disabled(model.dailySummaryPhase.isRunning)
                        }

                        Divider()

                        if model.dailySummarySource == .archive {
                            Text("Uses already-synced attributed Archive evidence for the selected window across all imports, not just the export you are browsing. Unattributed records and attachments are excluded. Importing does not sync Memory.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        HStack {
                            Button("Prepare chat content…") {
                                Task { await model.openDailySummaryMemorySettings() }
                            }
                            .disabled(!model.canOpenDailySummaryMemorySettings)
                            .help("Select this summary source in Memory Settings. Sync remains explicit.")
                            if !model.canOpenDailySummaryMemorySettings && model.memorySyncPhase.isRunning {
                                Text("Wait for the current Memory sync before changing its source.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        HStack {
                            Text("Reads only the already-synced Memory store.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Button(model.dailySummarySnapshot == nil ? "Prepare Summary" : "Refresh") {
                                Task { await model.prepareDailySummary() }
                            }
                            .disabled(!model.canPrepareDailySummary)
                        }
                    }
                    .padding(.vertical, 6)
                }

                if !model.allowsLocalPersistence {
                    ContentUnavailableView(
                        "Local Storage Is Off",
                        systemImage: "externaldrive.badge.xmark",
                        description: Text("Enable local message storage in Settings before Daily Summary can read Memory.")
                    )
                } else {
                    summaryState
                }
            }
            .padding(24)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .navigationTitle("Daily Summary")
    }

    @ViewBuilder
    private var summaryState: some View {
        switch model.dailySummaryPhase {
        case .idle:
            ContentUnavailableView(
                "Ready to Prepare",
                systemImage: "text.document",
                description: Text("Choose a source and bounded time window, then prepare a local evidence digest.")
            )
        case .running:
            HStack(spacing: 10) {
                ProgressView()
                Text("Reading Memory evidence…")
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 12)
        case .failed(let failure):
            ContentUnavailableView(
                "Summary Evidence Unavailable",
                systemImage: "exclamationmark.triangle",
                description: Text(failure.message)
            )
        case .ready:
            if let snapshot = model.dailySummarySnapshot {
                DailySummarySnapshotView(snapshot: snapshot)
            }
        }
    }
}

private struct DailySummarySnapshotView: View {
    let snapshot: DailySummarySnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            GroupBox("Evidence Status") {
                VStack(spacing: 0) {
                    summaryRow("Source", snapshot.source.label)
                    Divider()
                    summaryRow("Window", windowText)
                    Divider()
                    summaryRow("Coverage", coverageLabel)
                    Divider()
                    summaryRow("Returned messages", String(snapshot.returnedMessages))
                    Divider()
                    summaryRow("Conversations", String(snapshot.returnedConversations))
                    Divider()
                    summaryRow("Senders", String(snapshot.returnedSenders))
                    if let lastSync = snapshot.freshness?.lastSuccessfulSync {
                        Divider()
                        summaryRow(
                            "Last Memory sync",
                            lastSync.formatted(date: .abbreviated, time: .shortened)
                        )
                    }
                }
                .padding(.vertical, 4)
            }

            if snapshot.truncated || snapshot.textTruncatedCount > 0 || !snapshot.coverage.caveats.isEmpty {
                GroupBox("Caveats") {
                    VStack(alignment: .leading, spacing: 8) {
                        if snapshot.truncated {
                            Label(
                                "The evidence set is capped at the newest 200 messages in this window.",
                                systemImage: "ellipsis.circle"
                            )
                        }
                        if snapshot.textTruncatedCount > 0 {
                            Label(
                                "\(snapshot.textTruncatedCount) long message(s) were clipped in the structured input.",
                                systemImage: "text.badge.ellipsis"
                            )
                        }
                        ForEach(snapshot.coverage.caveats, id: \.self) { caveat in
                            Label(caveat, systemImage: "info.circle")
                        }
                    }
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 6)
                }
            }

            evidenceDigest
        }
    }

    @ViewBuilder
    private var evidenceDigest: some View {
        if snapshot.messages.isEmpty {
            if snapshot.coverage.trustworthyEmpty {
                ContentUnavailableView(
                    "No Messages in This Covered Window",
                    systemImage: "checkmark.circle",
                    description: Text("Memory reports complete coverage for the selected source and window.")
                )
            } else {
                ContentUnavailableView(
                    "No Stored Evidence in This Window",
                    systemImage: "questionmark.circle",
                    description: Text("Coverage is not complete, so this cannot be interpreted as “no messages happened.”")
                )
            }
        } else {
            GroupBox("Local Evidence Digest") {
                VStack(alignment: .leading, spacing: 16) {
                    Text(digestSentence)
                        .font(.headline)

                    if !snapshot.senders.isEmpty {
                        Text("Most active senders: " + senderSummary)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }

                    Divider()

                    ForEach(snapshot.conversations) { conversation in
                        DailySummaryConversationSection(
                            conversation: conversation,
                            messages: snapshot.messages.filter {
                                $0.conversationIndex == conversation.id
                            }
                        )
                        if conversation.id != snapshot.conversations.last?.id {
                            Divider()
                        }
                    }

                    Text("This view is a deterministic local digest of stored evidence, not an AI-generated interpretation.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 6)
            }
        }
    }

    private var digestSentence: String {
        "\(snapshot.returnedMessages) messages across \(snapshot.returnedConversations) conversations from \(snapshot.returnedSenders) senders."
    }

    private var senderSummary: String {
        snapshot.senders.prefix(6)
            .map { "\($0.sender) (\($0.count))" }
            .joined(separator: ", ")
    }

    private var coverageLabel: String {
        switch snapshot.coverage.status {
        case "complete": "Complete"
        case "partial": "Partial"
        case "unavailable": "Unavailable"
        case "not_observed": "Not observed"
        default: snapshot.coverage.status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private var windowText: String {
        snapshot.start.formatted(date: .abbreviated, time: .shortened)
            + " – "
            + snapshot.end.formatted(date: .abbreviated, time: .shortened)
    }

    private func summaryRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 8)
    }
}

private struct DailySummaryConversationSection: View {
    let conversation: DailySummaryConversation
    let messages: [DailySummaryMessage]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(conversation.label)
                    .font(.headline)
                Spacer()
                Text("\(conversation.messageCount) messages")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text(
                conversation.firstAt.formatted(date: .omitted, time: .shortened)
                    + " – "
                    + conversation.lastAt.formatted(date: .omitted, time: .shortened)
            )
            .font(.caption)
            .foregroundStyle(.secondary)

            ForEach(messages) { message in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(message.sender ?? "Unknown sender")
                            .font(.callout.weight(.medium))
                        Spacer()
                        Text(message.timestamp.formatted(date: .omitted, time: .shortened))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Text(message.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if message.textTruncated {
                        Text("Text clipped in summary input")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 4)
            }
        }
    }
}



private struct FollowUpsView: View {
    @Bindable var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var completedExpanded = false
    @State private var findingExpanded = false
    @State private var candidateToSave: FollowUpCandidate?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Follow-ups").font(.largeTitle.weight(.semibold))
                savedFollowUps

                HStack {
                    Button("Find follow-ups", systemImage: "magnifyingglass") {
                        findingExpanded = true
                        Task { await model.scanFollowUps() }
                    }
                    .disabled(!model.canScanFollowUps)
                    Spacer()
                }

                if !model.allowsLocalPersistence {
                    Text("Turn on local storage to find and keep follow-ups.")
                        .font(.callout).foregroundStyle(.secondary)
                    Button("Open Settings") { model.selectedDestination = .settings }
                } else if findingExpanded || model.followUpPhase != .idle {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("\(model.followUpWindow.label) · Prepared conversations")
                            .font(.caption).foregroundStyle(.secondary)
                        candidateState
                        DisclosureGroup("Options & details") {
                            findingOptions
                        }
                        .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .navigationTitle("Follow-ups")
        .task {
            await model.refreshSavedFollowUps()
        }
        .confirmationDialog(
            "Save this follow-up locally?",
            isPresented: Binding(
                get: { candidateToSave != nil },
                set: { if !$0 { candidateToSave = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let candidateToSave {
                Button("Save Follow-Up") {
                    let id = candidateToSave.id
                    self.candidateToSave = nil
                    Task { await model.saveFollowUpCandidate(id) }
                }
            }
            Button("Cancel", role: .cancel) {
                candidateToSave = nil
            }
        } message: {
            Text("Keep this follow-up on this Mac. No due date or notification will be added.")
        }
    }

    @ViewBuilder
    private var savedFollowUps: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let error = model.reminderStoreError {
                Label(reminderStoreMessage(error), systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.secondary)
            } else if model.savedFollowUps.isEmpty {
                ContentUnavailableView("No saved follow-ups", systemImage: "checklist",
                    description: Text("Review a suggested follow-up and save the ones you want to keep."))
            } else {
                let pending = model.savedFollowUps.filter { $0.status == .pending }
                let completed = model.savedFollowUps.filter { $0.status == .completed }
                if pending.isEmpty {
                    Text("No pending follow-ups.").foregroundStyle(.secondary)
                }
                ForEach(pending) { reminder in
                    SavedFollowUpRow(reminder: reminder, model: model)
                    if reminder.id != pending.last?.id { Divider() }
                }
                if !completed.isEmpty {
                    DisclosureGroup("Completed (\(completed.count))", isExpanded: $completedExpanded) {
                        ForEach(completed) { reminder in
                            SavedFollowUpRow(reminder: reminder, model: model)
                        }
                    }
                }
            }
        }
        // Persistence publishes the confirmed rows before they move between groups.
        .animation(reduceMotion ? nil : CompanionMotion.standard, value: model.savedFollowUps)
    }

    private var findingOptions: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("Look back", selection: Binding(
                get: { model.followUpWindow }, set: { model.setFollowUpWindow($0) }
            )) {
                ForEach(FollowUpWindow.allCases) { Text($0.label).tag($0) }
            }
            .disabled(model.followUpPhase.isRunning)
            Picker("Source", selection: Binding(
                get: { model.followUpSource }, set: { model.setFollowUpSource($0) }
            )) {
                ForEach(MemorySource.appSelectable, id: \.rawValue) { Text($0.label).tag($0) }
            }
            .disabled(model.followUpPhase.isRunning)
            Text("Uses previously prepared conversations for this period, not just the chat you last opened. Finding follow-ups does not prepare or sync new content.")
            if model.followUpSource == .archive {
                Text("Only messages with supported sender and time information are checked. Attachments and messages without attribution are excluded.")
            }
            Button("Prepare conversations…") { Task { await model.openFollowUpMemorySettings() } }
                .disabled(!model.canOpenFollowUpMemorySettings)
            if !model.canOpenFollowUpMemorySettings && model.memorySyncPhase.isRunning {
                Text("Preparation is already running. Wait for it to finish before switching conversations.")
            }
            if case .failed(let failure) = model.followUpPhase { Text(failure.message) }
        }
        .padding(.top, 8)
    }

    @ViewBuilder
    private var candidateState: some View {
        switch model.followUpPhase {
        case .idle:
            Text("Choose a period, then find follow-ups.").foregroundStyle(.secondary)
        case .running:
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Looking for follow-ups…").foregroundStyle(.secondary)
            }
        case .failed(let failure):
            VStack(alignment: .leading, spacing: 8) {
                Text(followUpFailureMessage(failure)).foregroundStyle(.secondary)
                if case .memoryUnavailable = failure {
                    Button("Prepare conversations…") { Task { await model.openFollowUpMemorySettings() } }
                        .disabled(!model.canOpenFollowUpMemorySettings)
                    Text("In Settings, prepare the saved conversations you want to use. Then return here and choose Find follow-ups.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        case .ready:
            if let snapshot = model.followUpSnapshot {
                FollowUpSnapshotView(snapshot: snapshot, candidateToSave: $candidateToSave)
            }
        }
    }

    private func followUpFailureMessage(_ failure: FollowUpFailure) -> String {
        switch failure {
        case .consentWithheld: "Turn on local storage to find follow-ups."
        case .runnerUnavailable: "Finding follow-ups is unavailable in this build."
        case .memoryUnavailable: "These conversations need to be prepared before finding follow-ups."
        case .workerFailed: "Could not find follow-ups. Try again, or check the details below."
        }
    }

    private func reminderStoreMessage(_ error: ReminderStoreError) -> String {
        switch error {
        case .unsupportedVersion:
            "The saved follow-up file was written by an unsupported version and was left untouched."
        case .malformed:
            "The saved follow-up file could not be read and was left untouched."
        case .unavailable:
            "Saved follow-ups are temporarily unavailable."
        }
    }
}

private struct SavedFollowUpRow: View {
    let reminder: SavedFollowUp
    @Bindable var model: AppModel
    @State private var isConfirmingDeletion = false
    @State private var isShowingDetails = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: reminder.status == .completed ? "checkmark.circle" : "circle")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(reminder.text)
                    .font(.body)
                    .strikethrough(reminder.status == .completed)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Text(reminder.conversationLabel + " · " + (reminder.sender ?? "Unknown sender"))
                    .font(.caption).foregroundStyle(.secondary)
                if let note = FollowUpCoveragePresentation.message(for: reminder.coverageStatus) {
                    Text(note).font(.caption).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 8) {
                Button(reminder.status == .completed ? "Reopen" : "Mark Done") {
                    Task {
                        await model.setSavedFollowUpStatus(
                            reminder.id,
                            status: reminder.status == .completed ? .pending : .completed
                        )
                    }
                }
                Menu("More", systemImage: "ellipsis") {
                    if reminder.source == .archive, reminder.archiveEvidence != nil {
                        Button("Show original message") {
                            Task { await model.openSavedFollowUpEvidence(reminder.id) }
                        }
                    }
                    Button("Details…") { isShowingDetails = true }
                    Button("Delete…", role: .destructive) { isConfirmingDeletion = true }
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
            }
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .contain)
        .accessibilityValue(reminder.status == .completed ? "Completed" : "Pending")
        .sheet(isPresented: $isShowingDetails) {
            VStack(alignment: .leading, spacing: 12) {
                Text("Follow-up details").font(.headline)
                ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                Text(provenanceText).font(.callout).foregroundStyle(.secondary)
                ForEach(Array(reminder.coverageCaveats.enumerated()), id: \.offset) { _, caveat in
                    Text(caveat).font(.callout).foregroundStyle(.secondary)
                }
                Text("Saved " + reminder.savedAt.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption).foregroundStyle(.secondary)
                Text("No due date or notification.").font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                }
                Button("Done") { isShowingDetails = false }.keyboardShortcut(.defaultAction)
            }
            .padding(24)
            .frame(minWidth: 320, idealWidth: 440, minHeight: 240, idealHeight: 360, maxHeight: 520)
        }
        .confirmationDialog("Delete this follow-up?", isPresented: $isConfirmingDeletion) {
            Button("Delete", role: .destructive) {
                Task { await model.deleteSavedFollowUp(reminder.id) }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var provenanceText: String {
        let sender = reminder.sender ?? "Unknown sender"
        return "\(reminder.source.label) · \(reminder.conversationLabel) · \(sender) · "
            + reminder.evidenceTimestamp.formatted(date: .abbreviated, time: .shortened)
            + " · Coverage: "
            + reminder.coverageStatus.replacingOccurrences(of: "_", with: " ").capitalized
            + " · Scan: "
            + reminder.scanWindowStart.formatted(date: .abbreviated, time: .shortened)
            + " – "
            + reminder.scanWindowEnd.formatted(date: .abbreviated, time: .shortened)
            + " · Timestamp: "
            + reminder.evidenceTimestampKind.replacingOccurrences(of: "_", with: " ")
    }
}

private struct FollowUpSnapshotView: View {
    let snapshot: FollowUpCandidateSnapshot
    @Binding var candidateToSave: FollowUpCandidate?

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if snapshot.coverage.status != "complete" || snapshot.truncated {
                Text(snapshot.coverage.status == "complete"
                    ? "Some messages or suggestions may be missing from this search."
                    : (FollowUpCoveragePresentation.message(for: snapshot.coverage.status) ?? ""))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if snapshot.textTruncatedCount > 0 {
                Text("Some suggestions were shortened and cannot be saved.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            DisclosureGroup("Search details") {
                VStack(alignment: .leading, spacing: 8) {
                    summaryRow("Source", snapshot.source.label)
                    summaryRow("Period", windowText)
                    summaryRow("History", coverageLabel)
                    summaryRow("Messages checked", String(snapshot.scannedMessages))
                    if let lastSync = snapshot.freshness?.lastSuccessfulSync {
                        summaryRow("Last prepared", lastSync.formatted(date: .abbreviated, time: .shortened))
                    }
                    if snapshot.truncated {
                        Text("The search checks the newest 200 messages and returns at most 50 suggestions.")
                    }
                    ForEach(snapshot.coverage.caveats, id: \.self) { Text($0) }
                }
                .font(.caption).foregroundStyle(.secondary)
            }
            .font(.caption).foregroundStyle(.secondary)

            candidates
        }
    }

    @ViewBuilder
    private var candidates: some View {
        if snapshot.candidates.isEmpty {
            if snapshot.coverage.trustworthyEmpty {
                ContentUnavailableView(
                    "No follow-ups found",
                    systemImage: "checkmark.circle",
                    description: Text("No clear requests or commitments were found in the prepared conversations for this period.")
                )
            } else {
                ContentUnavailableView(
                    "No follow-ups found in the available messages",
                    systemImage: "questionmark.circle",
                    description: Text("Only part of the conversation history was available. There may be other follow-ups in messages not checked here.")
                )
            }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Text("Suggested follow-ups").font(.headline)
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(snapshot.candidates) { candidate in
                        FollowUpCandidateRow(
                            candidate: candidate,
                            conversationLabel: conversationLabel(for: candidate),
                            coverageStatus: snapshot.coverage.status,
                            scanWindowStart: snapshot.start,
                            scanWindowEnd: snapshot.end,
                            save: { candidateToSave = candidate }
                        )
                        if candidate.id != snapshot.candidates.last?.id {
                            Divider()
                        }
                    }
                }
                .padding(.vertical, 4)
            }
        }
    }

    private func conversationLabel(for candidate: FollowUpCandidate) -> String {
        snapshot.conversations.first(where: { $0.id == candidate.conversationIndex })?.label
            ?? "Conversation"
    }

    private var coverageLabel: String {
        switch snapshot.coverage.status {
        case "complete": "Complete"
        case "partial": "Partial"
        case "unavailable": "Unavailable"
        case "not_observed": "Not observed"
        default: snapshot.coverage.status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private var windowText: String {
        snapshot.start.formatted(date: .abbreviated, time: .shortened)
            + " – "
            + snapshot.end.formatted(date: .abbreviated, time: .shortened)
    }

    private func summaryRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 8)
    }
}

private struct FollowUpCandidateRow: View {
    let candidate: FollowUpCandidate
    let conversationLabel: String
    let coverageStatus: String
    let scanWindowStart: Date
    let scanWindowEnd: Date
    let save: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(candidate.text)
                .font(.body).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            Text(conversationLabel + " · " + (candidate.sender ?? "Unknown sender"))
                .font(.caption).foregroundStyle(.secondary)
            if let note = FollowUpCoveragePresentation.message(for: coverageStatus) {
                Text(note).font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("Keep follow-up", action: save).disabled(candidate.textTruncated)
                if candidate.textTruncated {
                    Text("Shortened message · cannot be saved")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            DisclosureGroup("Message details") {
                Text(reasonText)
                Text(candidateProvenanceText)
                Text(candidate.timestamp.formatted(date: .abbreviated, time: .shortened))
            }
            .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 10)
    }

    private var reasonText: String {
        candidate.reasons.map(reasonLabel).joined(separator: " · ")
    }

    private var candidateProvenanceText: String {
        "Coverage: \(coverageStatus.replacingOccurrences(of: "_", with: " ").capitalized)"
            + " · Scan: "
            + scanWindowStart.formatted(date: .abbreviated, time: .shortened)
            + " – "
            + scanWindowEnd.formatted(date: .abbreviated, time: .shortened)
            + " · Timestamp: "
            + candidate.timestampKind.replacingOccurrences(of: "_", with: " ")
    }

    private func reasonLabel(_ reason: String) -> String {
        switch reason {
        case "explicit_request": "Explicit request"
        case "explicit_follow_up": "Follow-up action"
        case "explicit_commitment": "Explicit commitment"
        case "action_question": "Action question"
        case "time_reference": "Time reference (not a due date)"
        default: reason.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

/// B6 local search.
///
/// The query lives only in `@State` and in the model for the current session.
/// Nothing here writes it to UserDefaults, the message database, or any log,
/// so relaunching the app cannot restore a previous search. The index behind
/// the results is derived, in-memory, and rebuilt from canonical evidence on
/// demand -- this view only ever shows what the store still retains.
private struct SearchView: View {
    @Bindable var model: AppModel
    @State private var query = ""
    /// Session-only. Like `query` this never leaves SwiftUI state, so a
    /// relaunch cannot restore the last search.
    @State private var filter: LocalSearchFilter = .all

    private var trimmed: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Search")
                    .font(.largeTitle.weight(.semibold))

                Text("Search messages saved on this Mac")
                    .font(.callout)
                    .foregroundStyle(.secondary)

                searchField
                DisclosureGroup("Search options") { sourceFilter }

                Divider()
                results

                Text("Search covers only evidence currently retained on this Mac.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Search")
        .onAppear(perform: consumeArchiveSearchRequest)
        .onChange(of: model.archiveSearchRequest) { _, _ in
            consumeArchiveSearchRequest()
        }
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            TextField("Search local messages", text: $query)
                .textFieldStyle(.roundedBorder)
                .onSubmit(submit)
            Button("Search", action: submit)
                .disabled(trimmed.isEmpty)
            if !query.isEmpty {
                Button("Clear") {
                    query = ""
                    filter = .all
                    model.clearLocalSearch()
                }
            }
        }
    }

    private var sourceFilter: some View {
        Picker("Source", selection: Binding(
            get: { filter },
            set: { value in
                guard filter != value else { return }
                filter = value
                if !trimmed.isEmpty { submit() }
            }
        )) {
            ForEach(LocalSearchFilter.allCases, id: \.self) { option in
                Text(option.label).tag(option)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }

    @ViewBuilder
    private var results: some View {
        switch model.localSearch.status {
        case .storageDisabled:
            ContentUnavailableView(
                "Local search is off",
                systemImage: "lock",
                description: Text(
                    "Enable local message storage in Settings to search the evidence kept on this Mac."
                )
            )
        case .storeUnavailable:
            ContentUnavailableView(
                "Local search is unavailable",
                systemImage: "exclamationmark.triangle",
                description: Text("The local message store could not be opened.")
            )
        case .preparing:
            HStack(spacing: 8) {
                ProgressView()
                Text("Preparing local search index…")
                    .foregroundStyle(.secondary)
            }
        case .failed:
            ContentUnavailableView(
                "Search could not run",
                systemImage: "exclamationmark.triangle",
                description: Text("The local search index could not be prepared on this Mac.")
            )
        case .idle:
            Text("Enter a few words to search the message text stored on this Mac.")
                .foregroundStyle(.secondary)
        case .noMatches:
            VStack(alignment: .leading, spacing: 4) {
                Text("No matches in currently stored local evidence.")
                if model.localSearch.indexedDocumentCount > 0 {
                    Text("\(model.localSearch.indexedDocumentCount) messages indexed.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        case .results(let count):
            VStack(alignment: .leading, spacing: 8) {
                Text("\(count) matches")
                    .font(.callout.weight(.medium))
                ForEach(model.localSearch.results) { result in
                    Button {
                        Task { await model.openSearchResult(result) }
                    } label: {
                        LocalSearchResultRow(result: result)
                    }
                    .buttonStyle(.plain)
                    Divider()
                }
                if model.localSearch.results.count
                    == LocalMessageSearchIndex.maximumResults
                {
                    Text("Showing the first \(LocalMessageSearchIndex.maximumResults) matches.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func consumeArchiveSearchRequest() {
        guard let request = model.consumeArchiveSearchRequest() else { return }
        query = request.query
        filter = request.filter
        submit()
    }

    private func submit() {
        let value = trimmed
        guard !value.isEmpty else { return }
        Task { @MainActor in
            await model.searchLocalMessages(value, filter: filter)
        }
    }
}

/// One search result. Shows provenance in words, never an internal identifier,
/// a stored path, or a content hash.
private struct LocalSearchResultRow: View {
    let result: LocalSearchResult

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(result.conversationLabel == "Imported WeChat Archive" ? "Saved conversation" : result.conversationLabel)
                .font(.callout.weight(.medium))
            HStack(spacing: 6) {
                if let sender = result.sender {
                    Text(sender)
                } else if result.provenance == .archiveUnattributed {
                    Text("Sender and message time unavailable")
                }
                if let timestamp = result.timestamp {
                    Text((result.provenance == .visualCaptured ? "First seen " : "")
                        + timestamp.formatted(date: .abbreviated, time: .shortened))
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            if !result.excerpt.isEmpty {
                Text(result.excerpt).font(.callout).lineLimit(3)
            }
        }
        .padding(.vertical, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// On-Device Archive Answer v1. One question, one window, one answer.
///
/// The question buffer is this view's own @State, exactly as Search's query
/// is: it is not on the model, so nothing can write it to disk and a relaunch
/// has nothing to restore.
private struct AgentsView: View {
    @Bindable var model: AppModel
    @State private var question = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Agents")
                        .font(.largeTitle.bold())
                    Text("One question about already-synced Archive Memory, answered on this Mac. No API key, no remote processing, and nothing kept after you quit.")
                        .foregroundStyle(.secondary)
                }

                if let reason = model.answerAvailability.reason {
                    unavailable(reason)
                } else {
                    controls
                    state
                }
            }
            .padding(24)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .navigationTitle("Agents")
        .onAppear {
            // Availability is ephemeral system state, so it is re-read when
            // the surface appears rather than only at launch.
            model.refreshAnswerRuntimeAvailability()
        }
        .task {
            await model.loadArchiveSnapshots()
        }
    }

    private func unavailable(_ reason: AnswerRuntimeUnavailableReason) -> some View {
        ContentUnavailableView {
            Label("On-Device Answer Is Unavailable", systemImage: "cpu")
        } description: {
            Text(reason.message)
        } actions: {
            Text("This feature uses only the model built into macOS. It has no provider, key or remote fallback.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }

    private var controls: some View {
        GroupBox("Question") {
            VStack(alignment: .leading, spacing: 12) {
                TextField("Ask about the selected window", text: $question, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...3)
                    .accessibilityLabel("Question about the Archive")
                    .disabled(model.answerPhase.isRunning)

                HStack {
                    Menu {
                        Button {
                            model.setAnswerConversation(nil)
                        } label: {
                            if model.selectedArchiveConversationID == nil {
                                Label("All Archive snapshots", systemImage: "checkmark")
                            } else {
                                Text("All Archive snapshots")
                            }
                        }
                        ForEach(model.archiveSnapshots) { snapshot in
                            Button {
                                model.setAnswerConversation(snapshot)
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(snapshot.label)
                                        Text(snapshot.rangeLabel).font(.caption2)
                                    }
                                    if snapshot.id == model.selectedArchiveConversationID {
                                        Image(systemName: "checkmark")
                                    }
                                }
                            }
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(model.archiveSnapshots.first(where: {
                                $0.id == model.selectedArchiveConversationID
                            })?.label ?? "All Archive snapshots")
                            if let snapshot = model.archiveSnapshots.first(where: {
                                $0.id == model.selectedArchiveConversationID
                            }) {
                                Text(snapshot.rangeLabel).font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .disabled(model.answerPhase.isRunning)
                    .accessibilityLabel("Archive scope")

                    Menu(model.answerWindow.label) {
                        ForEach(DailySummaryWindow.allCases) { window in
                            Button {
                                model.setAnswerWindow(window)
                            } label: {
                                if window == model.answerWindow {
                                    Label(window.label, systemImage: "checkmark")
                                } else {
                                    Text(window.label)
                                }
                            }
                        }
                    }
                    .disabled(model.answerPhase.isRunning)
                    .accessibilityLabel("Time window")

                    Spacer()

                    if model.isAnswerRunActive {
                        Button("Cancel", role: .cancel) {
                            model.cancelArchiveAnswer()
                        }
                    } else {
                        Button("Ask", systemImage: "sparkle") {
                            let asked = question
                            Task { await model.askArchiveQuestion(asked) }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!model.canAskArchiveQuestion || question.isEmpty)
                    }
                }
            }
            .padding(.vertical, 6)
        }
    }

    @ViewBuilder
    private var state: some View {
        switch model.answerPhase {
        case .idle:
            ContentUnavailableView(
                "No Answer Yet",
                systemImage: "text.bubble",
                description: Text("Ask one question about the selected window. There is no history: the answer is kept only while the app stays open.")
            )
        case .running:
            HStack(spacing: 10) {
                if model.answerSnapshot != nil {
                    CompanionAIActivityIndicator()
                } else {
                    ProgressView().controlSize(.small)
                }
                Text(model.answerSnapshot == nil
                    ? "Preparing saved messages…" : "Waiting for the on-device answer…")
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 12)
        case .cancelled:
            notice("Cancelled", "The run was cancelled. No answer was kept.", "xmark.circle")
        case .timedOut:
            notice("Timed Out", "The on-device model took too long and was stopped.", "clock.badge.xmark")
        case .failed(let failure):
            notice("No Answer", failure.message, "exclamationmark.triangle")
        case .answered:
            if let result = model.answerResult {
                answer(result)
            }
        }
    }

    private func notice(_ title: String, _ message: String, _ symbol: String) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: symbol)
        } description: {
            Text(message)
        }
    }

    private func answer(_ result: AnswerRunResult) -> some View {
        let disclosure = model.answerSnapshot.map {
            AnswerEvidenceDisclosure(snapshot: $0, modelInput: result.input)
        }
        return VStack(alignment: .leading, spacing: 20) {
            GroupBox("Answer") {
                VStack(alignment: .leading, spacing: 12) {
                    Text(result.answer)
                        .textSelection(.enabled)
                    if result.disposition == .insufficientEvidence {
                        Label(
                            "The evidence supplied for this window did not answer the question.",
                            systemImage: "info.circle"
                        )
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    } else if !result.hasVerifiableSource {
                        Label(
                            "No verifiable source: no supplied evidence row backed this answer, so it is not grounded in the Archive.",
                            systemImage: "exclamationmark.triangle"
                        )
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 6)
            }

            if result.hasVerifiableSource {
                GroupBox("Sources") {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(result.citations) { citation in
                            Button {
                                Task { await model.openArchiveAnswerCitation(citation) }
                            } label: {
                                HStack(alignment: .firstTextBaseline, spacing: 8) {
                                    Text("[\(citation.token)]")
                                        .monospacedDigit()
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(citation.text)
                                            .lineLimit(2)
                                        Text(
                                            (citation.sender ?? "Unknown sender") + " · "
                                                + citation.timestamp.formatted(
                                                    date: .abbreviated, time: .shortened
                                                )
                                        )
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                }
                                .contentShape(.rect)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(
                                "Reveal source \(citation.token) in the Archive"
                            )
                            if citation.id != result.citations.last?.id {
                                Divider()
                            }
                        }
                    }
                    .padding(.vertical, 6)
                }
            }

            if let disclosure, !disclosure.lines.isEmpty {
                GroupBox("Evidence Completeness") {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(disclosure.lines, id: \.self) { line in
                            Label(line, systemImage: "info.circle")
                        }
                    }
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 6)
                }
            }

            if let snapshot = model.answerSnapshot {
                let coverage = AnswerCoverageDisclosure(
                    coverage: snapshot.coverage, rowCount: snapshot.rows.count
                )
                GroupBox("Source Window") {
                    VStack(spacing: 0) {
                        summaryRow(
                            "Window",
                            snapshot.start.formatted(date: .abbreviated, time: .shortened)
                                + " – "
                                + snapshot.end.formatted(date: .abbreviated, time: .shortened)
                        )
                        Divider()
                        summaryRow("Coverage", coverage.statusLabel)
                        Divider()
                        summaryRow("Rows read", String(snapshot.returnedEvidence))
                        if let freshness = snapshot.freshness,
                           let lastSync = freshness.lastSuccessfulSync {
                            Divider()
                            summaryRow(
                                "Last Memory sync",
                                lastSync.formatted(date: .abbreviated, time: .shortened)
                            )
                        }
                    }
                    .padding(.vertical, 4)
                }

                if !coverage.lines.isEmpty {
                    GroupBox("Window Coverage") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(coverage.lines, id: \.self) { line in
                                Label(line, systemImage: "info.circle")
                            }
                        }
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 6)
                    }
                }
            }
        }
    }

    private func summaryRow(_ label: String, _ value: String) -> some View {
        LabeledContent(label) {
            Text(value)
                .foregroundStyle(.secondary)
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

// MARK: - Consumer navigation (presentation only)

private struct HomeView: View {
    @Bindable var model: AppModel
    @State private var query = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Home").font(.largeTitle.weight(.semibold))
                Text("Find, read and revisit your WeChat conversations.")
                    .foregroundStyle(.secondary)
                HStack {
                    TextField("Search saved messages or people", text: $query)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(search)
                        .accessibilityIdentifier("home.search")
                    Button("Search", systemImage: "magnifyingglass", action: search)
                        .buttonStyle(.borderedProminent)
                        .disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 20) { homeActions }
                    VStack(alignment: .leading, spacing: 12) { homeActions }
                }
                .buttonStyle(.link)
                Text("Recent conversations").font(.title2.weight(.semibold))
                ConsumerConversationList(model: model, recentOnly: true)
                    .frame(minHeight: 300)
                if model.archiveEvidence.storeState == .disabled {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Start with a conversation you choose to share.").font(.headline)
                        Text("In WeChat, share a conversation export to WeChat Companion. Enable local storage to keep and read it here.")
                            .foregroundStyle(.secondary)
                        Button("Set up local storage") { model.selectedDestination = .settings }
                    }
                } else if model.archiveEvidence.storeState == .unavailable {
                    Label("Saved conversations are unavailable. Open Settings to check local storage.", systemImage: "exclamationmark.triangle")
                    Button("Open Settings") { model.selectedDestination = .settings }
                }
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .navigationTitle("Home")
    }

    @ViewBuilder private var homeActions: some View {
        Button("Today’s summary", systemImage: "text.document") { model.openArchiveDailySummary() }
        Button("Follow-ups", systemImage: "checklist") { model.selectedDestination = .reminders }
    }

    private func search() {
        let value = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        model.beginConsumerSearch(value)
    }
}

/// Each row is one observation. A title never merges exports or sources.
/// The aggregate readers do not provide last-message text, so no preview is invented.
private struct ConsumerConversationList: View {
    @Bindable var model: AppModel
    var recentOnly = false
    @State private var nameFilter = ""

    var body: some View {
        VStack(spacing: 0) {
            if !recentOnly {
                TextField("Find a conversation", text: $nameFilter)
                    .textFieldStyle(.roundedBorder)
                    .padding(12)
                    .accessibilityIdentifier("chats.find")
            }
            if recentOnly {
                List(rows) { row in
                    Button { open(row.id) } label: { CompanionConversationRow(row: row) }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Open \(row.accessibilityDescription)")
                }
                .listStyle(.plain)
            } else {
                // Selection belongs to AppModel. Native List handles keyboard/focus;
                // no second selected-conversation owner and no synthetic reveal anchor.
                List(rows, selection: Binding<ConsumerConversationID?>(
                    get: { selectedID },
                    set: { id in if let id { open(id) } }
                )) { row in
                    CompanionConversationRow(row: row)
                        .tag(row.id)
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel(row.accessibilityDescription)
                        .accessibilityAction(named: "Open conversation") { open(row.id) }
                        .contextMenu {
                            Button("Open conversation") { open(row.id) }
                        }
                }
                .listStyle(.plain)
            }
        }
        .overlay(alignment: .center) {
            if rows.isEmpty {
                ContentUnavailableView(
                    nameFilter.isEmpty ? "No saved conversations" : "No matching conversations",
                    systemImage: nameFilter.isEmpty ? "bubble.left.and.bubble.right" : "magnifyingglass",
                    description: Text(nameFilter.isEmpty ? "Conversations you share from WeChat appear here." : "Try another name, or search saved messages.")
                )
                .allowsHitTesting(false)
            }
        }
    }

    private var rows: [ConsumerConversationRow] {
        let filtered = ConsumerConversationRow.rows(archive: model.archiveEvidence, visual: model.captureLedger)
            .filter { nameFilter.isEmpty || $0.title.localizedCaseInsensitiveContains(nameFilter) }
        return recentOnly ? Array(filtered.prefix(5)) : filtered
    }

    private var selectedID: ConsumerConversationID? {
        if let id = model.selectedVisualConversationID { return .visualConversation(id) }
        if let id = model.selectedArchiveImportID { return .archiveImport(id) }
        return nil
    }

    private func open(_ id: ConsumerConversationID) {
        model.selectedDestination = .chats
        Task {
            switch id {
            case .archiveImport(let importID): await model.selectArchiveImport(importID)
            case .visualConversation(let id): await model.selectVisualConversation(id)
            }
        }
    }
}

/// Open Row: no per-conversation surface, border or status badge.
private struct CompanionConversationRow: View {
    let row: ConsumerConversationRow
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if !dynamicTypeSize.isAccessibilitySize {
                Text(String(row.title.prefix(1)).uppercased())
                    .font(.headline)
                    .frame(width: 34, height: 34)
                    .background(.quaternary, in: Circle())
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 4) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(row.title).font(.body.weight(.medium))
                            .fixedSize(horizontal: true, vertical: false)
                        Spacer(minLength: 4)
                        timestamp
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        Text(row.title).font(.body.weight(.medium))
                            .fixedSize(horizontal: false, vertical: true)
                        timestamp
                    }
                }
                Text(row.note).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    private var timestamp: some View {
        Text(row.date.formatted(date: .abbreviated, time: .omitted))
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize()
    }
}

private struct SettingsView: View {
    @Bindable var model: AppModel
    @State private var advancedExpanded = false
    @State private var preparationExpanded = false

    var body: some View {
        ScrollViewReader { proxy in
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Settings").font(.largeTitle.weight(.semibold))
                Text("Privacy & storage").font(.title2.weight(.semibold))
                GroupBox("Local Message Storage") {
                    VStack(alignment: .leading, spacing: 0) {
                        Toggle(
                            "Keep shared conversations and extracted text on this Mac",
                            isOn: Binding(
                                get: { model.allowsLocalPersistence },
                                set: { newValue in
                                    Task { await model.setAllowsLocalPersistence(newValue) }
                                }
                            )
                        )
                        .padding(.vertical, 10)
                        Divider()
                        Text("Off by default. Shared conversations wait until you enable storage. Enabling it may finish pending shares. Text you choose to extract can also be kept; screenshots are never saved. Remote processing requires separate consent.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .padding(.vertical, 10)
                        Divider()
                        Picker("Keep messages for", selection: Binding(
                            get: { model.retentionPolicy },
                            set: { newValue in
                                Task { await model.setRetentionPolicy(newValue) }
                            }
                        )) {
                            ForEach(RetentionPolicy.allCases) { option in
                                Text(option.label).tag(option)
                            }
                        }
                        .disabled(!model.allowsLocalPersistence)
                        .padding(.vertical, 10)
                        Divider()
                        Text("Older messages are removed based on when they were first "
                            + "seen on screen.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .padding(.vertical, 10)
                        Divider()
                        HStack {
                            Button("Delete Local Message History\u{2026}", role: .destructive) {
                                model.isConfirmingHistoryDeletion = true
                            }
                            Text("Turning the setting off does not delete what is already "
                                + "stored.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 10)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .confirmationDialog(
                    "Delete all locally stored WeChat message history?",
                    isPresented: $model.isConfirmingHistoryDeletion,
                    titleVisibility: .visible
                ) {
                    Button("Delete Message History", role: .destructive) {
                        Task { await model.deleteLocalMessageHistory() }
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("This permanently removes every stored conversation and message "
                        + "from this Mac. Your API key, permissions, and diagnostics are "
                        + "not affected.")
                }

                Text("Shared conversations are saved on this Mac. Optional remote processing requires separate consent in Advanced.")
                    .font(.callout).foregroundStyle(.secondary)
                DisclosureGroup("Advanced", isExpanded: $advancedExpanded) {
                    VStack(alignment: .leading, spacing: 16) {
                        DisclosureGroup("Sources & saved exports") { AdvancedChatsView(model: model) }
                        DisclosureGroup("Capture & acquisition — optional") { OverviewView(model: model) }
                        DisclosureGroup("Processing & prepared summaries", isExpanded: Binding(
                            get: { preparationExpanded || model.hasMemorySettingsRequest },
                            set: { preparationExpanded = $0 }
                        )) { AdvancedSettingsView(model: model) }
                        Button("On-device questions") { model.selectedDestination = .agents }
                        Button("Diagnostics") { model.selectedDestination = .diagnostics }
                        Text("Database acquisition remains unavailable until a supported access route is established.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(.top, 12)
                }
                .accessibilityIdentifier("settings.advanced")
                Text("WeChat Companion · A read-only companion for conversations you choose to keep.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding(24)
        }
        .navigationTitle("Settings")
        .task(id: model.hasMemorySettingsRequest) {
            guard model.hasMemorySettingsRequest else { return }
            advancedExpanded = true
            preparationExpanded = true
            await Task.yield()
            if model.consumeMemorySettingsRequest() {
                proxy.scrollTo("memory", anchor: .top)
            }
        }
        }
    }
}

#if DEBUG
/// Isolated production-view previews: no bootstrap, canonical store, credentials or worker.
private struct ConsumerPreviewSurface: View {
    let destination: Destination
    @State private var model: AppModel
    private let history: LocalMessageHistory

    init(destination: Destination, searchFrom: Destination? = nil, followUpPreparationRequired: Bool = false) {
        self.destination = destination
        let history = LocalMessageHistory(url: nil)
        self.history = history
        let defaults = UserDefaults(suiteName: "ui-preview-\(UUID().uuidString)")!
        let model = AppModel(
            messageHistory: history, shareInbox: nil, credentials: PreviewCredentials(),
            consentDefaults: defaults, memorySync: UnavailableMemorySyncRunner(),
            dailySummary: UnavailableDailySummaryRunner(), followUpCandidates: UnavailableFollowUpRunner(),
            answerEvidence: UnavailableAnswerEvidenceRunner(), reminderStore: VolatileReminderStore()
        )
        model.configureConsumerPreview(destination: destination, followUpPreparationRequired: followUpPreparationRequired)
        if let searchFrom {
            model.selectedDestination = searchFrom
            model.selectedDestination = .search
        }
        _model = State(initialValue: model)
    }

    var body: some View {
        ContentView(model: model)
            .frame(width: 1000, height: 680)

    }
}

private struct PreviewCredentials: CredentialStoring {
    func save(_ secret: String, account: String) throws {}
    func secret(account: String) throws -> String? { nil }
    func remove(account: String) throws {}
}

#Preview("Companion Open Row — long text") {
    CompanionConversationRow(row: ConsumerConversationRow(
        id: .archiveImport(1),
        title: "A long conversation name that must remain readable in a narrow macOS column",
        date: Date(timeIntervalSince1970: 1_791_151_200),
        note: "Saved on this Mac · The secondary text also remains readable without a status badge"
    ))
    .environment(\.dynamicTypeSize, .accessibility3)
    .frame(width: 260)
    .padding(20)
    .background(Color(nsColor: .windowBackgroundColor))
    .preferredColorScheme(.light)
}
#Preview("Consumer Chats — synthetic") { ConsumerPreviewSurface(destination: .chats) }
#Preview("Consumer Follow-ups — synthetic") { ConsumerPreviewSurface(destination: .reminders) }
#Preview("Consumer Home — synthetic") { ConsumerPreviewSurface(destination: .overview) }
#Preview("Consumer Settings — isolated") { ConsumerPreviewSurface(destination: .settings) }
#Preview("Search from Chats — isolated") { ConsumerPreviewSurface(destination: .search, searchFrom: .chats) }
#Preview("Follow-ups preparation needed — isolated") { ConsumerPreviewSurface(destination: .reminders, followUpPreparationRequired: true) }
#endif

private struct ImportAttentionView: View {
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if model.archiveImportStatus.needsAttention {
                Label(model.archiveImportStatus.consumerMessage, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.secondary)
                if model.archiveImportStatus == .localPersistenceConsentRequired || model.archiveImportStatus == .localStoreUnavailable {
                    Button("Open Settings") { model.selectedDestination = .settings }
                }
            }
            if model.archiveAttachmentImportStatus.needsAttention,
               let message = model.archiveAttachmentImportStatus.message {
                Label(message, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
            }
        }
    }
}

/// Local semantic motion; no imported Kitchen Manager code or package dependency.
private enum CompanionMotion {
    static let standard: Animation = .smooth(duration: 0.28)
}

/// Genuine AI work only. A plain native wait mark; no invented reasoning stage.
private struct CompanionAIActivityIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if reduceMotion {
                Image(systemName: "sparkles").foregroundStyle(.secondary)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .accessibilityHidden(true) // Adjacent status text owns the spoken state.
    }
}
