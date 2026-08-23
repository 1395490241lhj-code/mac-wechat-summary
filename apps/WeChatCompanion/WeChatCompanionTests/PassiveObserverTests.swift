import Foundation
import Testing
@testable import WeChatCompanion

struct PassiveObserverTests {
    @Test
    func stateTracksPermissionVisibilityAndPause() {
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: false,
                screenRecordingGranted: true,
                wechatFrontmost: true
            ) == .observing
        )
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: false,
                screenRecordingGranted: true,
                wechatFrontmost: false
            ) == .waitingForWeChat
        )
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: true,
                screenRecordingGranted: true,
                wechatFrontmost: true
            ) == .paused
        )
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: false,
                screenRecordingGranted: false,
                wechatFrontmost: true
            ) == .permissionRequired
        )
    }

    @Test
    func fingerprintSkipsNoiseAndAcceptsMeaningfulChange() {
        let original = FrameFingerprint(samples: .init(repeating: 0, count: 256))
        let noise = FrameFingerprint(samples: .init(repeating: 8, count: 256))
        let changed = FrameFingerprint(samples: .init(repeating: 255, count: 32) + .init(repeating: 0, count: 224))

        #expect(!noise.isMeaningfullyDifferent(from: original))
        #expect(changed.isMeaningfullyDifferent(from: original))
    }

    @Test
    func captureGatePreventsBacklog() {
        var gate = ObserverCaptureGate()
        let firstBegin = gate.begin()
        let beginWhileBusy = gate.begin()
        gate.finish()
        let beginAfterFinish = gate.begin()

        #expect(firstBegin)
        #expect(!beginWhileBusy)
        #expect(beginAfterFinish)
        #expect(PassiveObserver.frameBufferLimit == 1)
    }

    @Test
    func metricsPersistenceContainsOnlyAggregateMetadata() throws {
        var metrics = PassiveObserverMetrics()
        metrics.framesSampled = 8
        metrics.meaningfulFramesObserved = 2
        metrics.duplicateFramesSkipped = 6
        let data = try JSONEncoder().encode(metrics)
        let json = String(decoding: data, as: UTF8.self)

        #expect(!json.contains("image"))
        #expect(!json.contains("fingerprint"))
        #expect(!json.contains("content"))
        #expect(!json.contains("title"))
    }

    @Test
    func eachSampleResolvesTheCurrentWindowAgain() throws {
        let source = try String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("WeChatCompanion/Capture/WeChatCaptureSource.swift"),
            encoding: .utf8
        )
        #expect(source.contains("let content = try await WeChatWindowLocator.shareableContent()"))
        #expect(!source.contains("private var window"))
    }
}
