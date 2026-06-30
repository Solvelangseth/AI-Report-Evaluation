import Foundation

/// Minimal multipart/form-data builder (no third-party dependency).
struct MultipartBody {
    struct File { let name, filename, mime: String; let data: Data }

    let boundary = "Boundary-\(UUID().uuidString)"
    let fields: [String: String]
    let file: File?

    var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    func encoded() -> Data {
        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }

        for (key, value) in fields {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"\(key)\"\r\n\r\n")
            append("\(value)\r\n")
        }
        if let file {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"\(file.name)\"; filename=\"\(file.filename)\"\r\n")
            append("Content-Type: \(file.mime)\r\n\r\n")
            body.append(file.data)
            append("\r\n")
        }
        append("--\(boundary)--\r\n")
        return body
    }
}
