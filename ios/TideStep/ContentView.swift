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

            if let err = vm.routeErrorMessage {
                Text(err).font(.caption).foregroundStyle(.red)
            } else if let route = vm.currentRoute, route.hasRoute {
                routeSummary(route)
            }

            HStack {
                Button("Clear") { vm.clearRoute() }
                    .font(.caption)
                if vm.origin != nil && vm.destination != nil && vm.currentRoute?.hasRoute == true {
                    Button("Save & get alerts") { showSaveSheet = true }
                        .font(.caption)
                }
            }

            legend
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
        .frame(maxWidth: 300)
        .padding(.leading, 12)
    }

    private func routeSummary(_ route: RouteFeature) -> some View {
        let km = (route.properties.lengthM ?? 0) / 1000
        return VStack(alignment: .leading, spacing: 2) {
            Text(String(format: "%.2f km", km)).font(.subheadline.bold())
            if route.properties.baselineBlocked == true {
                let baseKm = (route.properties.baselineLengthM ?? 0) / 1000
                Text(String(format: "Usual route (%.2f km) is flooded — this detour avoids %d unsafe edges.",
                           baseKm, route.properties.avoidedEdges ?? 0))
                    .font(.caption)
            } else {
                Text("Usual route is clear at this hour.").font(.caption)
            }
            if let depth = route.properties.maxDepthCmOnRoute, depth > 0 {
                Text("Deepest water on route: \(depth) cm").font(.caption)
            }
        }
        .padding(6)
        .background(Color.gray.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
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
