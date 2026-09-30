import AppKit
import SwiftUI
import UniformTypeIdentifiers

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
            case .search:
                SearchView(model: model)
            case .dailySummary:
                DailySummaryView(model: model)
            case .reminders:
                RemindersView(model: model)
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
    @State private var archiveSearchText = ""
    @State private var highlightedAnchor: ContextRevealAnchor?
    @State private var highlightGeneration: UInt64 = 0

    var body: some View {
        ScrollViewReader { scroll in
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Chats")
                    .font(.largeTitle.weight(.semibold))

                let ledger = CaptureLedgerPresentation(ledger: model.captureLedger)

                CaptureLedgerSection(model: model, highlightedAnchor: highlightedAnchor)
                    .id("captured")

                ArchiveEvidenceBrowser(
                    model: model,
                    searchText: $archiveSearchText,
                    isChoosingArchive: $isChoosingArchive,
                    highlightedAnchor: highlightedAnchor
                )
                .id("archives")

                CaptureStatusDisclosure(model: model, ledger: ledger)

                DiagnosticsDisclosure(model: model)

                Spacer(minLength: 0)
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Chats")
        .onAppear {
            guard model.contextRevealRequest == nil else { return }
            switch model.contextNavigationTarget {
            case .archiveImport: scroll.scrollTo("archives", anchor: .top)
            case .visualConversation: scroll.scrollTo("visual-context", anchor: .top)
            case nil: break
            }
        }
        .onChange(of: model.contextNavigationTarget) { _, target in
            guard model.contextRevealRequest == nil else { return }
            switch target {
            case .archiveImport: scroll.scrollTo("archives", anchor: .top)
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

private struct ArchiveEvidenceBrowser: View {
    @Bindable var model: AppModel
    @Binding var searchText: String
    @Binding var isChoosingArchive: Bool
    let highlightedAnchor: ContextRevealAnchor?
    @State private var hasSubmittedSearch = false

    private var trimmedSearch: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("Archive Conversations")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button("Choose WeChat Export ZIP…") {
                    isChoosingArchive = true
                }
                .disabled(model.archiveImportStatus == .importing)
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

            if model.archiveEvidence.storeState == .ready {
                Text(model.archiveImportStatus.message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let attachmentMessage = model.archiveAttachmentImportStatus.message,
                model.archiveEvidence.storeState == .ready {
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
            TranscriptStateRow(state: .neverCaptured)
        } else {
            HStack(spacing: 8) {
                TextField("Search imported messages", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { submitSearch() }
                Button("Search") { submitSearch() }
                    .disabled(trimmedSearch.isEmpty)
                if hasSubmittedSearch {
                    Button("Clear") { clearSearch() }
                }
            }

            if hasSubmittedSearch && !model.searchHitUnavailable && !model.selectedArchiveIsHitWindow
                && model.contextRevealRequest == nil {
                searchResults
            } else {
                importsAndRecords
            }
        }
    }

    @ViewBuilder
    private var searchResults: some View {
        Divider()
        if model.archiveSearchResults.isEmpty {
            Text("No imported messages match “\(trimmedSearch)”.")
                .foregroundStyle(.secondary)
        } else {
            Text("\(model.archiveSearchResults.count) matching imported messages")
                .font(.callout.weight(.medium))
            ForEach(model.archiveSearchResults) { record in
                ArchiveEvidenceRecordRow(record: record, showsImportDate: true) {
                    Task { @MainActor in
                        await model.selectArchiveImport(record.importID)
                        searchText = ""
                        await model.searchArchiveEvidence("")
                        hasSubmittedSearch = false
                    }
                }
                Divider()
            }
            if model.archiveSearchResults.count == 100 {
                Text("Showing the first 100 matches.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var importsAndRecords: some View {
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

        if let selected = model.archiveEvidence.imports.first(where: {
            $0.id == model.selectedArchiveImportID
        }) {
            Divider()
            VStack(alignment: .leading, spacing: 4) {
                TranscriptContextHeader(
                    title: selected.displayName ?? "Imported WeChat Archive",
                    summary: "Archive · imported "
                        + selected.importedAt.formatted(date: .abbreviated, time: .shortened)
                        + " · \(selected.recordCount) records"
                        + (selected.attachmentCount > 0
                            ? " · \(selected.attachmentCount) attachments" : "")
                        + (selected.isAnonymous
                            ? (selected.link == nil ? " · Unlinked export" : " · Explicitly linked")
                            : ""),
                    caveat: transcriptWindowNote(summary: selected)
                )
            }

            // Naming and linking are real controls, but they are not what the
            // reader came for. They stay one disclosure away instead of
            // sitting between the header and the transcript at full weight.
            DisclosureGroup("Conversation details") {
                ArchiveLinkControls(model: model, summary: selected)
            }
            .font(.callout.weight(.medium))

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
        let query = trimmedSearch
        Task { @MainActor in
            await model.searchArchiveEvidence(query)
            hasSubmittedSearch = true
        }
    }

    private func clearSearch() {
        Task { @MainActor in
            searchText = ""
            await model.searchArchiveEvidence("")
            hasSubmittedSearch = false
        }
    }

    /// The one honest thing the reader still needs to know about how much of
    /// the import is on screen, said once in the header instead of under every
    /// row.
    private func transcriptWindowNote(summary: ArchiveEvidenceImportSummary) -> String {
        if model.selectedArchiveIsHitWindow {
            return "Showing a window of up to 500 records around the search result."
        }
        if summary.recordCount > model.selectedArchiveRecords.count {
            return "Showing the first \(model.selectedArchiveRecords.count) of "
                + "\(summary.recordCount) records."
        }
        return summary.shape.label + " import · read-only local evidence."
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

private struct ArchiveEvidenceRecordRow: View {
    let record: ArchiveEvidenceRecord
    var showsImportDate = false
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
    }

    private var content: some View {
        TranscriptRowView(
            row: TranscriptRow(archive: record),
            caption: showsImportDate
                ? "Imported \(record.importedAt.formatted(date: .abbreviated, time: .shortened))"
                : nil
        )
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
                Text(row.sender)
                    .font(.callout.weight(.medium))
                Spacer(minLength: 12)
                if let literalTime = row.literalTime {
                    Text(row.timeLabel ?? literalTime)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if let shortTime = row.shortTime {
                    Text(shortTime)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Text(row.body)
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
                            isHighlighted: highlightedAnchor == row.id
                        )
                        Divider()
                    }
                }
                .padding(.bottom, 6)
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
    @State private var hoveredConversationID: Int64?

    private var presentation: CaptureLedgerPresentation {
        CaptureLedgerPresentation(ledger: model.captureLedger)
    }

    var body: some View {
        let shown = presentation
        VStack(alignment: .leading, spacing: 12) {
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

            Group {
                if model.visualContextUnavailable {
                    TranscriptStateRow(state: .contextVanished)
                } else if let selected = model.captureLedger.conversations.first(where: {
                    $0.id == model.selectedVisualConversationID
                }) {
                    Divider()
                    VStack(alignment: .leading, spacing: 10) {
                        let count = model.selectedVisualMessages.count
                        let windowNote = model.selectedVisualIsHitWindow
                            ? "Showing a window of up to 100 messages around the search result."
                            : "Showing the newest retained messages."
                        let summary = "Visual capture · \(count) retained messages · captured "
                            + firstCapturedRange(selected)
                        TranscriptContextHeader(
                            title: selected.title,
                            summary: summary,
                            caveat: "Times are first observed, not sent times. " + windowNote
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

                GroupBox("Local Message Storage") {
                    VStack(alignment: .leading, spacing: 0) {
                        Toggle(
                            "Save extracted message text on this Mac",
                            isOn: Binding(
                                get: { model.allowsLocalPersistence },
                                set: { newValue in
                                    Task { await model.setAllowsLocalPersistence(newValue) }
                                }
                            )
                        )
                        .padding(.vertical, 10)
                        Divider()
                        Text("Off by default, and separate from remote processing. When "
                            + "off, extraction still runs and results are shown here, but "
                            + "nothing is written to disk. Screenshots are never saved "
                            + "either way.")
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

                MemorySection(model: model)

                Spacer(minLength: 0)
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Settings")
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



private struct RemindersView: View {
    @Bindable var model: AppModel
    @State private var candidateToSave: FollowUpCandidate?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Reminders")
                        .font(.largeTitle.bold())
                    Text("Local follow-ups grounded in Memory evidence. No due date is inferred and no notification is scheduled in this phase.")
                        .foregroundStyle(.secondary)
                }

                savedFollowUps

                GroupBox("Find Follow-Up Candidates") {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Source")
                            Spacer()
                            Menu(model.followUpSource.label) {
                                ForEach(MemorySource.appSelectable, id: \.rawValue) { source in
                                    Button {
                                        model.setFollowUpSource(source)
                                    } label: {
                                        if source == model.followUpSource {
                                            Label(source.label, systemImage: "checkmark")
                                        } else {
                                            Text(source.label)
                                        }
                                    }
                                }
                            }
                            .disabled(model.followUpPhase.isRunning)
                        }

                        Divider()

                        HStack {
                            Text("Window")
                            Spacer()
                            Menu(model.followUpWindow.label) {
                                ForEach(FollowUpWindow.allCases) { window in
                                    Button {
                                        model.setFollowUpWindow(window)
                                    } label: {
                                        if window == model.followUpWindow {
                                            Label(window.label, systemImage: "checkmark")
                                        } else {
                                            Text(window.label)
                                        }
                                    }
                                }
                            }
                            .disabled(model.followUpPhase.isRunning)
                        }

                        Divider()

                        HStack {
                            Text("Scans already-synced Memory only. It never syncs Memory automatically.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Button(model.followUpSnapshot == nil ? "Scan Candidates" : "Scan Again") {
                                Task { await model.scanFollowUps() }
                            }
                            .disabled(!model.canScanFollowUps)
                        }
                    }
                    .padding(.vertical, 6)
                }

                if !model.allowsLocalPersistence {
                    ContentUnavailableView(
                        "Local Storage Is Off",
                        systemImage: "externaldrive.badge.xmark",
                        description: Text("Enable local message storage in Settings before Reminders can read Memory or save follow-ups.")
                    )
                } else {
                    candidateState
                }
            }
            .padding(24)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .navigationTitle("Reminders")
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
            Text("This saves the selected evidence as a local follow-up. It does not create a due date, notification, or system reminder.")
        }
    }

    @ViewBuilder
    private var savedFollowUps: some View {
        GroupBox("Saved Follow-Ups") {
            VStack(alignment: .leading, spacing: 12) {
                if let error = model.reminderStoreError {
                    Label(reminderStoreMessage(error), systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.secondary)
                } else if model.savedFollowUps.isEmpty {
                    Text("No confirmed follow-ups yet.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.savedFollowUps) { reminder in
                        SavedFollowUpRow(reminder: reminder, model: model)
                        if reminder.id != model.savedFollowUps.last?.id {
                            Divider()
                        }
                    }
                }
            }
            .padding(.vertical, 6)
        }
    }

    @ViewBuilder
    private var candidateState: some View {
        switch model.followUpPhase {
        case .idle:
            ContentUnavailableView(
                "Ready to Scan",
                systemImage: "checklist",
                description: Text("The conservative local rules look only for explicit requests, commitments, follow-up actions, and action questions.")
            )
        case .running:
            HStack(spacing: 10) {
                ProgressView()
                Text("Scanning Memory evidence…")
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 12)
        case .failed(let failure):
            ContentUnavailableView(
                "Follow-Up Candidates Unavailable",
                systemImage: "exclamationmark.triangle",
                description: Text(failure.message)
            )
        case .ready:
            if let snapshot = model.followUpSnapshot {
                FollowUpSnapshotView(
                    snapshot: snapshot,
                    candidateToSave: $candidateToSave
                )
            }
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

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Label(
                    reminder.status == .completed ? "Completed" : "Pending",
                    systemImage: reminder.status == .completed ? "checkmark.circle.fill" : "circle"
                )
                .font(.caption.weight(.semibold))
                Spacer()
                Text(reminder.savedAt.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text(reminder.text)
                .strikethrough(reminder.status == .completed)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)

            Text(provenanceText)
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack {
                Button(reminder.status == .completed ? "Reopen" : "Mark Done") {
                    Task {
                        await model.setSavedFollowUpStatus(
                            reminder.id,
                            status: reminder.status == .completed ? .pending : .completed
                        )
                    }
                }
                Button("Delete", role: .destructive) {
                    Task { await model.deleteSavedFollowUp(reminder.id) }
                }
                Spacer()
                Text("No due date · no notification")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
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
            GroupBox("Evidence Status") {
                VStack(spacing: 0) {
                    summaryRow("Source", snapshot.source.label)
                    Divider()
                    summaryRow("Window", windowText)
                    Divider()
                    summaryRow("Coverage", coverageLabel)
                    Divider()
                    summaryRow("Messages scanned", String(snapshot.scannedMessages))
                    Divider()
                    summaryRow("Candidates", String(snapshot.returnedCandidates))
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
                                "The scan is bounded to the newest 200 messages and at most 50 candidates.",
                                systemImage: "ellipsis.circle"
                            )
                        }
                        if snapshot.textTruncatedCount > 0 {
                            Label(
                                "\(snapshot.textTruncatedCount) long candidate(s) were clipped and cannot be saved in this MVP.",
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

            candidates
        }
    }

    @ViewBuilder
    private var candidates: some View {
        if snapshot.candidates.isEmpty {
            if snapshot.coverage.trustworthyEmpty {
                ContentUnavailableView(
                    "No Explicit Follow-Up Candidates",
                    systemImage: "checkmark.circle",
                    description: Text("Memory reports complete coverage for this window, but no stored message matched the conservative explicit request/commitment rules.")
                )
            } else {
                ContentUnavailableView(
                    "Insufficient Stored Evidence",
                    systemImage: "questionmark.circle",
                    description: Text("Coverage is incomplete, so an empty candidate list cannot be interpreted as “nothing needs follow-up.”")
                )
            }
        } else {
            GroupBox("Candidate Follow-Ups") {
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
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(conversationLabel)
                    .font(.headline)
                Spacer()
                Text(candidate.timestamp.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text(candidate.sender ?? "Unknown sender")
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)

            Text(reasonText)
                .font(.caption)
                .foregroundStyle(.secondary)

            Text(candidateProvenanceText)
                .font(.caption2)
                .foregroundStyle(.tertiary)

            Text(candidate.text)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack {
                if candidate.textTruncated {
                    Label("Clipped evidence cannot be saved", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Save Follow-Up", action: save)
                    .disabled(candidate.textTruncated)
            }
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

                Text("Local only · Searches currently stored evidence")
                    .font(.callout)
                    .foregroundStyle(.secondary)

                searchField
                sourceFilter

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
        Picker("Source", selection: $filter) {
            ForEach(LocalSearchFilter.allCases, id: \.self) { option in
                Text(option.label).tag(option)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .onChange(of: filter) { _, _ in
            // Re-run the same query under the new source filter. The query text
            // is unchanged, so this narrows the search rather than starting a
            // new one.
            if !trimmed.isEmpty { submit() }
        }
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
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(result.source.label)
                    .font(.caption.weight(.semibold))
                Text(result.conversationLabel)
                    .font(.callout.weight(.medium))
                if let linkState = result.linkState {
                    Text(linkState)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 6) {
                Text(result.provenance.label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let sender = result.sender {
                    Text(sender)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let timestamp = result.timestamp {
                    Text(timestamp.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if !result.excerpt.isEmpty {
                Text(result.excerpt)
                    .font(.callout)
                    .lineLimit(3)
            }
        }
        .padding(.vertical, 2)
        .frame(maxWidth: .infinity, alignment: .leading)
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
