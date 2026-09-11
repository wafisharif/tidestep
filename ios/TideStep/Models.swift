//
//  Models.swift
//  TideStep
//
//  Codable models mirroring tidestep/api.py's JSON shapes exactly.
//  Keep field names in sync with the backend — these are hand-written to
//  match tidestep/api.py and tidestep/db.py as of the Stage 4-8 commit;
//  if the backend response shape changes, this is the file to update.

import Foundation
import CoreLocation

// MARK: - /api/config

struct AppConfig: Codable {
    let station: String
    let stationId: String
    let bbox: [Double]              // [south, west, north, east]
    let profiles: [String]
    let depthLimitM: [String: Double]
    let floodThresholdsMNavd88: [String: Double]

    enum CodingKeys: String, CodingKey {
        case station
        case stationId = "station_id"
        case bbox, profiles
        case depthLimitM = "depth_limit_m"
        case floodThresholdsMNavd88 = "flood_thresholds_m_navd88"
    }
}

// MARK: - /api/hours

struct HourInfo: Codable, Identifiable {
    let hour: Int
    let validTime: String
    let waterLevelM: Double
    let floodedSegments: Int

    var id: Int { hour }

    enum CodingKeys: String, CodingKey {
        case hour
        case validTime = "valid_time"
        case waterLevelM = "water_level_m"
        case floodedSegments = "flooded_segments"
    }

    /// Parsed once, cached by the caller — ISO8601 with a UTC offset, as
    /// FastAPI/pydantic serializes a Python datetime.
    var date: Date? {
        ISO8601DateFormatter().date(from: validTime)
    }
}

struct HoursResponse: Codable {
    let runTime: String?
    let ofsBiasM: Double?
    let hours: [HourInfo]

    enum CodingKeys: String, CodingKey {
        case runTime = "run_time"
        case ofsBiasM = "ofs_bias_m"
        case hours
    }
}

// MARK: - Profiles (child / adult / vehicle_small / vehicle_large / vehicle_4wd)
// Kept in sync with tidestep/hazard.py PROFILES — order matters for the picker.

enum Profile: String, CaseIterable, Identifiable, Codable {
    case child, adult
    case vehicleSmall = "vehicle_small"
    case vehicleLarge = "vehicle_large"
    case vehicle4wd = "vehicle_4wd"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .child: return "Child (limit 0.5 m)"
        case .adult: return "Adult (limit 1.2 m)"
        case .vehicleSmall: return "Small car (0.3 m)"
        case .vehicleLarge: return "SUV / large car (0.4 m)"
        case .vehicle4wd: return "Truck / 4WD (0.5 m)"
        }
    }

    var isVehicle: Bool { self != .child && self != .adult }

    /// The matching "safe_<profile>" key in a risk-map feature's properties.
    var safeKey: String { "safe_\(rawValue)" }
}

// MARK: - /api/risk — GeoJSON FeatureCollection

struct RiskSegmentProperties: Codable {
    let segmentId: Int
    let name: String?
    let highway: String?
    let groundM: Double?
    let nearInlet: Bool
    let validTime: String
    let waterLevelM: Double
    let depthCm: Int
    let flooded: Bool
    let safeChild: Bool
    let safeAdult: Bool
    let safeVehicleSmall: Bool
    let safeVehicleLarge: Bool
    let safeVehicle4wd: Bool

    enum CodingKeys: String, CodingKey {
        case segmentId = "segment_id"
        case name, highway
        case groundM = "ground_m"
        case nearInlet = "near_inlet"
        case validTime = "valid_time"
        case waterLevelM = "water_level_m"
        case depthCm = "depth_cm"
        case flooded
        case safeChild = "safe_child"
        case safeAdult = "safe_adult"
        case safeVehicleSmall = "safe_vehicle_small"
        case safeVehicleLarge = "safe_vehicle_large"
        case safeVehicle4wd = "safe_vehicle_4wd"
    }

    func isSafe(for profile: Profile) -> Bool {
        switch profile {
        case .child: return safeChild
        case .adult: return safeAdult
        case .vehicleSmall: return safeVehicleSmall
        case .vehicleLarge: return safeVehicleLarge
        case .vehicle4wd: return safeVehicle4wd
        }
    }
}

/// A LineString geometry: coordinates are [[lon, lat], [lon, lat], ...].
/// Decoded by hand because Swift's Codable has no built-in "array of
/// 2-tuples" support and GeoJSON's [lon, lat] order is the opposite of
/// CoreLocation's (lat, lon).
struct LineStringGeometry: Codable {
    let coordinates: [CLLocationCoordinate2D]

