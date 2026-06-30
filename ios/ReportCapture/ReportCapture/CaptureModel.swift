import Foundation
import SwiftUI

/// Orchestrates one inspection: record voice, snap categorized photos, then
/// upload everything and finalize. The phone only captures + uploads; all the
/// analysis happens server-side.
@MainActor
final class CaptureModel: ObservableObject {
    @Published var categories: [Category] = []
    @Published var selectedCategory: Category?
    @Published var photos: [CapturedPhoto] = []
    @Published var title = ""
    @Published var phase: Phase = .idle
    @Published var status: SessionStatus?
    @Published var errorMessage: String?

    enum Phase: Equatable { case idle, recording, uploading, processing, done, failed }

    let recorder = AudioRecorder()
    private let api = APIClient()

    func loadCategories() async {
        do {
            categories = try await api.categories()
            selectedCategory = categories.first
        } catch { errorMessage = error.localizedDescription }
    }

    func startRecording() {
        recorder.start()
        phase = .recording
    }

    /// Capture a photo at the current recording offset, tagged with the category.
    func addPhoto(_ jpeg: Data) {
        guard let category = selectedCategory else { return }
        photos.append(CapturedPhoto(jpeg: jpeg, timestamp: recorder.currentTimestamp,
                                    category: category))
    }

    /// Stop recording and push the whole session to the server.
    func finishAndUpload() async {
        recorder.stop()
        phase = .uploading
        do {
            let created = try await api.createSession(title: title.isEmpty ? "Befaring" : title)
            let sid = created.session_id
            if let audio = recorder.fileURL {
                try await api.uploadAudio(sessionId: sid, fileURL: audio)
            }
            for photo in photos {
                try await api.uploadPhoto(sessionId: sid, jpeg: photo.jpeg,
                                          timestamp: photo.timestamp,
                                          category: photo.category.slug,
                                          caption: photo.caption)
            }
            status = try await api.finalize(sessionId: sid, background: true)
            phase = .processing
            await pollUntilDone(sid: sid)
        } catch {
            errorMessage = error.localizedDescription
            phase = .failed
        }
    }

    private func pollUntilDone(sid: Int) async {
        for _ in 0..<120 {                       // up to ~2 min
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard let s = try? await api.getSession(sessionId: sid) else { continue }
            status = s
            if s.isFailed { phase = .failed; errorMessage = s.error; return }
            if s.isDone { phase = .done; return }
        }
    }

    func reset() {
        photos = []; title = ""; status = nil; errorMessage = nil; phase = .idle
    }
}
