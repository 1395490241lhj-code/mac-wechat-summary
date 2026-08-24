import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Prepares a frame for remote extraction entirely in memory.
///
/// The encoded bytes exist only as a `Data` value for the lifetime of one
/// request. Nothing here writes to disk: the destination is backed by
/// `CFMutableData`, never a file URL.
enum FrameImageEncoder {
    /// Conservative cap. Large enough that Chinese WeChat text stays legible,
    /// small enough that a full-height chat window is not sent at retina scale.
    static let maxPixelDimension = 1600
    static let compressionQuality = 0.85

    static func encodedJPEG(from image: CGImage) throws -> Data {
        let prepared = downsampled(image)
        let buffer = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            buffer,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            throw FrameImageEncoderError.encodingFailed
        }
        CGImageDestinationAddImage(
            destination,
            prepared,
            [kCGImageDestinationLossyCompressionQuality: compressionQuality] as CFDictionary
        )
        guard CGImageDestinationFinalize(destination) else {
            throw FrameImageEncoderError.encodingFailed
        }
        return buffer as Data
    }

    /// Never upscales; only shrinks a frame whose longest side exceeds the cap.
    static func downsampled(_ image: CGImage) -> CGImage {
        let longestSide = max(image.width, image.height)
        guard longestSide > maxPixelDimension else { return image }

        let scale = Double(maxPixelDimension) / Double(longestSide)
        let width = max(1, Int((Double(image.width) * scale).rounded()))
        let height = max(1, Int((Double(image.height) * scale).rounded()))
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ) else { return image }

        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return context.makeImage() ?? image
    }
}

enum FrameImageEncoderError: Error, Equatable {
    case encodingFailed
}
