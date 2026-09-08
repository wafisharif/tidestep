//
//  APIClient.swift
//  TideStep
//
//  Thin async/await wrapper over the FastAPI backend (tidestep/api.py).
//  No third-party networking library — URLSession is plenty for this
//  surface (5 endpoints, all JSON).

import Foundation

enum APIError: LocalizedError {
    case badStatus(Int, String)
    case decoding(Error)

    var errorDescription: String? {
        switch self {
        case .badStatus(let code, let body):
            return "Server returned \(code): \(body)"
        case .decoding(let err):
            return "Couldn't read the server's response: \(err.localizedDescription)"
        }
    }
}

final class APIClient {
    /// Default backend address, used the first time the app runs. Change it
    /// live afterward from the Settings sheet (gear icon in ContentView) —
    /// no rebuild needed, since on demo day you may need to point the phone
    /// at whichever machine is actually running `uvicorn` without
    /// re-plugging into Xcode.
    /// - iOS Simulator talking to `uvicorn` on the same Mac: "http://127.0.0.1:8000"
    /// - A physical iPhone talking to a laptop on the same Wi-Fi: your
    ///   laptop's LAN IP, e.g. "http://192.168.1.23:8000" (find it with
    ///   `ipconfig getifaddr en0` on macOS or `ipconfig` on Windows), and
    ///   run `uvicorn tidestep.api:app --host 0.0.0.0` so it accepts
    ///   non-local connections.
    /// See ios/README.md for the App Transport Security exception needed
    /// to allow plain http:// during development.
    static let shared = APIClient()

    private static let defaultsKey = "tidestep.backendBaseURL"
    private static let fallbackURL = URL(string: "http://127.0.0.1:8000")!

    private(set) var baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(session: URLSession = .shared) {
        if let stored = UserDefaults.standard.string(forKey: Self.defaultsKey),
           let url = URL(string: stored) {
            self.baseURL = url
        } else {
            self.baseURL = Self.fallbackURL
        }
        self.session = session
        self.decoder = JSONDecoder()
        self.encoder = JSONEncoder()
    }

    /// Called from the Settings sheet when the user changes the backend URL.
    func setBaseURL(_ url: URL) {
        baseURL = url
        UserDefaults.standard.set(url.absoluteString, forKey: Self.defaultsKey)
    }

    // MARK: - Generic request helpers

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        var comps = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query }
        let (data, response) = try await session.data(from: comps.url!)
        try Self.checkStatus(response, data: data)
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.decoding(error) }
    }

    private func send<Body: Encodable, T: Decodable>(
        _ method: String, _ path: String, body: Body
    ) async throws -> T {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: req)
        try Self.checkStatus(response, data: data)
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.decoding(error) }
    }

    private static func checkStatus(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200...299).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "<no body>"
            throw APIError.badStatus(http.statusCode, body)
        }
    }

    // MARK: - Endpoints

    func config() async throws -> AppConfig {
        try await get("/api/config")
    }

    func hours() async throws -> HoursResponse {
        try await get("/api/hours")
    }

    func risk(hour: Int) async throws -> RiskFeatureCollection {
        try await get("/api/risk", query: [URLQueryItem(name: "hour", value: String(hour))])
    }

    func route(from origin: (lat: Double, lon: Double), to destination: (lat: Double, lon: Double),
              profile: Profile, hour: Int) async throws -> RouteFeature {
        try await get("/api/route", query: [
            URLQueryItem(name: "olat", value: String(origin.lat)),
            URLQueryItem(name: "olon", value: String(origin.lon)),
            URLQueryItem(name: "dlat", value: String(destination.lat)),
            URLQueryItem(name: "dlon", value: String(destination.lon)),
            URLQueryItem(name: "profile", value: profile.rawValue),
            URLQueryItem(name: "hour", value: String(hour)),
        ])
    }

    func savedRoutes() async throws -> [SavedRoute] {
        try await get("/api/routes")
    }

    func saveRoute(_ r: NewSavedRoute) async throws -> SavedRouteCreated {
        try await send("POST", "/api/routes", body: r)
    }

    func deleteRoute(id: Int) async throws {
        var req = URLRequest(url: baseURL.appendingPathComponent("/api/routes/\(id)"))
        req.httpMethod = "DELETE"
        let (data, response) = try await session.data(for: req)
        try Self.checkStatus(response, data: data)
    }
}
