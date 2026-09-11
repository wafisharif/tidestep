//
//  RiskMapView.swift
//  TideStep
//
//  MapKit risk map: every road segment drawn as a MapPolyline colored by
//  hazard state for the selected profile/hour — same three-color legend as
//  frontend/index.html (safe / flooded-but-passable / unsafe), plus the
//  computed route in blue. Uses the iOS 17+ SwiftUI Map API (MapReader +
//  MapPolyline), so the deployment target must be iOS 17 or later.

import SwiftUI
import MapKit

/// Matches the JS frontend's `color(p, profile)` function exactly.
enum HazardColor {
    static let safe = Color(red: 0x2e / 255, green: 0x7d / 255, blue: 0x32 / 255)
    static let passable = Color(red: 0xf9 / 255, green: 0xa8 / 255, blue: 0x25 / 255)
    static let unsafe = Color(red: 0xc6 / 255, green: 0x28 / 255, blue: 0x28 / 255)
    static let route = Color(red: 0x0b / 255, green: 0x5f / 255, blue: 0xff / 255)
    // "Evacuate to safety" route — dashed orange, matching
    // frontend/index.html's safety-panel line, deliberately distinct from
    // the solid blue chosen-destination route above so the two modes are
    // never visually ambiguous on screen at once.
    static let safety = Color(red: 0xf9 / 255, green: 0x7e / 255, blue: 0x00 / 255)

    static func forSegment(_ props: RiskSegmentProperties, profile: Profile) -> Color {
        guard props.flooded else { return safe }
        return props.isSafe(for: profile) ? passable : unsafe
    }
}

struct RiskMapView: View {
    @ObservedObject var vm: TideStepViewModel
    @State private var cameraPosition: MapCameraPosition = .automatic
    @State private var didSetInitialCamera = false

    var body: some View {
        MapReader { proxy in
            Map(position: $cameraPosition) {
                if let risk = vm.currentRisk {
                    ForEach(risk.features) { feature in
                        MapPolyline(coordinates: feature.geometry.coordinates)
                            .stroke(HazardColor.forSegment(feature.properties, profile: vm.profile),
                                   lineWidth: feature.properties.flooded ? 5 : 2.5)
                    }
                }
                if let route = vm.currentRoute, let coords = route.geometry?.coordinates {
                    MapPolyline(coordinates: coords)
                        .stroke(HazardColor.route, lineWidth: 5)
                }
                // "Evacuate to safety" result -- a dashed line to the
                // nearest safe haven, or (when the origin already
                // qualifies) just a marker, since there's no route to draw
                // for a zero-length "you're already safe" result.
                if let geometry = vm.safeHaven?.geometry {
                    switch geometry {
                    case .line(let g):
                        MapPolyline(coordinates: g.coordinates)
                            .stroke(HazardColor.safety, style: StrokeStyle(lineWidth: 4, dash: [8, 6]))
                    case .point(let coord):
                        Marker("Already safe", coordinate: coord).tint(HazardColor.safety)
                    }
                }
                if let o = vm.origin {
                    Marker("Start", coordinate: o).tint(.blue)
                }
                if let d = vm.destination {
                    Marker("Destination", coordinate: d).tint(.blue)
                }
            }
            .mapStyle(.standard(elevation: .flat))
            // SpatialTapGesture (not the plain .onTapGesture(perform:) that
            // takes no location) is what actually hands back a CGPoint to
            // feed into MapProxy.convert(_:from:) — this is the documented
            // MapReader tap-to-coordinate pattern.
            .gesture(
                SpatialTapGesture().onEnded { value in
                    if let coordinate = proxy.convert(value.location, from: .local) {
                        vm.setTapPoint(coordinate)
                    }
                }
            )
        }
        .onChange(of: vm.currentRisk?.features.count) { _, _ in
            guard !didSetInitialCamera, let risk = vm.currentRisk, !risk.features.isEmpty else { return }
            fitCamera(to: risk.features.flatMap { $0.geometry.coordinates })
            didSetInitialCamera = true
        }
    }

    private func fitCamera(to coordinates: [CLLocationCoordinate2D]) {
        guard !coordinates.isEmpty else { return }
        let lats = coordinates.map(\.latitude)
        let lons = coordinates.map(\.longitude)
        let center = CLLocationCoordinate2D(
            latitude: (lats.min()! + lats.max()!) / 2,
            longitude: (lons.min()! + lons.max()!) / 2)
        let span = MKCoordinateSpan(
            latitudeDelta: max((lats.max()! - lats.min()!) * 1.3, 0.01),
            longitudeDelta: max((lons.max()! - lons.min()!) * 1.3, 0.01))
        cameraPosition = .region(MKCoordinateRegion(center: center, span: span))
    }
}
