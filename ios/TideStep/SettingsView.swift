//
//  SettingsView.swift
//  TideStep
//
//  One field: the backend's base URL. Exists so the app can be repointed
//  at whatever machine is running `uvicorn` on demo day without a rebuild
//  — see APIClient.swift.

import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var urlText: String = APIClient.shared.baseURL.absoluteString
    var onSave: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.1.23:8000", text: $urlText)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Backend URL")
                } footer: {
                    Text("The address `uvicorn tidestep.api:app` is running on. Use your laptop's LAN IP (not 127.0.0.1) when testing on a physical iPhone, and start uvicorn with --host 0.0.0.0. Plain http:// requires the App Transport Security exception described in ios/README.md.")
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if let url = URL(string: urlText) {
                            APIClient.shared.setBaseURL(url)
                            onSave()
                        }
                        dismiss()
                    }
                }
            }
        }
    }
}
