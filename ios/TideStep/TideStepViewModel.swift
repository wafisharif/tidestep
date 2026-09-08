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
    }

    func findRoute() async {
        guard let o = origin, let d = destination else { return }
        do {
            let result = try await api.route(
                from: (o.latitude, o.longitude), to: (d.latitude, d.longitude),
                profile: profile, hour: hourIndex)
            currentRoute = result
            routeErrorMessage = result.hasRoute ? nil : (result.properties.error ?? "No safe route at this hour.")
        } catch {
            currentRoute = nil
            routeErrorMessage = "Route request failed: \(error.localizedDescription)"
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
