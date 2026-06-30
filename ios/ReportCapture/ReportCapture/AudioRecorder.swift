import AVFoundation
import Foundation

/// Records one continuous voice track to an .m4a file and exposes elapsed time
/// so photos can be stamped with their offset from the recording start.
final class AudioRecorder: NSObject, ObservableObject {
    @Published var isRecording = false
    @Published var elapsed: TimeInterval = 0

    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private(set) var fileURL: URL?

    func start() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .default)
        try? session.setActive(true)

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("inspection-\(UUID().uuidString).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
        ]
        recorder = try? AVAudioRecorder(url: url, settings: settings)
        recorder?.record()
        fileURL = url
        isRecording = true
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            self?.elapsed = self?.recorder?.currentTime ?? 0
        }
    }

    func stop() {
        recorder?.stop()
        timer?.invalidate()
        timer = nil
        isRecording = false
    }

    /// Current offset (seconds) to stamp a photo with.
    var currentTimestamp: Double { recorder?.currentTime ?? elapsed }
}