    enum CodingKeys: String, CodingKey { case type, coordinates }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let raw = try c.decode([[Double]].self, forKey: .coordinates)
        coordinates = raw.map { CLLocationCoordinate2D(latitude: $0[1], longitude: $0[0]) }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode("LineString", forKey: .type)
        try c.encode(coordinates.map { [$0.longitude, $0.latitude] }, forKey: .coordinates)
    }
}

struct RiskFeature: Codable, Identifiable {
    let geometry: LineStringGeometry
    let properties: RiskSegmentProperties

    var id: Int { properties.segmentId }
}

struct RiskFeatureCollection: Codable {
    let features: [RiskFeature]
}

// MARK: - /api/route — a single GeoJSON Feature, or {geometry: null, properties: {error}}
//
// Two response shapes share this one struct, selected by whether the
// request set time_aware=true (tidestep/api.py get_route): the plain
// mode's fields (baselineLengthM/baselineBlocked/avoidedEdges) and the
// time-aware mode's fields (travelTimeMin/departureHour/arrivalHour/
// hourCrossed/timeAware) are mutually exclusive in practice, which is why
// every field here is optional rather than the struct being split in two.

struct RouteProperties: Codable {
    let lengthM: Double?
    let baselineLengthM: Double?
    let baselineBlocked: Bool?
    let avoidedEdges: Int?
    let maxDepthCmOnRoute: Int?
    let error: String?
    // time_aware=true only (Router.route_time_aware / time_aware_route_geojson)
    let travelTimeMin: Double?
    let departureHour: Int?
    let arrivalHour: Int?
    let hourCrossed: Bool?
    let timeAware: Bool?

    enum CodingKeys: String, CodingKey {
        case lengthM = "length_m"
        case baselineLengthM = "baseline_length_m"
        case baselineBlocked = "baseline_blocked"
        case avoidedEdges = "avoided_edges"
        case maxDepthCmOnRoute = "max_depth_cm_on_route"
        case error
        case travelTimeMin = "travel_time_min"
        case departureHour = "departure_hour"
        case arrivalHour = "arrival_hour"
        case hourCrossed = "hour_crossed"
        case timeAware = "time_aware"
    }
}

struct RouteFeature: Codable {
    let geometry: LineStringGeometry?
    let properties: RouteProperties

    var hasRoute: Bool { geometry != nil }
}

// MARK: - /api/route/advisory — hour-by-hour safe/unsafe forecast for one
// fixed (flood-blind) path (Router.route_advisory)

struct HourAdvisory: Codable, Identifiable {
    let hour: Int
    let safe: Bool
    let maxDepthCm: Int

    var id: Int { hour }

    enum CodingKeys: String, CodingKey {
        case hour, safe
        case maxDepthCm = "max_depth_cm"
    }
}

struct RouteAdvisoryResponse: Codable {
    let baselineLengthM: Double?
    let hours: [HourAdvisory]

    enum CodingKeys: String, CodingKey {
        case baselineLengthM = "baseline_length_m"
        case hours
    }
}

// MARK: - /api/route/best_departure — hour-by-hour ACTUAL best route, plus
// the earliest hour a real route exists (Router.route_best_departure)

struct HourRoute: Codable, Identifiable {
    let hour: Int
    let safe: Bool
    let lengthM: Double?
    let travelTimeMin: Double?
    let maxDepthCmOnRoute: Int?

    var id: Int { hour }

    enum CodingKeys: String, CodingKey {
        case hour, safe
        case lengthM = "length_m"
        case travelTimeMin = "travel_time_min"
        case maxDepthCmOnRoute = "max_depth_cm_on_route"
    }
}

struct BestDepartureResponse: Codable {
    let baselineLengthM: Double?
    let recommendedHour: Int?
    let recommendedLengthM: Double?
    let recommendedTravelTimeMin: Double?
    let hours: [HourRoute]

    enum CodingKeys: String, CodingKey {
        case baselineLengthM = "baseline_length_m"
        case recommendedHour = "recommended_hour"
        case recommendedLengthM = "recommended_length_m"
        case recommendedTravelTimeMin = "recommended_travel_time_min"
        case hours
    }
}

// MARK: - Shared geometry helper for endpoints whose geometry can be
// either a LineString (a real multi-point route) or a Point (a
// zero-length "already here"/degenerate-leg case) — POST
// /api/route/multi_stop and GET /api/route/to_safety both do this (see
// tidestep/routing.py: multi_stop_route_geojson, safe_haven_geojson).

enum RouteOrPointGeometry: Codable {
    case line(LineStringGeometry)
    case point(CLLocationCoordinate2D)

