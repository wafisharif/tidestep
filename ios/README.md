# TideStep iOS

A native SwiftUI client for the same backend the web app uses
(`tidestep/api.py`) — no duplicated pipeline logic, it's a thin,
purpose-built map/routing/alerts UI on top of the existing FastAPI
endpoints. Same three-color hazard legend and same behavior as
`frontend/index.html`, so the web and iOS demos read as one product.

**Important — read this before opening Xcode:** this code was written and
reviewed carefully (matched field-for-field against `tidestep/api.py`'s
actual JSON responses, cross-checked against Apple's documented MapKit
SwiftUI API), but it has **not been compiled**. Neither Claude sandbox used
to build this has Xcode or a Swift/Apple-platform toolchain available (only
Linux is reachable from here — SwiftUI, MapKit, and UIKit don't exist on
Linux). Budget 15-30 minutes for the first build to shake out anything Xcode
flags — normal for any hand-written Swift, but worth planning for rather
than assuming it drops straight in.

## What's here

```
ios/TideStep/
  TideStepApp.swift        app entry point
  ContentView.swift        root screen: map + control panel + time slider
  RiskMapView.swift        MapKit risk map, colored polylines, tap-to-route
  TideStepViewModel.swift  all app state (forecast, risk cache, routing, saved routes)
  APIClient.swift          async/await networking against tidestep/api.py
  Models.swift             Codable structs matching the backend's JSON exactly
  SavedRoutesView.swift    manage routes saved for alerting
  SettingsView.swift       change the backend URL at runtime, no rebuild
```

There is deliberately no `.xcodeproj` here — hand-writing Xcode's project
file format is fragile and, without Xcode to verify it, more likely to
cause a mysterious "won't open" than to save you time. Creating a fresh
project and adding these files takes about two minutes and guarantees a
project file Xcode actually generated.

## Setup

1. **Create the project.** Xcode -> File -> New -> Project -> iOS -> App.
   Product Name: `TideStep`. Interface: **SwiftUI**. Language: **Swift**.
   Uncheck "Use Core Data" and "Include Tests" (not needed here).
2. **Set the deployment target to iOS 17.0 or later.** `RiskMapView.swift`
   uses the `MapReader`/`MapPolyline`/`Marker` SwiftUI Map API introduced
   in iOS 17 — this is a hard requirement, not a nice-to-have.
3. **Delete the placeholder `ContentView.swift`** Xcode generated, then drag
   every file from `ios/TideStep/` in this repo into the Xcode project
   navigator (check "Copy items if needed").
4. **Allow local HTTP during development.** The backend runs on plain
   `http://`, and iOS's App Transport Security blocks that by default.
   Open your target's Info tab (or `Info.plist` if you added one) and add:
   ```xml
   <key>NSAppTransportSecurity</key>
   <dict>
       <key>NSAllowsLocalNetworking</key>
       <true/>
   </dict>
   ```
   `NSAllowsLocalNetworking` covers `127.0.0.1` and RFC 1918 LAN addresses
   (like a laptop's `192.168.x.x`), which is all this needs — no blanket
   "allow arbitrary loads" exception required.
5. **If testing on a physical iPhone**, it needs to reach the backend over
   Wi-Fi, not localhost:
   - Start the backend with `uvicorn tidestep.api:app --host 0.0.0.0` so it
     accepts non-local connections.
   - Find your laptop's LAN IP (macOS: `ipconfig getifaddr en0`; Windows:
     `ipconfig` and look for the Wi-Fi adapter's IPv4 address).
   - Open the app, tap the gear icon, enter `http://<that IP>:8000`. This
     is stored in `UserDefaults` so it persists across launches — no
     rebuild needed to change it later (useful if you switch networks
     between now and demo day).
   - Phone and laptop must be on the same Wi-Fi network.
6. Build and run. On first launch the app calls `/api/config` and
   `/api/hours` — if you see the "Couldn't reach TideStep backend" alert,
   check step 4/5 first; that error means the request never landed, not a
   bug in the map itself.

## Demo without live NOAA data

Same fallback as the web app: run `python scripts/dev_seed.py` from the
repo root (see the main `README.md`) to load a small synthetic scenario
into PostGIS, then point the iOS app at that backend exactly as above. The
map, slider, and per-profile routing all work identically against
synthetic data — useful for rehearsing the demo without depending on NOAA
being reachable at that moment.

## API coverage

`APIClient.swift` and `Models.swift` cover all 11 JSON endpoints
`tidestep/api.py` exposes, kept in sync as the backend grew across several
passes (see `docs/STATUS.md`): `/api/config`, `/api/hours`, `/api/risk`,
`/api/route` (including `time_aware=true`), `/api/route/advisory`,
`/api/route/best_departure`, `POST /api/route/multi_stop` (including
`optimize_order=true`), `GET /api/route/to_safety`, and the saved-routes
CRUD trio. UI wiring in
`ContentView.swift`/`RiskMapView.swift`/`TideStepViewModel.swift` now
covers every one of those except `/api/route/advisory`: the original
map/routing/saved-routes flow, the "check hazard at actual arrival time"
time-aware toggle, "Evacuate to safety" (a button that reuses the
already-set start point, no destination needed, drawing a dashed orange
line — or a marker for the already-safe case — to the nearest point that
stays flood-safe for the rest of the forecast window), the "best time to
leave" hour strip (`ContentView.bestDepartureStrip`, backed by
`TideStepViewModel.loadBestDeparture()` — refreshed alongside every
`findRoute()` call, same trigger as the web frontend's `renderAdvisory()`),
and the multi-stop trip planner (`ContentView.multiStopPanel` +
`RiskMapView`'s stop markers/leg polylines, backed by
`TideStepViewModel.addStop()`/`clearStops()`/`planTrip()`), including the
opt-in "find the best order to visit stops in" toggle
(`route_multi_stop_optimized`).
**`/api/route/advisory` (`RouteAdvisoryResponse`) has no screen and none is
planned**: it was never actually a standalone feature of the web app either
— `frontend/index.html`'s "advisory" hour strip calls
`renderAdvisory()`, and that function has always been powered by
`/api/route/best_departure`, not `/api/route/advisory` (confirmed by
reading the frontend JS directly). `RouteAdvisoryResponse` and
`APIClient.routeAdvisory()` are kept for API completeness/direct testing
but true web-parity only ever required the two screens now built above.

## Known gaps / next steps

- **Not compiled yet** (see above) — this is the top priority before
  relying on it for a demo. This applies doubly to the newest additions
  (the time-aware toggle, "Evacuate to safety", the best-departure strip,
  and the multi-stop planner) — reviewed line by line against
  `tidestep/api.py`'s and `tidestep/routing.py`'s actual JSON shapes and
  against `frontend/index.html`'s equivalent behavior, brace- and
  paren-balance-checked, but never built.
- No offline caching beyond the in-memory per-hour risk cache
  (`TideStepViewModel.riskCache`) — closing the app loses it. Fine for a
  demo, worth a `URLCache`/on-disk cache for a real 2.0.
- No push notifications — alerts are still delivered by
  `scripts/hourly_update.py` via email (see the main README), same as the
  web app. A 2.0 item would be a companion push notification via APNs,
  which needs an Apple Developer account and a server-side push
  integration, out of scope for the submission timeline.
- No location permission / "use my current location" for the start point
  — routes are set entirely by tapping the map, matching the web
  frontend's UX exactly. Worth adding `CoreLocation` for "start from where
  I am" as a nice-to-have, not required for the core demo.
