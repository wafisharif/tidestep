//
//  SavedRoutesView.swift
//  TideStep
//
//  List of routes saved for alerting (Stage 8 / scripts/hourly_update.py).
//  Delivery itself happens server-side (email, per hourly_update.py); this
//  view is just management — see, add, remove.

import SwiftUI

struct SavedRoutesView: View {
    @ObservedObject var vm: TideStepViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if vm.savedRoutes.isEmpty {
                    ContentUnavailableView(
                        "No saved routes",
                        systemImage: "bell.slash",
                        description: Text("Set a start and destination on the map, find a route, then \"Save & get alerts\" to be notified when it's about to flood."))
                } else {
                    ForEach(vm.savedRoutes) { route in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(route.label?.isEmpty == false ? route.label! : "Untitled route")
                                    .font(.headline)
                                Spacer()
                                if route.lastBlocked {
                                    Label("flooded", systemImage: "exclamationmark.triangle.fill")
                                        .font(.caption).foregroundStyle(.red)
                                }
                            }
                            Text("\(profileLabel(route.profile)) · alerts to \(route.contact)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .swipeActions {
                            Button("Delete", role: .destructive) {
                                Task { await vm.deleteSavedRoute(route) }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Saved routes")
            .task { await vm.loadSavedRoutes() }
            .refreshable { await vm.loadSavedRoutes() }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func profileLabel(_ raw: String) -> String {
        Profile(rawValue: raw)?.label ?? raw
    }
}
