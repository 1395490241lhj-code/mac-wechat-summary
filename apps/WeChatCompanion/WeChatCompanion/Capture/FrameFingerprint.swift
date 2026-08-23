import CoreGraphics
import Foundation

struct FrameFingerprint: Equatable, Sendable {
    static let side = 16
    static let noiseDelta = 12
    static let meaningfulChangedPixelRatio = 0.08

    private let grayscale: [UInt8]

    init?(image: CGImage) {
        var samples = [UInt8](repeating: 0, count: Self.side * Self.side)
        let rendered = samples.withUnsafeMutableBytes { bytes -> Bool in
            guard let baseAddress = bytes.baseAddress,
                  let context = CGContext(
                    data: baseAddress,
                    width: Self.side,
                    height: Self.side,
                    bitsPerComponent: 8,
                    bytesPerRow: Self.side,
                    space: CGColorSpaceCreateDeviceGray(),
                    bitmapInfo: CGImageAlphaInfo.none.rawValue
                  ) else { return false }
            context.interpolationQuality = .low
            context.draw(
                image,
                in: CGRect(x: 0, y: 0, width: Self.side, height: Self.side)
            )
            return true
        }
        guard rendered else { return nil }
        grayscale = samples
    }

    init(samples: [UInt8]) {
        precondition(samples.count == Self.side * Self.side)
        grayscale = samples
    }

    func changedPixelRatio(comparedTo other: FrameFingerprint) -> Double {
        let changed = zip(grayscale, other.grayscale).reduce(into: 0) { count, pair in
            if abs(Int(pair.0) - Int(pair.1)) > Self.noiseDelta { count += 1 }
        }
        return Double(changed) / Double(grayscale.count)
    }

    func isMeaningfullyDifferent(from other: FrameFingerprint) -> Bool {
        changedPixelRatio(comparedTo: other) >= Self.meaningfulChangedPixelRatio
    }
}

struct ObservedFrame: Sendable {
    let image: CGImage
    let capturedAt: Date
    let captureMode: CaptureMode
    let fingerprint: FrameFingerprint
}
