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

    // "Evacuate to safety" (Router.route_to_safety) — a destination-free
    // routing mode: given only vm.origin, find the nearest point that
    // stays flood-safe for the rest of the forecast window. Kept as its
    // own published state rather than reusing currentRoute/destination,
    // since it answers a different question (no chosen destination) and
    // the web frontend's "Evacuate to safety" panel keeps it separate too.
    @Published var safeHaven: SafeHavenFeature?
    @Published var safetyErrorMessage: String?
    @Published var isFindingSafety = false

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
