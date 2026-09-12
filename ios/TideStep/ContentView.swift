//
//  ContentView.swift
//  TideStep
//
//  Root screen: full-screen risk map with a control panel and time slider
//  overlaid, matching frontend/index.html's layout (#panel top-left,
//  #time bottom-center) so the web and iOS demos look like the same app.

import SwiftUI

struct ContentView: View {
    @StateObject private var vm = TideStepViewModel()
    @State private var showSavedRoutes = false
    @State private var showSaveSheet = false
    @State private var showSettings = false
    // Local draft slider position, kept in sync with vm.hourIndex below but
    // decoupled during an active drag so the API isn't hit on every pixel
    // of finger movement — see timeSlider.
    @State private var sliderDraft: Double = 0

    var body: some View {
        ZStack(alignment: .top) {
            RiskMapView(vm: vm)
                .ignoresSafeArea()

            VStack {
                HStack(alignment: .top) {
                    controlPanel
                    Spacer()
                }
                Spacer()
                timeSlider
            }
            .padding(.top, 8)
        }
        .task { await vm.loadInitial() }
        .onChange(of: vm.hourIndex) { _, newValue in sliderDraft = Double(newValue) }
        .sheet(isPresented: $showSavedRoutes) {
            SavedRoutesView(vm: vm)
        }
        .sheet(isPresented: $showSaveSheet) {
            SaveRouteSheet(vm: vm)
        }
        .sheet(isPresented: $showSettings) {
            SettingsView { Task { await vm.loadInitial() } }
        }
        .alert("TideStep", isPresented: .constant(vm.errorMessage != nil), actions: {
            Button("OK") { vm.errorMessage = nil }
        }, message: {
            Text(vm.errorMessage ?? "")
        })
    }

