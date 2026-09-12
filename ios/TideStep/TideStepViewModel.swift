//
//  TideStepViewModel.swift
//  TideStep
//
//  Single view model backing the map screen: mirrors the state the Leaflet
//  frontend (frontend/index.html) keeps in plain JS variables — current
//  hour, per-hour risk cache, selected profile, tap-to-set-route points,
//  the last computed route, and saved routes.

import Foundation
import CoreLocation
import SwiftUI

@MainActor
final class TideStepViewModel: ObservableObject {
    private let api: APIClient

    // Forecast
    @Published var hours: [HourInfo] = []
    @Published var hourIndex: Int = 0
    @Published var runTime: String?
    @Published var ofsBiasM: Double?

    // Risk map (cached per hour, same idea as `cache[h]` in the JS frontend
    // — avoids refetching every time the slider passes back over an hour
    // already seen this session)
    private var riskCache: [Int: RiskFeatureCollection] = [:]
    @Published var currentRisk: RiskFeatureCollection?

    // Routing
    @Published var profile: Profile = .adult
    @Published var origin: CLLocationCoordinate2D?
    @Published var destination: CLLocationCoordinate2D?
    @Published var currentRoute: RouteFeature?
    @Published var routeErrorMessage: String?
    /// Mirrors frontend/index.html's "check hazard at actual arrival time,
    /// not just departure" checkbox — when on, findRoute() calls
    /// Router.route_time_aware via /api/route?time_aware=true instead of
    /// the departure-hour-only default.
    @Published var timeAware: Bool = false

    // "Best time to leave" hour strip (Router.route_best_departure) --
    // mirrors frontend/index.html's renderAdvisory(), which (despite the
    // name) is powered by /api/route/best_departure, not
    // /api/route/advisory: it recomputes the ACTUAL best route for every
    // hour rather than just checking one fixed path, so it can find a
    // safe detour at an hour a plain advisory check would call unsafe.
    // Refreshed alongside findRoute(), same trigger as the web version.
    @Published var bestDeparture: BestDepartureResponse?

    // "Evacuate to safety" (Router.route_to_safety) — a destination-free
    // routing mode: given only vm.origin, find the nearest point that
    // stays flood-safe for the rest of the forecast window. Kept as its
    // own published state rather than reusing currentRoute/destination,
    // since it answers a different question (no chosen destination) and
    // the web frontend's "Evacuate to safety" panel keeps it separate too.
    @Published var safeHaven: SafeHavenFeature?
    @Published var safetyErrorMessage: String?
    @Published var isFindingSafety = false

    // Multi-stop trip planning (Router.route_multi_stop /
    // route_multi_stop_optimized) -- mirrors frontend/index.html's
    // "Multi-stop trip" panel: when multiStopMode is on, map taps append
    // an ordered stop instead of setting origin/destination (see
    // RiskMapView's tap gesture, which checks this flag).
    @Published var multiStopMode = false
    @Published var stops: [CLLocationCoordinate2D] = []
    @Published var optimizeStopOrder = false
    @Published var tripPlan: MultiStopFeatureCollection?
    @Published var tripErrorMessage: String?
    @Published var isPlanningTrip = false

    // Saved routes
    @Published var savedRoutes: [SavedRoute] = []

    // UI state
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var config: AppConfig?

    private var autoplayTask: Task<Void, Never>?
    @Published var isPlaying = false

    init(api: APIClient = .shared) {
        self.api = api
    }

    var currentHour: HourInfo? {
        hours.indices.contains(hourIndex) ? hours[hourIndex] : nil
    }

    // MARK: - Loading