    private enum CodingKeys: String, CodingKey { case type, coordinates }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        if type == "Point" {
            let coord = try c.decode([Double].self, forKey: .coordinates)
            self = .point(CLLocationCoordinate2D(latitude: coord[1], longitude: coord[0]))
        } else {
            self = .line(try LineStringGeometry(from: decoder))
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .line(let g):
            try g.encode(to: encoder)
        case .point(let coord):
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode("Point", forKey: .type)
            try c.encode([coord.longitude, coord.latitude], forKey: .coordinates)
        }
    }

    /// Coordinates to draw on the map, whichever geometry this is — a
    /// Point becomes a single-element array so a caller can treat a
    /// zero-length leg/haven the same way as a real route without
    /// branching on the case everywhere it draws one.
    var coordinates: [CLLocationCoordinate2D] {
        switch self {
        case .line(let g): return g.coordinates
        case .point(let c): return [c]
        }
    }
}

// MARK: - POST /api/route/multi_stop — a GeoJSON FeatureCollection, one
// Feature per completed leg, plus trip-level totals in a top-level
// `properties` key (Router.route_multi_stop / multi_stop_route_geojson)

struct MultiStopLegProperties: Codable {
    let legIndex: Int
    let lengthM: Double
    let travelTimeMin: Double
    let departureHour: Int
    let arrivalHour: Int
    let maxDepthCmOnRoute: Int

    enum CodingKeys: String, CodingKey {
        case legIndex = "leg_index"
        case lengthM = "length_m"
        case travelTimeMin = "travel_time_min"
        case departureHour = "departure_hour"
        case arrivalHour = "arrival_hour"
        case maxDepthCmOnRoute = "max_depth_cm_on_route"
    }
}

struct MultiStopLegFeature: Codable, Identifiable {
    let geometry: RouteOrPointGeometry
    let properties: MultiStopLegProperties

    var id: Int { properties.legIndex }
}

struct MultiStopTripProperties: Codable {
    let blockedLegIndex: Int?
    let totalLengthM: Double?
    let totalTravelTimeMin: Double?
    let departureHour: Int
    let arrivalHour: Int?
    let maxDepthCmOnRoute: Int

    enum CodingKeys: String, CodingKey {
        case blockedLegIndex = "blocked_leg_index"
        case totalLengthM = "total_length_m"
        case totalTravelTimeMin = "total_travel_time_min"
        case departureHour = "departure_hour"
        case arrivalHour = "arrival_hour"
        case maxDepthCmOnRoute = "max_depth_cm_on_route"
    }
}

struct MultiStopFeatureCollection: Codable {
    let features: [MultiStopLegFeature]
    let properties: MultiStopTripProperties

    var allSafe: Bool { properties.blockedLegIndex == nil }
}

/// Request body for POST /api/route/multi_stop (tidestep/api.py's
/// MultiStopRequest/Waypoint pydantic models) — 2-10 ordered waypoints.
struct MultiStopRequest: Encodable {
    struct WaypointBody: Encodable { let lat: Double; let lon: Double }
    let waypoints: [WaypointBody]
    let profile: String
    let hour: Int
}

// MARK: - GET /api/route/to_safety — a single GeoJSON Feature (Point for
// the "already safe" case, LineString for a real route), or
// {geometry: null, properties: {error}} (Router.route_to_safety /
// safe_haven_geojson)

struct SafeHavenProperties: Codable {
    let lengthM: Double?
    let travelTimeMin: Double?
    let departureHour: Int?
    let arrivalHour: Int?
    let maxDepthCmOnRoute: Int?
    let alreadySafe: Bool?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case lengthM = "length_m"
        case travelTimeMin = "travel_time_min"
        case departureHour = "departure_hour"
        case arrivalHour = "arrival_hour"
        case maxDepthCmOnRoute = "max_depth_cm_on_route"
        case alreadySafe = "already_safe"
        case error
    }
}

struct SafeHavenFeature: Codable {
    let geometry: RouteOrPointGeometry?
    let properties: SafeHavenProperties

    var found: Bool { geometry != nil }
}

// MARK: - /api/routes — saved routes for alerting

struct SavedRoute: Codable, Identifiable {
    let routeId: Int
    let label: String?
    let contact: String
    let profile: String
    let createdAt: String
    let lastBlocked: Bool
    let lastChecked: String?
    let olat: Double
    let olon: Double
    let dlat: Double
    let dlon: Double

    var id: Int { routeId }

    enum CodingKeys: String, CodingKey {
        case routeId = "route_id"
        case label, contact, profile
        case createdAt = "created_at"
        case lastBlocked = "last_blocked"
        case lastChecked = "last_checked"
        case olat, olon, dlat, dlon
    }
}

struct NewSavedRoute: Codable {
    let label: String
    let contact: String
    let profile: String
    let olat: Double
    let olon: Double
    let dlat: Double
    let dlon: Double
}

struct SavedRouteCreated: Codable {
    let routeId: Int
    enum CodingKeys: String, CodingKey { case routeId = "route_id" }
}