    private var controlPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("TideStep").font(.headline)
                    Text(vm.config?.station ?? "loading…")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button { showSettings = true } label: {
                    Image(systemName: "gearshape")
                }
                Button { showSavedRoutes = true } label: {
                    Image(systemName: "bell.badge")
                }
            }

            Picker("Who is traveling?", selection: $vm.profile) {
                ForEach(Profile.allCases) { p in Text(p.label).tag(p) }
            }
            .pickerStyle(.menu)
            .onChange(of: vm.profile) { _, _ in Task { await vm.findRoute() } }

            Text("Tap the map to set a start point, tap again for a destination.")
                .font(.caption2).foregroundStyle(.secondary)

            Toggle(isOn: Binding(
                get: { vm.timeAware },
                set: { vm.setTimeAware($0) }
            )) {
                Text("check hazard at actual arrival time, not just departure")
                    .font(.caption2)
            }
            .toggleStyle(.switch)

            if let err = vm.routeErrorMessage {
                Text(err).font(.caption).foregroundStyle(.red)
            } else if let route = vm.currentRoute, route.hasRoute {
                routeSummary(route)
            }

            bestDepartureStrip

            HStack {
                Button("Clear") { vm.clearRoute() }
                    .font(.caption)
                if vm.origin != nil && vm.destination != nil && vm.currentRoute?.hasRoute == true {
                    Button("Save & get alerts") { showSaveSheet = true }
                        .font(.caption)
                }
            }

            evacuateToSafety

            multiStopPanel

            legend
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
        .frame(maxWidth: 300)
        .padding(.leading, 12)
    }

    private func routeSummary(_ route: RouteFeature) -> some View {
        let p = route.properties
        let km = (p.lengthM ?? 0) / 1000
        return VStack(alignment: .leading, spacing: 2) {
            if p.timeAware == true {
                // time-aware mode: no baseline/avoided-edges (those are
                // departure-hour concepts) -- show travel time and whether
                // the trip crosses into a later forecast hour, matching
                // frontend/index.html's route() timeAware branch.
                Text(String(format: "%.2f km · ~%.1f min", km, p.travelTimeMin ?? 0))
                    .font(.subheadline.bold())
                if p.hourCrossed == true {
                    Text("Arrives around hour \(p.arrivalHour ?? 0) (later than departure hour \(p.departureHour ?? 0)); "
                         + "hazard was checked for each segment at the hour you'd actually reach it.")
                        .font(.caption)
                } else {
                    Text("Arrives within the same forecast hour.").font(.caption)
                }
            } else {
                Text(String(format: "%.2f km", km)).font(.subheadline.bold())
                if p.baselineBlocked == true {
                    let baseKm = (p.baselineLengthM ?? 0) / 1000
                    Text(String(format: "Usual route (%.2f km) is flooded — this detour avoids %d unsafe edges.",
                               baseKm, p.avoidedEdges ?? 0))
                        .font(.caption)
                } else {
                    Text("Usual route is clear at this hour.").font(.caption)
                }
            }
            if let depth = p.maxDepthCmOnRoute, depth > 0 {
                Text("Deepest water on route: \(depth) cm").font(.caption)
            }
        }
        .padding(6)
        .background(Color.gray.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
    }

    /// "Best time to leave" hour strip (Router.route_best_departure) --
    /// mirrors frontend/index.html's renderAdvisory(): a row of colored
    /// cells (one per forecast hour, green if a real route exists that
    /// hour, red if not) plus a callout naming the earliest hour with a
    /// route. Refreshed whenever findRoute() runs (see
    /// TideStepViewModel.loadBestDeparture()), so it always reflects the
    /// current origin/destination.
    private var bestDepartureStrip: some View {
        Group {
            if let a = vm.bestDeparture {
                VStack(alignment: .leading, spacing: 3) {
                    if a.baselineLengthM == nil {
                        Text("No route exists for this profile between these points, flooding aside.")
                            .font(.caption2).foregroundStyle(.secondary)
                    } else {
                        HStack(spacing: 1) {
                            ForEach(a.hours) { h in
                                RoundedRectangle(cornerRadius: 1)
                                    .fill(h.safe ? HazardColor.safe : HazardColor.unsafe)
                                    .frame(height: 10)
                            }
                        }
                        if let rh = a.recommendedHour {
                            let km = (a.recommendedLengthM ?? 0) / 1000
                            let longer: String = {
                                guard let rec = a.recommendedLengthM, let base = a.baselineLengthM, base > 0,
                                      rec > base else { return "" }
                                let pct = Int((rec / base - 1) * 100)
                                return " (\(pct)% longer than the direct route)"
                            }()
                            Text(String(format: "Best time to leave: hour %d — %.2f km, ~%.1f min%@",
                                       rh, km, a.recommendedTravelTimeMin ?? 0, longer))
                                .font(.caption2)
                        } else {
                            Text("No safe route exists for this trip at any hour in the forecast.")
                                .font(.caption2)
                        }
                        let nUnsafe = a.hours.filter { !$0.safe }.count
                        Text(nUnsafe > 0 ? "Safe to leave now → +24h · unsafe \(nUnsafe) of \(a.hours.count) hours"
                                          : "Safe to leave now → +24h · safe the whole window")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    /// "Evacuate to safety" (Router.route_to_safety, GET
    /// /api/route/to_safety) — reuses whatever start point is already set
    /// above; needs no destination, which is the point of the feature.
    /// Matches frontend/index.html's "Evacuate to safety" panel.
    private var evacuateToSafety: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                Task { await vm.findSafety() }
            } label: {
                if vm.isFindingSafety {
                    ProgressView().controlSize(.small)
                } else {
                    Text("Evacuate to safety")
                }
            }
            .font(.caption)
            .disabled(vm.isFindingSafety)

            if let err = vm.safetyErrorMessage {
                Text(err).font(.caption2).foregroundStyle(.red)
            } else if let haven = vm.safeHaven, haven.found {
                safetySummary(haven.properties)
            }
        }
    }

    private func safetySummary(_ p: SafeHavenProperties) -> some View {
        Group {
            if p.alreadySafe == true {
                Text("You're already somewhere that stays safe for the rest of the forecast.")
                    .font(.caption2)
            } else {
                let km = (p.lengthM ?? 0) / 1000
                Text(String(format: "Nearest safe haven: %.2f km, ~%.1f min away.",
                           km, p.travelTimeMin ?? 0))
                    .font(.caption2)
            }
        }
        .foregroundStyle(.secondary)
    }

    /// "Multi-stop trip" panel (Router.route_multi_stop /
    /// route_multi_stop_optimized, POST /api/route/multi_stop) -- mirrors
    /// frontend/index.html's multi-stop panel + planTrip(). While
    /// vm.multiStopMode is on, RiskMapView's tap gesture appends to
    /// vm.stops instead of setting origin/destination.
    private var multiStopPanel: some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle(isOn: $vm.multiStopMode) {
                Text("Multi-stop trip").font(.caption)
            }
            .toggleStyle(.switch)
            .onChange(of: vm.multiStopMode) { _, on in if !on { vm.clearStops() } }

            if vm.multiStopMode {
                Text(stopsLabel).font(.caption2).foregroundStyle(.secondary)

                Toggle(isOn: $vm.optimizeStopOrder) {
                    Text("find the best order to visit stops in (up to 6 stops)")
                        .font(.caption2)
                }
                .toggleStyle(.switch)

                HStack {
                    Button {
                        Task { await vm.planTrip() }
                    } label: {
                        if vm.isPlanningTrip {
                            ProgressView().controlSize(.small)
                        } else {
                            Text("Plan trip")
                        }
                    }
                    .font(.caption)
                    .disabled(vm.stops.count < 2 || vm.isPlanningTrip)

                    Button("Clear stops") { vm.clearStops() }
                        .font(.caption)
                }

                if let err = vm.tripErrorMessage {
                    Text("Couldn't plan this trip: \(err)").font(.caption2).foregroundStyle(.red)
                } else if let trip = vm.tripPlan {
                    tripSummary(trip)
                }
            }
        }
    }

    private var stopsLabel: String {
        switch vm.stops.count {
        case 0: return "Tap the map to add stops, in order."
        case 1: return "1 stop set — add at least one more"
        default: return "\(vm.stops.count) stops set — ready to plan"
        }
    }

    private func tripSummary(_ trip: MultiStopFeatureCollection) -> some View {
        let p = trip.properties
        return Group {
            if let blocked = p.blockedLegIndex {
                Text("Blocked at stop \(blocked + 2) of \(vm.stops.count) — "
                     + "no safe route for that leg at the hour you'd actually reach it "
                     + "(\(trip.features.count) of \(vm.stops.count - 1) leg(s) shown are safely reachable).")
                    .font(.caption2)
            } else {
                let km = (p.totalLengthM ?? 0) / 1000
                let orderNote: String = {
                    guard vm.optimizeStopOrder, let tried = p.ordersTried else { return "" }
                    return p.optimized == true
                        ? " — reordered stops for a shorter trip (checked \(tried) visiting order(s))."
                        : " — your order was already best (checked \(tried) visiting order(s))."
                }()
                Text(String(format: "%.2f km total · ~%.1f min · arrives around hour %d%@",
                           km, p.totalTravelTimeMin ?? 0, p.arrivalHour ?? 0, orderNote))
                    .font(.caption2)
            }
        }
        .foregroundStyle(.secondary)
    }

    private var legend: some View {
        HStack(spacing: 10) {
            legendItem(HazardColor.safe, "safe")
            legendItem(HazardColor.passable, "passable")
            legendItem(HazardColor.unsafe, "unsafe")
            legendItem(HazardColor.route, "route")
        }
        .font(.caption2)
    }

    private func legendItem(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 3) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 14, height: 4)
            Text(label)
        }
    }

    private var timeSlider: some View {
        VStack(spacing: 4) {
            HStack {
                if let h = vm.currentHour {
                    Text(formatted(h.date)).font(.subheadline.bold())
                    Spacer()
                    Text(String(format: "%.2f m NAVD88", h.waterLevelM)).font(.caption.monospacedDigit())
                    Spacer()
                    Text("\(h.floodedSegments) flooded").font(.caption)
                } else {
                    Text("loading…")
                }
            }
            // Local draft value while dragging; only calls the API (which
            // fetches a new risk map + re-routes) once the finger lifts,
            // via onEditingChanged — not on every pixel of drag.
            Slider(
                value: $sliderDraft,
                in: 0...Double(max(vm.hours.count - 1, 0)), step: 1,
                onEditingChanged: { editing in
                    if !editing { Task { await vm.selectHour(Int(sliderDraft.rounded())) } }
                }
            )
            HStack {
                Text(vm.hours.first.flatMap { formatted($0.date) } ?? "now").font(.caption2)
                Spacer()
                Button(vm.isPlaying ? "pause" : "play") { vm.togglePlay() }
                    .font(.caption2)
                Spacer()
                Text(vm.hours.last.flatMap { formatted($0.date) } ?? "+24h").font(.caption2)
            }
        }
        .padding(10)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
        .frame(maxWidth: 500)
        .padding(.bottom, 18)
    }

    private func formatted(_ date: Date?) -> String {
        guard let date else { return "—" }
        let f = DateFormatter()
        f.dateFormat = "EEE h a"
        return f.string(from: date)
    }
}

struct SaveRouteSheet: View {
    @ObservedObject var vm: TideStepViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var label = ""
    @State private var contact = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Alert me when this route floods") {
                    TextField("Label (e.g. \"school run\")", text: $label)
                    TextField("Email", text: $contact)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                }
            }
            .navigationTitle("Save route")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            await vm.saveCurrentRoute(label: label, contact: contact)
                            dismiss()
                        }
                    }
                    .disabled(contact.isEmpty)
                }
            }
        }
    }
}

#Preview {
    ContentView()
}