    func loadInitial() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let cfg = api.config()
            async let hrs = api.hours()
            let (loadedConfig, loadedHours) = try await (cfg, hrs)
            config = loadedConfig
            hours = loadedHours.hours
            runTime = loadedHours.runTime
            ofsBiasM = loadedHours.ofsBiasM
            errorMessage = nil
            if !hours.isEmpty { await selectHour(0) }
            await loadSavedRoutes()
        } catch {
            errorMessage = "Couldn't reach TideStep backend: \(error.localizedDescription)"
        }
    }

    func selectHour(_ index: Int) async {
        guard hours.indices.contains(index) else { return }
        hourIndex = index
        if let cached = riskCache[index] {
            currentRisk = cached
        } else {
            do {
                let risk = try await api.risk(hour: index)
                riskCache[index] = risk
                currentRisk = risk
            } catch {
                errorMessage = "Couldn't load the risk map for this hour: \(error.localizedDescription)"
            }
        }
        if origin != nil && destination != nil {
            await findRoute()
        }
    }

    func togglePlay() {
        if isPlaying {
            autoplayTask?.cancel()
            isPlaying = false
            return
        }
        isPlaying = true
        autoplayTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 700_000_000)
                if Task.isCancelled { break }
                let next = (self.hourIndex + 1) % max(self.hours.count, 1)
                await self.selectHour(next)
            }
        }
    }

    // MARK: - Routing

    func setTapPoint(_ coordinate: CLLocationCoordinate2D) {
        if origin == nil {
            origin = coordinate
            destination = nil
            currentRoute = nil
            routeErrorMessage = nil
        } else if destination == nil {
            destination = coordinate
            Task { await findRoute() }
        } else {
            // third tap starts a new pair, same as the JS frontend's "clear then set"
            origin = coordinate
            destination = nil
            currentRoute = nil
            routeErrorMessage = nil
        }
    }

    func clearRoute() {
        origin = nil
        destination = nil
        currentRoute = nil
        routeErrorMessage = nil
        bestDeparture = nil
        clearSafety()   // safeHaven is always relative to `origin`, which just cleared
    }

    func findRoute() async {
        guard let o = origin, let d = destination else { return }
        do {
            let result = try await api.route(
                from: (o.latitude, o.longitude), to: (d.latitude, d.longitude),
                profile: profile, hour: hourIndex, timeAware: timeAware)
            currentRoute = result
            routeErrorMessage = result.hasRoute ? nil : (result.properties.error ?? "No safe route at this hour.")
        } catch {
            currentRoute = nil
            routeErrorMessage = "Route request failed: \(error.localizedDescription)"
        }
        await loadBestDeparture()
    }

    /// Hour-by-hour ACTUAL best route for the current origin/destination,
    /// across the whole forecast window (Router.route_best_departure) --
    /// called alongside findRoute() so the "best time to leave" strip
    /// always reflects the current trip, same as the web frontend calling
    /// renderAdvisory() from route().
    func loadBestDeparture() async {
        guard let o = origin, let d = destination else { bestDeparture = nil; return }
        do {
            bestDeparture = try await api.routeBestDeparture(
                from: (o.latitude, o.longitude), to: (d.latitude, d.longitude), profile: profile)
        } catch {
            bestDeparture = nil
        }
    }

    /// Called when the user flips the time-aware toggle — re-runs the
    /// current route (if one is set) under the new mode, same as the web
    /// frontend's `$('timeAware').onchange = route`.
    func setTimeAware(_ value: Bool) {
        timeAware = value
        if origin != nil && destination != nil { Task { await findRoute() } }
    }

    // MARK: - Evacuate to safety

    /// Finds the nearest point reachable from `origin` that stays
    /// flood-safe for `profile` across the rest of the forecast window
    /// (Router.route_to_safety), and stores it in `safeHaven` for
    /// RiskMapView to draw. Needs only vm.origin — unlike findRoute(),
    /// there is no destination to pick, which is the entire point of this
    /// feature (see docs/NOVELTY.md's "get me to safety" entry).
    func findSafety() async {
        guard let o = origin else {
            safetyErrorMessage = "Tap the map to set a starting point first."
            return
        }
        isFindingSafety = true
        defer { isFindingSafety = false }
        do {
            let result = try await api.routeToSafety(
                from: (o.latitude, o.longitude), profile: profile, hour: hourIndex)
            safeHaven = result
            safetyErrorMessage = result.found ? nil
                : (result.properties.error ?? "No reachable safe haven found for this profile.")
        } catch {
            safeHaven = nil
            safetyErrorMessage = "Safety request failed: \(error.localizedDescription)"
        }
    }

    func clearSafety() {
        safeHaven = nil
        safetyErrorMessage = nil
    }

    // MARK: - Multi-stop trip planning

    /// Appends an ordered stop -- called from RiskMapView's tap gesture
    /// when multiStopMode is on, mirroring the web frontend's addStop().
    func addStop(_ coordinate: CLLocationCoordinate2D) {
        stops.append(coordinate)
        tripPlan = nil          // stale relative to the new stop list
        tripErrorMessage = nil
    }

    func clearStops() {
        stops = []
        tripPlan = nil
        tripErrorMessage = nil
    }

    /// Routes through every waypoint in `stops`, in order (or in the best
    /// order, if optimizeStopOrder is set) -- Router.route_multi_stop /
    /// route_multi_stop_optimized via POST /api/route/multi_stop.
    func planTrip() async {
        guard stops.count >= 2 else { return }
        isPlanningTrip = true
        defer { isPlanningTrip = false }
        do {
            let plan = try await api.routeMultiStop(
                waypoints: stops.map { ($0.latitude, $0.longitude) },
                profile: profile, hour: hourIndex, optimizeOrder: optimizeStopOrder)
            tripPlan = plan
            tripErrorMessage = nil
        } catch {
            tripPlan = nil
            tripErrorMessage = "Couldn't plan this trip: \(error.localizedDescription)"
        }
    }

    // MARK: - Saved routes

    func loadSavedRoutes() async {
        do { savedRoutes = try await api.savedRoutes() }
        catch { /* non-fatal — the map still works without this */ }
    }

    func saveCurrentRoute(label: String, contact: String) async {
        guard let o = origin, let d = destination else { return }
        let new = NewSavedRoute(label: label, contact: contact, profile: profile.rawValue,
                                olat: o.latitude, olon: o.longitude,
                                dlat: d.latitude, dlon: d.longitude)
        do {
            _ = try await api.saveRoute(new)
            await loadSavedRoutes()
        } catch {
            errorMessage = "Couldn't save that route: \(error.localizedDescription)"
        }
    }

    func deleteSavedRoute(_ route: SavedRoute) async {
        do {
            try await api.deleteRoute(id: route.routeId)
            savedRoutes.removeAll { $0.id == route.routeId }
        } catch {
            errorMessage = "Couldn't delete that route: \(error.localizedDescription)"
        }
    }
}
