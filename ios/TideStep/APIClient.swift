//
//  APIClient.swift
//  TideStep
//
//  Thin async/await wrapper over the FastAPI backend (tidestep/api.py).
//  No third-party networking library — URLSession is plenty for this
//  surface (11 endpoints, all JSON).

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

    /// - Parameter timeAware: when true, hazard is checked at each
    ///   segment's own arrival hour (based on travel time) instead of
    ///   once at departure (Router.route_time_aware) — defaults to false,
    ///   the original behavior, so existing call sites are unaffected.
    func route(from origin: (lat: Double, lon: Double), to destination: (lat: Double, lon: Double),
              profile: Profile, hour: Int, timeAware: Bool = false) async throws -> RouteFeature {
        try await get("/api/route", query: [
            URLQueryItem(name: "olat", value: String(origin.lat)),
            URLQueryItem(name: "olon", value: String(origin.lon)),
            URLQueryItem(name: "dlat", value: String(destination.lat)),
            URLQueryItem(name: "dlon", value: String(destination.lon)),
            URLQueryItem(name: "profile", value: profile.rawValue),
            URLQueryItem(name: "hour", value: String(hour)),
            URLQueryItem(name: "time_aware", value: timeAware ? "true" : "false"),
        ])
    }

    /// Hour-by-hour safe/unsafe forecast for this trip's usual (flood-blind)
    /// path across the whole forecast window (Router.route_advisory).
    func routeAdvisory(from origin: (lat: Double, lon: Double), to destination: (lat: Double, lon: Double),
                       profile: Profile) async throws -> RouteAdvisoryResponse {
        try await get("/api/route/advisory", query: [
            URLQueryItem(name: "olat", value: String(origin.lat)),
            URLQueryItem(name: "olon", value: String(origin.lon)),
            URLQueryItem(name: "dlat", value: String(destination.lat)),
            URLQueryItem(name: "dlon", value: String(destination.lon)),
            URLQueryItem(name: "profile", value: profile.rawValue),
        ])
    }

    /// Per-hour ACTUAL best route across the forecast window, plus the
    /// earliest hour a real route exists at all (Router.route_best_departure)
    /// — can find a safe detour at an hour routeAdvisory() would call
    /// unsafe because its one fixed path floods.
    func routeBestDeparture(from origin: (lat: Double, lon: Double), to destination: (lat: Double, lon: Double),
                            profile: Profile) async throws -> BestDepartureResponse {
        try await get("/api/route/best_departure", query: [
            URLQueryItem(name: "olat", value: String(origin.lat)),
            URLQueryItem(name: "olon", value: String(origin.lon)),
            URLQueryItem(name: "dlat", value: String(destination.lat)),
            URLQueryItem(name: "dlon", value: String(destination.lon)),
            URLQueryItem(name: "profile", value: profile.rawValue),
        ])
    }

    /// Flood-avoiding route through an ordered list of 2-10 waypoints,
    /// where each leg's hazard check starts from the PREVIOUS leg's
    /// actual arrival hour, not the trip's overall departure hour
    /// repeated for every leg (Router.route_multi_stop).
    func routeMultiStop(waypoints: [(lat: Double, lon: Double)], profile: Profile,
                        hour: Int) async throws -> MultiStopFeatureCollection {
        let body = MultiStopRequest(
            waypoints: waypoints.map { MultiStopRequest.WaypointBody(lat: $0.lat, lon: $0.lon) },
            profile: profile.rawValue, hour: hour)
        return try await send("POST", "/api/route/multi_stop", body: body)
    }

    /// Evacuation-style routing: given only a starting point (no
    /// destination), find the nearest reachable point that stays
    /// flood-safe for the rest of the forecast window and route there
    /// (Router.route_to_safety).
    func routeToSafety(from origin: (lat: Double, lon: Double), profile: Profile,
                       hour: Int) async throws -> SafeHavenFeature {
        try await get("/api/route/to_safety", query: [
            URLQueryItem(name: "olat", value: String(origin.lat)),
            URLQueryItem(name: "olon", value: String(origin.lon)),
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
