import Foundation

/// App configuration. Point `baseURL` at your Flask server and set the token to
/// match `CAPTURE_API_TOKEN` (leave nil for an open dev server).
final class AppConfig {
    static let shared = AppConfig()
    var baseURL = URL(string: "http://localhost:5050")!
    var token: String? = nil
}

struct Category: Codable, Identifiable, Hashable {
    let slug: String
    let label: String
    let part: String
    var id: String { slug }
}

struct CategoriesResponse: Codable { let categories: [Category] }
struct GenericResponse: Codable { let success: Bool }

/// Mirrors the session JSON the API returns.
struct SessionStatus: Codable {
    let session_id: Int
    let title: String
    let status: String          // open | processing | clean | minor_error | major_error | failed
    let photo_count: Int?
    let report_id: Int?
    let verdict: String?
    let error: String?

    var isProcessing: Bool { status == "processing" }
    var isDone: Bool { report_id != nil }
    var isFailed: Bool { status == "failed" }
}

/// A photo captured locally, pending (or done) upload.
struct CapturedPhoto: Identifiable {
    let id = UUID()
    let jpeg: Data
    let timestamp: Double        // seconds from recording start
    let category: Category
    var caption: String = ""
    var uploaded: Bool = false
}
