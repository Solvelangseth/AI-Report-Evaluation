import Foundation

/// Talks to the Flask capture API (see PLAN.md "Mobile capture API").
/// All calls are async/await over URLSession. Multipart is built by hand to
/// avoid a dependency. Configure `baseURL` / `token` in `AppConfig`.
struct APIClient {
    let baseURL: URL
    let token: String?

    init(config: AppConfig = .shared) {
        self.baseURL = config.baseURL
        self.token = config.token
    }

    private func request(_ path: String, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = method
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return req
    }

    private func send<T: Decodable>(_ req: URLRequest, as: T.Type) async throws -> T {
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw APIError.server(String(data: data, encoding: .utf8) ?? "request failed")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    // MARK: - Endpoints

    func categories() async throws -> [Category] {
        try await send(request("api/categories"), as: CategoriesResponse.self).categories
    }

    func createSession(title: String, transcript: String? = nil) async throws -> SessionStatus {
        var req = request("api/sessions", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(
            withJSONObject: ["title": title, "transcript": transcript ?? ""])
        return try await send(req, as: SessionStatus.self)
    }

    func uploadPhoto(sessionId: Int, jpeg: Data, timestamp: Double,
                     category: String, caption: String = "") async throws {
        var req = request("api/sessions/\(sessionId)/photos", method: "POST")
        let body = MultipartBody(fields: [
            "timestamp": String(timestamp), "category": category, "caption": caption,
        ], file: .init(name: "photo", filename: "photo.jpg",
                       mime: "image/jpeg", data: jpeg))
        req.setValue(body.contentType, forHTTPHeaderField: "Content-Type")
        req.httpBody = body.encoded()
        _ = try await send(req, as: GenericResponse.self)
    }

    func uploadAudio(sessionId: Int, fileURL: URL) async throws {
        let data = try Data(contentsOf: fileURL)
        var req = request("api/sessions/\(sessionId)/audio", method: "POST")
        let body = MultipartBody(fields: [:], file: .init(
            name: "audio", filename: fileURL.lastPathComponent,
            mime: "audio/m4a", data: data))
        req.setValue(body.contentType, forHTTPHeaderField: "Content-Type")
        req.httpBody = body.encoded()
        _ = try await send(req, as: GenericResponse.self)
    }

    /// Kick off processing in the background; poll `getSession` for the result.
    func finalize(sessionId: Int, background: Bool = true) async throws -> SessionStatus {
        var req = request("api/sessions/\(sessionId)/finalize", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["background": background])
        return try await send(req, as: SessionStatus.self)
    }

    func getSession(sessionId: Int) async throws -> SessionStatus {
        try await send(request("api/sessions/\(sessionId)"), as: SessionStatus.self)
    }
}

enum APIError: LocalizedError {
    case server(String)
    var errorDescription: String? { if case let .server(m) = self { return m }; return nil }
}
