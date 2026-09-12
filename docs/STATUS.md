# Status

Updated: 2026-09-12

## Done (2026-09-12, tenth pass — iOS parity: the two remaining screens)

As with the ninth pass, this pass re-read `docs/STATUS.md`,
`ios/README.md`, and every touched Swift file fresh from disk before
editing anything, since the concurrent teammate session shares this
sandbox's filesystem and, apparently, the connected laptop folder too —
no collision found this time, everything was exactly as the ninth pass's
own entry above left it.

Before writing any Swift, re-checked `ios/README.md`'s own claim that
three endpoints (`/api/route/advisory`, `/api/route/best_departure`,
`POST /api/route/multi_stop`) had "no screen yet." Reading
`frontend/index.html` directly showed this was one endpoint too many:
the web app's own "advisory" hour-strip UI (`renderAdvisory()`) has
always called `/api/route/best_departure`, not `/api/route/advisory` —
the latter has never been a standalone web feature, just a model/client
pair kept for completeness. So true web-parity only ever needed **two**
new screens, not three, and this pass built exactly those two:

- **"Best time to leave" hour strip** (`ContentView.bestDepartureStrip`):
  a row of 24 colored cells (green = a real route exists that hour, red =
  none does) plus a callout naming the earliest safe hour, its
  length/travel time, and how much longer it is than the direct route
  when applicable — a line-for-line SwiftUI port of `renderAdvisory()`'s
  actual DOM output, including the "no route exists at all, flooding
  aside" and "no safe route at any hour" edge cases. Wired into
  `TideStepViewModel.bestDeparture` (`@Published`), refreshed by a new
  `loadBestDeparture()` called at the end of `findRoute()` for both the
  success and failure paths, exactly mirroring the web frontend calling
  `renderAdvisory()` unconditionally from `route()`.
- **Multi-stop trip planner** (`ContentView.multiStopPanel` +
  `RiskMapView`'s map layer): a toggle switches `RiskMapView`'s tap
  gesture from "set origin/destination" to "append an ordered stop"
  (`TideStepViewModel.addStop()`), draws each tapped stop as a numbered
  marker, and — after "Plan trip" calls the new `planTrip()` — draws one
  colored polyline (or marker, for a zero-length leg) per completed leg
  via a new `HazardColor.legColors`/`forLeg(_:)` palette that matches
  `frontend/index.html`'s `legColors` array exactly, cycled by leg index.
  Includes the ninth pass's `optimize_order` opt-in as a checkbox
  ("find the best order to visit stops in (up to 6 stops)"), and the
  result text reproduces the web version's exact three cases: blocked at
  a specific stop, completed with a plain order, or completed with a
  reordered/confirmed-already-best order naming how many visiting orders
  were checked — all read directly from `frontend/index.html`'s
  `planTrip()` JS rather than re-derived, so the wording matches on both
  platforms.
- **Models/APIClient support added first** (same order as every previous
  iOS pass — data layer before UI): `MultiStopTripProperties` gained the
  four `optimize_order`-only fields (`order`, `optimized`, `ordersTried`,
  `ordersComplete`, all optional so a plain `multi_stop` response — which
  omits these keys entirely — still decodes correctly);
  `MultiStopRequest` gained `optimizeOrder`; `APIClient.routeMultiStop()`
  gained an `optimizeOrder: Bool = false` parameter (default preserves
  every existing call site).
- **Verification, same honest caveat as every iOS pass**: no
  Swift/Xcode toolchain exists in either Claude sandbox, so this is
  reviewed and cross-checked, not compiled. Every field name was matched
  against the structs read from `Models.swift` before writing a single
  line of UI code (`MultiStopLegFeature.properties.legIndex`,
  `RouteOrPointGeometry`'s `.line`/`.point` cases, `HourRoute.safe`, etc.)
  rather than guessed, and brace/paren balance was re-checked after every
  edit: `Models.swift` 75/75 braces, 57/57 parens; `APIClient.swift`
  30/30, 130/130; `TideStepViewModel.swift` 63/63, 83/83;
  `RiskMapView.swift` 28/28, 88/88; `ContentView.swift` 125/125, 270/270.
- **`ios/README.md`** rewritten to describe both new screens under "API
  coverage," and to correct the "three endpoints, no screen" framing to
  the real "one endpoint (`/api/route/advisory`) was never a standalone
  feature to begin with" story, so a future reader doesn't go looking for
  a third screen that was never actually missing.
- Python test suite untouched by this pass (iOS-only changes) —
  re-ran anyway as a sanity check: still 136/136.
- Synced to the laptop (`C:\Users\wafis\Documents\tidestep-app\...`) via
  the device bridge and byte-verified: `Models.swift`, `APIClient.swift`,
  `TideStepViewModel.swift`, `RiskMapView.swift`, `ContentView.swift`,
  `ios/README.md`, `docs/STATUS.md`.

## Done (2026-09-12, ninth pass — stop-order optimization for multi-stop
trips, the first item on the eighth pass's own "Next" list)

Before starting any new code, this pass re-read the entire current state
of `tidestep/routing.py`, `tidestep/api.py`, `frontend/index.html`, and
every test file fresh from disk — not from memory of an earlier session —
specifically because of the eighth pass's concurrent-edit warning above.
Confirmed `route_multi_stop()`, `route_to_safety()`, `always_safe_nodes()`,
and their frontend/tests/docs were already complete and correct (126/126
passing before this pass touched anything), so this pass picked up the
next undone item from the roadmap rather than re-deriving already-finished
work.

- **`Router.route_multi_stop_optimized()`** (`POST /api/route/multi_stop`
  with `optimize_order: true`) closes the exact gap
  `docs/LIMITATIONS.md` flagged: `route_multi_stop()` visits waypoints in
  the order given, with no search for a better order. The new method
  fixes the origin and final destination and brute-forces every
  permutation of the INTERMEDIATE stops (capped at
  `routing.MAX_OPTIMIZE_STOPS = 6` — 6! = 720 permutations is fast at this
  bbox's scale; the endpoint rejects with a clear 400 past that rather
  than silently taking a long time), scoring only visiting orders where
  every leg actually completes (no `blocked_leg_index`) by total travel
  time. If no order completes at all, the original given order's plan is
  returned unchanged (`optimized: false`) rather than guessing which
  partial failure is "best."
- **Built entirely on `route_multi_stop()`** (one call per candidate
  order) rather than a new pathfinding implementation, so its correctness
  rests on that method's already-tested per-leg arrival-hour chaining —
  this method only adds the search over orders, the same "reuse the
  already-proven engine" pattern every routing feature since
  `route_time_aware()` has followed.
- **Test count: 126 -> 136** (+4 `tests/test_routing.py`: a constructed
  case with a straight 4-node chain graph where the given order forces
  backtracking (500 m) and the optimal reorder does not (300 m) — proving
  this answers a genuinely different question than a fixed-order chain,
  not just relabeling the same result; the "0 or 1 intermediate stop,
  nothing to optimize" case; the `MAX_OPTIMIZE_STOPS` rejection; and the
  "every order is blocked, fall back to the given order" case, where the
  only edge out of the origin is unsafe so no visiting order can possibly
  work. +4 `tests/test_api.py` request-validation paths (including
  confirming the stop-count cap is rejected with a 400 *before* the
  database is touched, and that a plain `multi_stop` request without
  `optimize_order` is completely unaffected). +2
  `tests/test_integration.py` against real loaded data: a 4-waypoint
  adult trip confirming both permutations complete and the response is
  self-consistent (a genuine permutation of all waypoint indices, origin
  and destination never reordered), and the stop-count-cap rejection
  against the real app. All 136 pass in `pytest -q`, including the 16
  DB-dependent integration tests run for real against a live local
  Postgres this pass.
- **Live end-to-end verification proved a REAL improvement, not just a
  well-formed response**: booted a real `uvicorn` process against
  regenerated dev_seed data and queried actual segment coordinates from
  Postgres to build waypoints that land on genuinely distinct street
  nodes (the first attempt used coordinates outside the synthetic
  scenario's actual (tiny) street extent, which — honestly noted, not
  hidden — made every waypoint snap to the same one or two nodes and
  produced no interesting reordering; querying real segment endpoints
  from the database fixed this). With four waypoints arranged so the
  given order forces a diagonal criss-cross across the grid, the plain
  endpoint returned 1203.3 m; the same four waypoints with
  `optimize_order=true` correctly found the perimeter order instead,
  863.3 m — a real 28% reduction, from reordering alone. Every
  pre-existing endpoint (`/api/route`, `/api/route/to_safety`, and a
  plain `multi_stop` request) was re-curled and reconfirmed unchanged —
  zero regression, since `routing.py` and `api.py` were touched again
  this pass.
- **Frontend**: the "Multi-stop trip" panel gets a new "find the best
  order to visit stops in" checkbox. When checked, `planTrip()` sends
  `optimize_order: true` and, on success, relabels the numbered stop pins
  on the map with their ACTUAL visiting order (not just the click order),
  so a reordering is visible at a glance, not only described in the
  result text — which itself now says whether reordering happened and
  how many visiting orders were checked. A non-200 response (the
  stop-count cap) is now shown as an explicit message instead of failing
  silently. Verified via `node --check` on the extracted inline script
  and, live, by curling the running server and confirming the new
  `optimizeOrder` checkbox and `optimize_order` field are actually
  served.
- Docs updated: `docs/LIMITATIONS.md`'s multi-stop-order caveat rewritten
  to describe the new opt-in optimization (and its honest remaining
  limits: brute force, not a real TSP heuristic past the cap; optimizes
  total time only, no per-stop time windows); `docs/NOVELTY.md` gets an
  11th numbered differentiator, citing the live 1203.3 m -> 863.3 m
  result above as concrete proof rather than a claimed capability.

## Done (2026-09-11, eighth pass — concurrent-edit collision caught and
resolved, iOS app brought back to parity with 5 backend passes it had
missed)

**Heads up for both of you, since this is exactly the kind of thing
`CLAUDE.md`'s "both teammates run their own Claude session against this
repo" setup can produce**: this pass started by independently building
multi-stop routing and a "nearest safe haven" evacuation finder — the same
two features the *previous* ("seventh pass") entry below already
describes, which a concurrent session had just finished writing directly
into `tidestep/routing.py` while this session was mid-edit on the same
file. The two sets of edits landed interleaved: duplicate `SafeHavenResult`
class definitions, a duplicate `route_multi_stop` method (Python keeps only
the last definition of a repeated name, so the second one silently wins),
and a `route_to_safety()` that would have raised `TypeError` at its first
real call (it constructs `SafeHavenResult` with keyword arguments that only
its *original* class definition has, but the redefinition further down the
file — from this session's competing edit — had already overwritten that
class with an incompatible, simpler shape). **Caught before anything was
saved to disk from this session's own separate mistake compounding it
further** — noticed only because a docstring edit's exact-text match
failed against what was actually on disk, which prompted a full re-read
instead of retrying the edit blindly.

- **Resolved by removing this session's duplicate/inferior code and
  keeping the concurrent session's implementation entirely**, which turned
  out to be more complete anyway: a single grouped SQL query
  (`db.always_safe_nodes()`) instead of this session's N-per-hour-queries
  Python loop for the same "which nodes stay safe all window" computation,
  full per-leg route geometry for multi-stop trips (this session's
  competing version only kept summary numbers, no drawable geometry), and
  already-written frontend/tests/docs to match. Verified clean afterward:
  every class and method in `tidestep/routing.py` now appears exactly
  once (`grep -n "^    def \|^class \|^@dataclass"`), the module imports
  without error, and the full suite passes — **126/126**, unchanged from
  before this pass touched anything, confirming the cleanup was a pure
  no-op on top of the concurrent session's already-complete, already-
  tested work (multi-stop trip chaining + `route_to_safety()` — see the
  "seventh pass" entry directly below for what that pass actually built).
- **No feature work was duplicated as a result** — instead, this pass did
  something the concurrent session's own "Next" list hadn't gotten to:
  the iOS SwiftUI client (`ios/`) had not been touched since before *any*
  of passes 4 through 7 (time-aware routing, `route_best_departure()`,
  multi-stop trips, `route_to_safety()` — five new/changed endpoints,
  confirmed via file mtimes: every `ios/TideStep/*.swift` file predates
  all of them), so `docs/NOVELTY.md`'s "cross-platform from day one" claim
  (item 5: "same routing logic, same data, two real clients") had quietly
  gone stale. Brought back to parity:
  - **`Models.swift`**: `RouteProperties` extended with the five
    time-aware-only fields (`travelTimeMin`, `departureHour`,
    `arrivalHour`, `hourCrossed`, `timeAware`), all optional so the one
    struct still decodes both the plain and time-aware response shapes
    correctly. New `RouteAdvisoryResponse`/`HourAdvisory`,
    `BestDepartureResponse`/`HourRoute`, `MultiStopFeatureCollection`/
    `MultiStopLegFeature`/`MultiStopRequest`, and `SafeHavenFeature`/
    `SafeHavenProperties` structs, every field name matched against
    `tidestep/api.py`'s actual JSON keys the same way the original models
    were built. New `RouteOrPointGeometry` enum (mirrors
    `tidestep/routing.py`'s Point-for-a-zero-length-leg fix from the
    previous pass) reusing the same manual-decode pattern the original
    `LineStringGeometry` already established, rather than a new approach.
  - **`APIClient.swift`**: `route()` gained a `timeAware` parameter
    (default `false`, so every existing call site is unaffected) plus four
    new methods — `routeAdvisory()`, `routeBestDeparture()`,
    `routeMultiStop()`, `routeToSafety()` — one per new endpoint. Header
    comment's endpoint count corrected from 5 to 11 (`tidestep/api.py`'s
    actual current route count).
  - **`TideStepViewModel.swift`** / **`ContentView.swift`** /
    **`RiskMapView.swift`**: two of the five new endpoints got full UI —
    a time-aware toggle (mirrors `frontend/index.html`'s checkbox exactly,
    re-runs the current route under the new mode) and "Evacuate to
    safety" (a button needing only the already-set start point, drawing a
    dashed orange line — or a marker for the "already safe" case — to the
    nearest point that stays flood-safe for the rest of the window).
    `route/advisory`, `route/best_departure`, and `route/multi_stop` got
    complete model + API-client support but **no screen yet** — their web
    equivalents (a 24-cell hour strip, a click-to-add-stop planner) are
    real UI builds, not extensions of an existing control, and were left
    for a follow-up rather than rushed with no compiler to check against.
  - **Verification, honestly scoped**: still no Swift/Xcode toolchain in
    either Claude sandbox (`docs/STATUS.md`'s standing caveat), so this is
    reviewed-not-compiled, same as every previous iOS pass. Checked what
    *can* be checked without one: every new/changed struct's field names
    and JSON key mappings re-verified against the live `tidestep/api.py`
    and `tidestep/routing.py` on disk (not memory/assumption), and a
    brace/paren balance check across all five touched files (naive, but
    catches gross structural mistakes: `{`/`}` and `(`/`)` counts match in
    every file).
  - `ios/README.md` updated: a new "API coverage" section states plainly
    which endpoints have a screen and which don't, so this doesn't quietly
    go stale the way the pre-this-pass state did.

## Done (2026-09-10, seventh pass — two new routing capabilities:
multi-stop trip chaining and destination-free evacuation routing)

This pass builds the two feature candidates the sixth pass's "Next"
section proposed: multi-stop trips and a "nearest safe high ground"
finder. Both are genuinely new questions the app can now answer, not
variations on what already existed, and both are built entirely on
`route_time_aware()` (fourth pass) rather than new pathfinding code.

- **`Router.route_multi_stop()`** (`POST /api/route/multi_stop`) routes
  through an ordered list of 2-10 waypoints — an errand run with stops,
  not just point-to-point — by chaining `route_time_aware()` calls where
  each leg's departure hour is the PREVIOUS leg's actual arrival hour.
  That chaining is the entire point: checking each leg independently at
  the trip's starting hour repeats, one level up, the exact "hazard at
  departure hour" mistake time-aware routing was built to fix for a
  single leg. Proven with a constructed case
  (`tests/test_routing.py::test_route_multi_stop_catches_a_leg_that_floods_by_the_time_you_reach_it`)
  where a second leg looks completely safe checked in isolation at hour
  0, but the real trip — because the first leg alone takes over an hour —
  wouldn't reach it until hour 1, by which point it has flooded;
  `route_multi_stop()` correctly reports the trip blocked there, and the
  test explicitly confirms the naive per-leg-at-hour-0 check would have
  missed it. If a leg can't be completed, the response reports which one
  (`blocked_leg_index`) and still returns the legs that succeeded before
  it, so a user can see "you can safely get this far."
- **`Router.route_to_safety()`** (`GET /api/route/to_safety`) answers a
  genuinely different question from every other routing method in this
  app: not "get me to a destination I have in mind" but "I don't have
  one — where can I go that's safe?" Given only a starting point and
  profile, it searches for the nearest reachable point that stays
  flood-safe for the rest of the forecast window (not just this instant),
  via a new `db.always_safe_nodes()` query and a multi-target version of
  the time-expanded Dijkstra (stops at the *first* popped node that
  qualifies, which — because Dijkstra pops in increasing order of elapsed
  time — is guaranteed nearest in actual travel time, not just straight-
  line distance). Handles the "you're already somewhere safe" case as a
  zero-length result rather than an unnecessary route, and correctly
  reports "no reachable haven" when none exists for a profile in the
  window (honestly reproduced live for `vehicle_small` at the synthetic
  scenario's peak hour, below).
- **Real bug found and fixed via live testing, not just unit tests**:
  the first live check of `/api/route/multi_stop` against real seeded
  data hit a case where two requested waypoints snapped to the same
  street graph node, producing a zero-length leg whose GeoJSON Feature
  was an invalid single-coordinate LineString (the GeoJSON spec requires
  at least two positions in a LineString). Fixed by emitting a Point
  geometry for any zero-length leg instead — the same treatment already
  used for `route_to_safety()`'s "already safe" case — and added a
  regression test
  (`test_multi_stop_route_geojson_uses_point_when_a_leg_is_zero_length`)
  reproducing the exact scenario so it can't silently regress.
- **Test count: 105 -> 126** (+10 `tests/test_routing.py`: the two
  constructed correctness demonstrations above, happy-path and
  already-safe and no-haven and unreachable-haven cases for
  `route_to_safety()`, a spy test confirming `always_safe_nodes()` is
  queried from the requested departure hour onward (not the whole
  window, which would wrongly disqualify a haven that only floods
  earlier in the day), and the zero-length-leg regression above; +8
  `tests/test_api.py` request-validation paths for both new endpoints;
  +3 `tests/test_integration.py` against real loaded data: a full
  3-waypoint trip for `adult` (which never floods in this scenario, so
  every leg should succeed), a bad-profile rejection, and a
  self-consistency check for `route_to_safety()` that doesn't overclaim
  a specific destination the synthetic topology may or may not have).
  All 126 pass in `pytest -q`, including the 14 DB-dependent integration
  tests run for real against a live local Postgres this pass.
- **Live end-to-end verification**: regenerated the dev_seed scenario,
  booted a real `uvicorn` process, and curled both new endpoints
  directly. `multi_stop` correctly chains a 3-waypoint adult trip end to
  end; `to_safety` for `adult` at hour 0 correctly reports "already
  safe" (adult never floods in this scenario); `to_safety` for
  `vehicle_small` at hour 0 returns a real routed detour (55.6 m, a
  genuine non-trivial result); `to_safety` for `vehicle_small` at the
  peak hour 7 honestly reports no reachable haven exists for that
  profile at that hour in this scenario — not fabricated, the real
  result of a real search. Every previously-existing endpoint
  (`/api/route`, `/api/route/best_departure`, `/api/route/advisory`) was
  re-curled and reconfirmed byte-identical to its pre-existing shape —
  zero regression, since `routing.py` and `api.py` were touched again
  this pass.
- **Frontend**: two new panel sections. "Multi-stop trip" — a checkbox
  toggles click-to-add-stop mode (numbered purple markers), a "Plan
  trip" button posts the collected waypoints and draws each leg in a
  distinct color, with a result line covering both the success and
  blocked-leg cases. "Evacuate to safety" — a button that reuses the
  route panel's already-set start point, calls the new endpoint, and
  draws the result as a dashed orange line (or a marker for the
  already-safe case). Verified via `node --check` on the extracted
  inline script and, live, by curling the running server and confirming
  the new DOM elements (`multiStopMode`, `planTrip`, `findSafety`,
  `tripResult`, `safetyResult`) are actually served.
- Docs updated: `docs/LIMITATIONS.md` gets two new honest caveats (no
  stop-order optimization for multi-stop trips; `route_to_safety()`'s
  "safe haven" is any dry point, not a known real shelter location, and
  still inherits the still-water-ponding-only model); `docs/NOVELTY.md`
  gets two new numbered differentiators (9, 10).

## Done (2026-09-10, sixth pass — "best time to leave" trip planning:
`route_best_departure()` closes the exact gap the fifth pass flagged as
its own next step)

This pass builds directly on the previous one's new feature (time-aware
routing) and its own stated next-step candidate in this file's "Next"
section: `route_advisory()` only checks whether one fixed, flood-blind
path is safe hour by hour — so a trip whose *usual* route floods all day
gets reported as impossible all day, even when a real (longer) detour
would get someone there safely right now. This pass builds the fix.

- **`Router.route_best_departure()`** (`tidestep/routing.py`, exposed via
  new `/api/route/best_departure`) recomputes the *actual* best route for
  every hour in the forecast window — not just whether the usual path is
  blocked — by calling the previous pass's `route_time_aware()` once per
  candidate departure hour and collecting the result. It reports, per
  hour: whether any route exists, its real length and travel time, and
  the deepest water it crosses; plus a single `recommended_hour` — the
  earliest hour a real route exists at all — with that route's length and
  travel time, and the flood-blind baseline length for comparison.
- **Proven to answer a genuinely different question than the existing
  advisory endpoint, not just a rename of it**:
  `tests/test_routing.py::test_route_best_departure_finds_a_safe_detour_advisory_would_call_unsafe`
  constructs a graph where the direct path is unsafe at every hour.
  `route_advisory()` (unchanged, still tested against its original
  behavior) correctly reports every hour unsafe — it only ever looks at
  that one path. `route_best_departure()`, on the same graph, correctly
  finds the longer detour is safe immediately and reports
  `recommended_hour = 0` — because it's actually searching for the best
  route each hour, not just re-checking one fixed path. Two more unit
  tests cover picking the *earliest* safe hour when the direct path only
  clears up partway through the window, and the no-path-exists-at-all
  case. A third confirms the same forecast-horizon hour-clamping
  guarantee `route_time_aware()` already has holds through this method's
  per-hour loop too.
- **Deliberately reuses, doesn't reimplement, the time-aware engine.**
  `route_best_departure()` is a thin per-hour loop over
  `route_time_aware()` — the already-tested arrival-hour-correct search —
  rather than a new hand-rolled Dijkstra, so its own correctness rests on
  code already proven correct, and any future fix to `route_time_aware()`
  automatically benefits this method too. Kept as its own method rather
  than folded into `route_advisory()` (unchanged, its existing tests and
  `scripts/hourly_update.py` callers untouched) — same "don't touch
  tested, relied-on code to add a new, different-shaped feature" pattern
  the previous four passes all followed.
- **Test count: 96 -> 105** (+4 `tests/test_routing.py`, +3
  `tests/test_api.py` request-validation paths for the new endpoint
  (unknown profile / missing coordinates / valid request reaches the
  router — matching the existing pattern for every other endpoint), +2
  `tests/test_integration.py` against real loaded data: one anchored to
  the already-established fact that `adult` never floods in this scenario
  (asserts `recommended_hour == 0`, every hour safe), one a
  self-consistency check for `vehicle_small` that doesn't overclaim
  specific hours the synthetic topology may or may not support a detour
  for). All 105 pass in `pytest -q` with nothing running (the 11
  DB-dependent integration tests ran for real against a live local
  Postgres this pass too, not skipped).
- **Live end-to-end verification**: regenerated the dev_seed scenario,
  booted a real `uvicorn` process, and curled the new endpoint directly.
  Confirmed live and worth recording honestly: `adult` is safe at every
  hour with `recommended_hour: 0` (matches the unit-test-proven logic and
  the established "adult never floods" fact); `vehicle_small` is safe
  only at hour 0 and unsafe hours 1-23, **matching** what
  `/api/route/advisory` already reported for the same trip — meaning in
  *this specific synthetic street topology* there is in fact no real
  vehicle detour around the flooded segment, so the two endpoints happen
  to agree here. That's not a bug or a wasted feature: it's the honest
  result for this data, and it's exactly why the synthetic
  `long_detour_graph()` unit test above exists — to prove the *algorithm*
  finds a detour when the *street network* actually has one, independent
  of whether this particular demo scenario's graph happens to have one
  available for vehicles. `time_aware=false` on `/api/route` was also
  re-confirmed byte-identical to its pre-existing shape (zero regression
  carried over from last pass, reconfirmed here since `routing.py` was
  touched again).
- **Frontend rewired to the richer endpoint**: the existing 24-cell hour
  strip (`renderAdvisory()`) now calls `/api/route/best_departure` instead
  of `/api/route/advisory` — same visual widget, but each safe hour's
  tooltip now shows the real route length/time for that hour instead of
  just "safe", and a new "Best time to leave: hour N — X km, ~Y min"
  callout appears above the strip, including a "(Z% longer than the
  direct route)" note when the recommended route isn't the ideal
  flood-blind path. `/api/route/advisory` itself is untouched and still
  live (kept for any lighter-weight caller that only needs the cheaper
  single-path check). Verified via `node --check` on the extracted inline
  script and, live, by curling the running server and confirming the new
  strings (`best_departure`, `renderAdvisory`, `Best time to leave`) are
  actually served.
- Docs updated: `docs/LIMITATIONS.md`'s note about `route_advisory()`
  only checking one fixed path now describes `route_best_departure()` as
  addressing that gap (with the honest caveat about the current demo
  data's topology above); `docs/NOVELTY.md` gets a new numbered
  differentiator.

## Done (2026-09-09, fifth pass — genuinely new feature work, not just
audit/bugfix: time-expanded routing + trip advisory)

This pass was deliberately different in kind from the previous four: those
were audits (real bugs found and fixed, coverage raised, no new user-facing
capability). This one retires an explicitly-documented Stage 6
simplification with a real algorithmic upgrade and adds a new endpoint,
without touching any previously-tested code path.

- **Time-expanded (arrival-hour-aware) routing — `Router.route_time_aware()`**
  (`tidestep/routing.py`). Until now, every route TideStep computed checked
  the hazard table once, at the hour the trip departs, and assumed that
  hazard state held for the whole trip — explicitly called out as a known
  simplification in `docs/LIMITATIONS.md`. For a walk or drive that's long
  enough to cross into the next forecast hour, that's wrong: a segment 20
  minutes into the trip is flooded or not based on the tide an hour later,
  not the tide when the traveler left home. `route_time_aware()` is a
  hand-rolled Dijkstra over `(elapsed_seconds, node)` state (not expressible
  as a static per-edge weight, so it can't reuse `networkx.dijkstra`): it
  converts each edge's length into actual time-on-segment (new
  `config.WALK_SPEED_MPS` for pedestrians; the vehicle profiles reuse
  osmnx's existing posted-speed-limit `travel_time`), tracks cumulative
  elapsed time along each candidate path, and checks the hazard state at
  the forecast hour a traveler would *actually* be on each segment,
  clamped to `config.MAX_HOUR`. Exposed as `time_aware=true` on the
  existing `/api/route` endpoint; **default is `false`, preserving the
  original departure-hour-only response byte-for-byte** — verified with a
  live curl comparison, not just reasoned about, so this is purely
  additive with zero regression risk to the existing route mode.
- **Proven, not just plausible**: `tests/test_routing.py` builds a new
  synthetic `long_detour_graph()` fixture with edge lengths chosen so that,
  at the configured walking speed, a direct path's cumulative travel time
  provably crosses a forecast-hour boundary where a hazard appears. The
  new test (`test_route_time_aware_avoids_hazard_that_appears_after_departure`)
  demonstrates concretely that the *old* departure-hour router recommends
  a path that turns out unsafe, while the *new* time-aware router
  correctly detours around it — the actual value of the feature, shown
  working, not asserted.
- **Trip advisory — new `/api/route/advisory` endpoint and
  `Router.route_advisory()`.** Answers a different, also-new question:
  "across the whole 24 h forecast, when is it safe to make *this specific
  trip*" — not a single hour's snapshot, and not "is my route blocked
  right now," but every hour's safe/unsafe status for the trip's usual
  path in one response. Deliberately implemented as its own method rather
  than reusing `Router.route_window()` (the existing predictive-alert
  mechanism `scripts/hourly_update.py` depends on, which only needs and
  returns the *first* unsafe hour for a fixed alert) — kept separate so
  this new read-only, ad-hoc-query feature carries zero risk to the
  already-tested alert loop.
- **Frontend wired to both**: `frontend/index.html` gets a "check hazard
  at actual arrival time, not just departure" checkbox that toggles
  `time_aware` on route requests and switches the result text to describe
  elapsed travel time and whether the trip crosses an hour boundary; and a
  new 24-cell green/red hour strip (`renderAdvisory()`) that calls
  `/api/route/advisory` whenever an origin/destination is set, giving an
  at-a-glance "safe now vs. wait until later" view for the exact trip
  being planned. Verified with `node --check` on the extracted inline
  script (including the nested ES6 template literals) and, live, by
  curling the running server and confirming both new DOM elements are
  actually served.
- **DRY housekeeping alongside the feature**: `MAX_HOUR` was a local
  constant duplicated in `api.py`; moved to `config.MAX_HOUR` so
  `routing.py` can share the same forecast-window bound without importing
  from `api.py` (a bad direction of dependency). `api.MAX_HOUR` kept as a
  backward-compatible alias.
- **Test count: 81 -> 96** (+7 `tests/test_routing.py` — the six behaviors
  above plus an hour-clamping test against a hazard source that raises if
  asked for an hour past the forecast horizon; +6 `tests/test_api.py` —
  every request-validation path for both new/changed endpoints, confirmed
  (via the existing monkeypatched-engine pattern) to reach the router and
  nothing further when valid, and to reject before touching the database
  when not; +2 `tests/test_integration.py` — both features hit through
  the real FastAPI app against a real local Postgres+PostGIS, not just
  the synthetic-graph unit tests). All 96 pass in plain `pytest -q`
  (nothing running required except the 9 already-conditional integration
  tests, which ran for real this pass, not skipped).
- **Live end-to-end verification, not just tests**: regenerated the
  dev_seed synthetic scenario, booted a real `uvicorn tidestep.api:app`
  process, and hit it with real `curl` requests: `time_aware=true`
  returns a route with the new `travel_time_min` / `arrival_hour` /
  `hour_crossed` properties; `time_aware=false` (the default) returns a
  response byte-identical in shape to the pre-existing format — proving
  zero regression; `/api/route/advisory` for `vehicle_small` correctly
  flags hour 7 unsafe, matching the already-established peak-hour ground
  truth from `test_route_differs_by_profile_at_peak`; and `curl / | grep`
  confirms the new `timeAware` checkbox and `advisory` strip are actually
  served by the running frontend.
- Docs updated to match: `docs/LIMITATIONS.md`'s "Hazard is evaluated at
  departure hour" item rewritten to describe the new opt-in time-aware
  mode (with the departure-hour-only default kept for compatibility, and
  a clear note that this is separate from `route_window()`'s alert-loop
  mechanism); `docs/NOVELTY.md`'s "What's actually new here" list gets two
  new entries (time-expanded routing; the trip-advisory hour strip).

## Done (2026-09-08, fourth pass — second real bug found one layer above the
first, dead code removed, coverage pushed from 74% to 95% on `tidestep/`)
- **Second real bug, same failure class as the near_inlet fix, one layer
  further downstream**: `scripts/hourly_update.py`'s `main()` only called
  `db.load_segments` (the only thing that pushes segments.gpkg's actual
  column values — near_inlet, ground_m, tags, geometry — into PostGIS)
  when the segments table's row *count* differed from the freshly-read
  `segments.gpkg`. A correction to segments.gpkg that doesn't change the
  segment count — exactly what the near_inlet caching fix produces —
  would then never reach the live database: the hourly cron loop would
  keep serving the stale row forever, silently, because the count check
  always matched. Concretely, this meant fixing `build_hazard.py` alone
  was not enough to actually get corrected `near_inlet` flags live: the
  operational loop would have swallowed the fix. Fixed by extracting
  `sync_db()` and always reloading segments unconditionally (cheap next
  to the DEM/floodfill/network work the script already does hourly).
  Regression-tested in the new `tests/test_hourly_update.py` against a
  real local Postgres, reproducing the exact same-count-different-value
  scenario and confirming the reload now actually happens.
- **Dead code removed**: `tidestep/segments.py`'s `_sample_points()` was
  defined but never called anywhere (superseded by the inline sampling in
  `sample_min_elevation`) — same class of leftover cruft as the
  `dev_seed.py` fix in the previous pass. Removed.
- **Coverage-driven gap found and closed**: `pytest --cov` showed
  `routing.py`'s `_highway_set()` list/tuple branch (OSM sometimes tags a
  simplified edge's `highway` with a list of values, not one string) was
  never exercised — added `test_highway_set_handles_list_valued_tags`
  confirming a vehicle is blocked only when *every* value in the list is
  non-drivable. Also added 5 new `tidestep/coops.py` tests (all pure
  monkeypatching, no network): the CO-OPS `{"error": ...}` response body
  is correctly raised as `RuntimeError` rather than silently treated as
  data, `check_datums()` genuinely catches a drifted station datum sheet
  (and passes when it matches), and `fetch_forecast_frame`'s bias-
  correction arithmetic (`ofs_navd88_m = ofs_raw_m - bias`,
  `nontidal_m = ofs_navd88_m - pred_navd88_m`) is checked directly,
  including that `bias_correct=False` genuinely skips the network call to
  `recent_ofs_bias` rather than just ignoring its result.
- **Net effect**: `tidestep/` package coverage 74% -> **95%**
  (`pytest --cov=tidestep`); test count 74 -> **81** (28 -> 81 across this
  and the previous two passes), still `pytest -q tests` runs the DB-free
  majority with nothing running, and 81 (up from 75) pass against a real
  local Postgres+PostGIS when one is reachable. Read `routing.py`, `api.py`,
  `db.py`, `hazard.py`, `floodfill.py`, `segments.py`, `streets.py`,
  `dem.py`, `coops.py`, `frontend/index.html`, `docker-compose.yml`,
  `README.md`, `docs/LIMITATIONS.md` fresh, independent of the previous
  passes' "confirmed correct" notes (which is exactly how the
  hourly_update.py bug above was caught — the previous pass's read of
  `db.py`/`api.py` alone wasn't enough to see a bug in how another script
  *calls* them). Remaining uncovered lines are defensive edge-case guards
  (empty/off-DEM/no-line-water-feature short-circuits) and one
  effectively-unreachable `except NetworkXNoPath` branch in
  `routing.Router.route()` (the baseline computation is strictly more
  permissive than the primary route, so if the primary succeeds the
  baseline provably cannot fail) — judged not worth contriving a test for.

## Done (2026-09-08, third pass — rigorous module-by-module audit against
docs/PIPELINE.md, test coverage gap closed, live end-to-end verification)
- **Read every remaining core module line by line against its pipeline
  stage** (`coops.py` vs Stage 1, `dem.py`/`streets.py` vs Stage 1,
  `segments.py`/`floodfill.py` vs Stage 2, `hazard.py`/`config.py` vs
  Stage 3, `db.py` vs Stage 4, and all four orchestration scripts) — the
  parts of the codebase the previous audit pass hadn't gotten to yet.
  Confirmed correct: datum conversion, OFS bias correction, DEM tiling and
  merge, `retain_all=True` bbox handling, the connected-flood-fill
  algorithm, the near-inlet stricter-threshold direction, the PostGIS
  schema and every query (including the profile-name SQL-injection guard
  in `unsafe_edges`), and the lat/lon argument order through
  `api.py -> db.py -> PostGIS` for saved routes.
- **Real bug found and fixed**: `scripts/build_hazard.py` cached
  `data/segments.gpkg` (expensive: DEM sampling) and, whenever that file
  already existed, silently skipped recomputing `near_inlet` — a cheap
  spatial join against `data/water.gpkg` that has nothing to do with why
  the file is cached. Concretely: the laptop's `segments.gpkg` was built
  before `water.gpkg` existed, with `near_inlet` all `False`; simply
  re-running `build_hazard.py` after fetching `water.gpkg` — which is
  exactly what the previous version of this file's "Next" section told the
  user to do — would silently keep every segment marked "not near an
  inlet" forever. Fixed by extracting a `refresh_near_inlet()` helper that
  always recomputes the flag and only rewrites the cache file when it
  actually changes; regression-tested in `tests/test_build_hazard_script.py`
  against exactly this stale-cache scenario.
- **Dead code removed**: `scripts/dev_seed.py`'s `synthetic_tide()`
  computed a tide curve, then immediately discarded it and computed a
  second, different one that was actually used — the first computation was
  leftover cruft from an earlier iteration. Removed; verified via the full
  `dev_seed.py` run below that the actual (second) curve is unchanged.
- **Test coverage gap fully closed.** Six new test files, all passing:
  `test_dem.py` (DEM tiling/merge math, exercised for real by monkeypatching
  only the network call), `test_streets.py` (bbox ordering, the osmnx-2.x
  `osmid` rename, graph/water caching), `test_segments.py` (line-splitting
  edge cases, OSM tag normalization), `test_db.py` (schema, round-trips,
  `unsafe_edges`' injection guard, `edge_hazard`'s aggregation, saved-route
  CRUD — against a real local PostGIS), `test_api.py` (every request-
  validation rejection path — malformed bbox, out-of-range hour, unknown
  profile — confirmed to never touch the database at all), and
  `test_build_hazard_script.py` (the near_inlet regression above). Test
  count: **28 -> 74**, all passing, `pytest -q tests` still needs nothing
  running for the majority of them (only `test_db.py`/`test_integration.py`
  need a reachable Postgres, and skip cleanly without one).
- **Live end-to-end verification, not just tests**: got a local Postgres +
  PostGIS running directly in this Claude sandbox (the `postgresql`/`postgis`
  packages were already installed; `docker`'s daemon isn't startable here,
  so this bypassed Docker entirely), ran the complete test suite against it
  (74 passed), ran `python scripts/dev_seed.py` end to end, then actually
  booted `uvicorn tidestep.api:app` as a live server and hit it with `curl`
  — confirmed `/api/hours` returns 24 hours, `/api/route` returns a real
  route for `adult` at the synthetic peak hour and correctly returns
  "no safe route at this hour" for `vehicle_small` at the same hour/trip,
  and `/` serves the frontend with a 200. This sandbox still cannot reach
  NOAA (`curl` to `api.tidesandcurrents.noaa.gov` gets a proxy 403, as
  documented in `CLAUDE.md`), so Stage 9 validation and real-data fetches
  are still laptop-only — but everything downstream of a fetch has now been
  verified live, not just read and reasoned about.

## Done (2026-09-08 — laptop set up for real, first real data + two bugs found)
- **The laptop is now a fully working dev environment.** In order, fixed:
  a `.git/index.lock` left over from a bad shutdown, a README that only
  documented Unix venv activation (now has a separate Windows PowerShell
  block + troubleshooting section), `.venv` built against a too-old Python
  (3.10.11 — below the 3.11+ `requirements.txt` needs) rebuilt with
  `py -3.12 -m venv .venv`, and Docker Desktop installed but never launched.
  `pip install -r requirements.txt` now succeeds with real Windows wheels
  for every package.
- **Real data fetched for the first time on the corrected bbox**:
  `python scripts/fetch_all.py` and `python scripts/build_hazard.py` both
  ran against live NOAA/USGS/Overpass from the laptop (neither Claude
  sandbox has that egress). `docker compose up -d` + `python
  scripts/load_db.py` loaded it into PostGIS, and `uvicorn
  tidestep.api:app --reload` started cleanly ("Application startup
  complete") — the full pipeline has now genuinely run end to end on real
  data, not just against `dev_seed.py`'s synthetic fixture.
- **Bug fix, found by rigorous line-by-line audit, not by running it**:
  `scripts/hourly_update.py`'s predictive-alert message used
  `strftime("%-I:%M %p %Z")` — `%-I` is a glibc/macOS-only strftime
  extension and raises `ValueError` on Windows. Since the user runs
  `hourly_update.py` directly on Windows (it's not in the Docker
  container — only Postgres is), this would have crashed the first time a
  real predictive alert fired (`first_unsafe_hour > 0`), silently killing
  the app's most novel feature. Fixed with the portable equivalent:
  `strftime("%I:%M %p %Z").lstrip("0")`. Confirmed via
  `grep -rn "%-[A-Za-z]"` that this was the only `%-`-style format code
  anywhere in the repo.
- **Docs fix**: `docs/LIMITATIONS.md`'s "Alerts" section still described
  the pre-`route_window()` behavior (checked forecast_hour=0 only) as a
  current limitation, contradicting the predictive-alerting feature that
  had already superseded it earlier in the session. Rewrote it as a
  superseded note and added a new, real, previously-undocumented gap found
  in the same audit: **no authentication on `/api/routes`** — anyone with
  the API URL can view or delete any saved route. Fine for a single-user
  demo, would need accounts before a real multi-user deployment.
- **Full-repo audit, everything else reviewed and confirmed correct, no
  further bugs found**: `tidestep/api.py`, `tidestep/routing.py`,
  `tidestep/config.py`, `docker-compose.yml`, `tidestep/validate.py`,
  `frontend/index.html`, and all 9 iOS Swift files — including a line-by-
  line cross-check of every Swift `Codable` struct's JSON field names
  against the actual Python API/DB response shapes (`Models.swift` vs.
  `api.py`/`db.py`). No mismatches found. (Caveat, unchanged: this is
  correctness-by-inspection only — neither Claude sandbox has a Swift
  toolchain, so the iOS app has still never actually compiled.)

## Done (earlier this session — code + repo hygiene, no data refetch: no
NOAA/USGS/Overpass egress from either Claude sandbox used, same restriction
the laptop sandbox already documented at the time)
- Repo hygiene: `cache/` untracked and gitignored (was accidentally
  committed, ~9.4 MB of regeneratable Overpass cache), `requirements.txt`
  pinned to exact versions the full test suite passed against,
  `requirements-dev.txt` added (pytest + httpx, for test_integration.py).
- `docs/LIMITATIONS.md`: Stage 10 write-up, consolidated from the
  docstring-level caveats already in the code.
- **Bug fix, found via integration testing**: `scripts/dev_seed.py` (new —
  see below) first serialized its synthetic graph with plain
  `networkx.write_graphml`; loading it through `ox.load_graphml` (what
  `api.py`/`hourly_update.py` actually do) crashed with
  `AttributeError: 'float' object has no attribute 'startswith'`.
  Production code was never affected (`streets.py` already uses
  `ox.save_graphml`/`ox.load_graphml` symmetrically) but this is exactly
  the failure class integration testing exists to catch, and the unit
  tests couldn't have caught it since they don't touch a real save/load
  round trip.
- **`scripts/dev_seed.py`** (new): a small, clearly-synthetic "Cove Harbor"
  scenario (DEM + street graph + water polygons + a 24 h tide curve) that
  exercises the entire pipeline without network. Two purposes: (1)
  integration test fixture, (2) an offline demo fallback — if live NOAA/USGS
  access is flaky right before the actual demo, `python scripts/dev_seed.py`
  gives a fully working, clearly-labelled-synthetic app to show instead of
  no app at all.
- **`tests/test_integration.py`** (new): drives the real FastAPI app (via
  `TestClient`, no separate server process) against a real local
  Postgres+PostGIS with the dev_seed scenario loaded — the first tests that
  exercise `db.py`'s actual SQL and `api.py`'s actual routing together,
  rather than mocking them. Skips itself automatically if no DATABASE_URL
  is reachable, so plain `pytest -q tests` still needs nothing running.
  Confirmed live: per-profile hazard differentiation genuinely holds
  end-to-end (adult has a route at the synthetic peak hour, vehicle_small
  and child correctly get "no safe route"), saved-route CRUD works against
  real Postgres, and the previously-silent `?hour=999` case is now a 422.
- **Predictive alerting** (`tidestep/routing.py: Router.route_window`,
  `scripts/hourly_update.py`): the alert loop previously only checked
  forecast_hour=0 ("is it flooded right now") despite its own comment
  claiming it checked the next 24 h. It now actually scans the full
  window and reports the *first* hour the saved route's usual path
  becomes unsafe, so an alert can read "floods starting around 4:00 PM
  today" instead of only firing once the flooding has already happened.
  Verified against the dev_seed scenario: child route flags unsafe
  starting hour 3, vehicle_small starting hour 1, adult never — matching
  the underlying depth data exactly. 3 new unit tests
  (`test_route_window_*` in `tests/test_routing.py`).
- **`tidestep/validate.py` + `scripts/validate_stage9.py`** (new, Stage 9):
  pulls real historical observed water levels at Kings Point for a sample
  of past days, reruns the actual hazard pipeline against them, and checks
  the model's core claim — floods when the gauge exceeds NWS minor stage,
  stays dry when it doesn't — reporting sensitivity/specificity. This
  needs NOAA network access to actually run (`python
  scripts/validate_stage9.py --days 30`, from a normal terminal); the
  logic itself has 5 passing unit tests against synthetic historical data
  (`tests/test_validate.py`) so it's proven correct and ready the moment
  it's run for real.
- `api.py` / `db.py` hardening: the `?hour=` bound was hardcoded to 48
  (inconsistent with `config.FORECAST_HOURS = 24`) — now derived from
  config. `db.unsafe_edges` now validates `profile` against the known set
  before interpolating it into a column name (defense in depth; the API
  layer already validated it, this is the actual SQL boundary).
- Test count: 10 -> **28 passing** (13 original-suite + 7 integration +
  5 validate + 3 `test_dev_seed.py`, added with the graph-path bug fix
  below).
- **Second bug fix, found by actually running the demo path end to end**
  (not just its unit tests): `scripts/dev_seed.py` wrote its synthetic
  street graph to `data/dev_seed_streets.graphml`, but `tidestep/api.py`'s
  `router()` always loads the hardcoded `streets.GRAPH_PATH`
  (`data/streets.graphml`) — a fresh clone had no file there at all
  (`/api/route` would 500), and on a machine with real fetched data already
  present, the API would route against real Long Island street coordinates
  while segments/hazard came from the synthetic scenario centered miles
  away, silently returning nonsense instead of a working demo. Fixed by
  having `dev_seed.py` write straight to `streets.GRAPH_PATH` (backing up
  any real graph already there to `.real-backup` first, never overwritten
  by a repeat run). Verified for real, not just by reasoning about it: ran
  `python scripts/dev_seed.py` against a live local Postgres, then hit
  `/api/hours`, `/api/risk?hour=7`, and `/api/route` through the actual
  `api.app` object — adult gets a routed path at the synthetic peak hour,
  `vehicle_small` correctly gets `no safe route at this hour`. `docs/NOVELTY.md`
  (new) written: cross-references TideStep's street-level/predictive/routed/
  per-profile approach against specific 2025 CAC winners (CoFIS, Watershape,
  RoadWatch, BoostT1D, VERDIS/OptiSense/Computerpreter), researched live
  rather than from memory.
- iOS app (`ios/`, new): full SwiftUI client — `Models.swift` (Codable
  structs matching `api.py`'s JSON field-for-field), `APIClient.swift`
  (async/await networking, runtime-configurable backend URL),
  `TideStepViewModel.swift` (state, per-hour risk cache, routing, autoplay,
  saved routes), `RiskMapView.swift` (iOS 17 MapKit risk polylines,
  tap-to-route via `MapReader`/`SpatialTapGesture`), `ContentView.swift`
  (map + control panel + time slider, matching `frontend/index.html`'s
  layout and hazard colors exactly), `SavedRoutesView.swift`,
  `SettingsView.swift`. Reviewed line-by-line against `api.py`'s actual
  response shapes and Apple's documented MapKit SwiftUI API, but **not
  compiled** — no Swift/Xcode toolchain in either Claude sandbox; see
  `ios/README.md` for exact Xcode setup steps and honestly-scoped known
  gaps (no offline cache, no push notifications, no CoreLocation).

## Done (earlier)
- Stage 0: scope locked (docs/SCOPE.md, tidestep/config.py). Station =
  Kings Point 8516945, bbox = Manhasset Bay, 24h hourly, still-water ponding.
- Stage 1 (code): `tidestep/coops.py` (NYOFS + predictions + datums, hourly
  max, MLLW->NAVD88), `tidestep/dem.py` (3DEP ImageServer bbox export, tiled
  + merged), `tidestep/streets.py` (osmnx road graph + OSM water features).
  `scripts/fetch_all.py` runs all of it. Unit tests in `tests/` pass.

- Stage 1 (data): water levels fetched on the laptop (24 rows, NYOFS peak
  1.59 m NAVD88 on 2026-09-07 00Z, just under NWS minor 1.77 m).
- Stages 2-3 (code): `tidestep/segments.py` (15 m segments, min DEM
  elevation + its pixel), `tidestep/floodfill.py` (seed mask from
  nodata/MLLW/OSM water, scipy.ndimage.label connectivity, bathtub kept for
  comparison), `tidestep/hazard.py` (depth, inlet factor, per-profile safe
  flags). `scripts/build_hazard.py` runs them. Synthetic-terrain tests pass,
  including the cut-off-basin case the bathtub model gets wrong.

- Stages 2-3 (data): `scripts/build_hazard.py` ran on the real DEM
  (3913 x 4214 px, 1 m) and street graph (770 nodes / 1944 edges -> 5830
  segments) in 18 s. At the 2026-09-07 00Z peak (1.59 m NAVD88) 60 segments
  flood, all waterfront footways/paths (Manhasset Bay Walk etc.), no roads
  — consistent with the level being below NWS minor stage (1.77 m).
  Seed rule changed from MLLW to `SEED_ELEVATION_M = -1.0` because 3DEP
  hydro-flattens the bay at about -1.1 m NAVD88 (see config.py).

- OFS bias check (2026-09-06, last 48 h): obs were +0.38 m above
  predictions (real non-tidal water), OFS was +0.26 m above obs. So the
  0.4-1.1 m OFS-minus-predictions gap was part weather, part model bias.
  `fetch_forecast_frame` now subtracts the trailing 48 h mean OFS-obs bias
  (columns ofs_raw_m / ofs_bias_m / ofs_navd88_m in water_levels.csv).

- Stages 4-8 (code, tested end to end against a local PostGIS in Claude's
  sandbox with the real Sept 6 data): `docker-compose.yml` + `tidestep/db.py`
  (schema, loaders, GeoJSON + unsafe-edge queries, saved routes),
  `tidestep/api.py` (FastAPI: /api/hours, /api/risk, /api/route,
  /api/routes), `tidestep/routing.py` (Dijkstra with unsafe edges removed,
  vehicle profiles snap to drivable nodes, baseline comparison),
  `frontend/index.html` (Leaflet, hour slider with play button, click-to-
  route, per-profile colouring; Leaflet vendored so the demo works offline),
  `scripts/hourly_update.py` (fetch -> hazard -> PostGIS -> saved-route
  check -> email/log alert). 10 unit tests pass.

## Bug found and fixed: study area
The first bbox left the Kings Point gauge outside and, because the two
shores of Manhasset Bay only connect by road south of the box, osmnx's
default largest-component filter silently dropped the entire west shore
(graph had nodes only between lon -73.711 and -73.700). Fixed:
`BBOX = (40.795, -73.775, 40.845, -73.695)` and `retain_all=True`.
**data/ must be regenerated**: delete `data/dem_1m.tif`,
`data/streets.graphml`, `data/water.gpkg`, `data/segments.gpkg`, then run
fetch_all -> build_hazard -> load_db again. DEM will be ~4x larger
(about 150 MB); build_hazard should take about a minute.

## Next (the laptop is now a working environment — these are all runnable
there today; nothing left is blocked on tooling)
1. **Commit and push.** This pass's fixes (and everything from every
   previous pass) is not on `origin/main` yet — see "Uncommitted work"
   below for the exact commands. Do this first so nothing above is
   sitting only on disk. **Coordinate with your teammate before this
   one** — see the eighth pass's concurrent-edit note above: two sessions
   were mid-edit on `tidestep/routing.py` at once, and this repo's
   `.git` working tree is shared, so whoever runs `git add`/`commit`
   first should let the other know, to avoid the same kind of collision
   happening again at the git layer instead of the filesystem layer.
1a. Once pushed, feature-work candidates worth considering next: (i)
   ~~stop-order optimization for multi-stop trips~~ **done this pass** —
   see `route_multi_stop_optimized()` above; (ii)
   **real shelter locations for `route_to_safety()`** — it currently
   treats any dry street segment as a valid haven; loading an actual POI
   layer (schools, firehouses) would make its answer meaningfully more
   useful, also flagged in `docs/LIMITATIONS.md`. **Confirmed this pass:
   not buildable from either Claude sandbox** — `curl` to both
   `overpass-api.de` and `nominatim.openstreetmap.org` gets a proxy
   `connect_rejected` (same class of restriction `CLAUDE.md` already
   documents for NOAA/USGS), so fetching an OSM POI layer for this needs
   the laptop, same as every other real-data fetch in this project; (iii)
   push
   `route_best_departure()`'s per-hour search from a full 24-hour sweep
   down to only the hours between two changes in safety state, to cut its
   DB-query count if it ever needs to run against a much larger street
   graph than this bbox's; (iv) ~~iOS screens for `/api/route/best_departure`
   and `POST /api/route/multi_stop`~~ **done this pass (tenth)** — see
   `ContentView.bestDepartureStrip`/`multiStopPanel` above;
   `/api/route/advisory` was confirmed to need no screen (see `ios/README.md`).
2. `python scripts/validate_stage9.py --days 30` — Stage 9 has never
   actually been run anywhere. The logic is unit-tested and ready
   (`tests/test_validate.py`, 5 passing); this just needs to hit NOAA for
   real, which the laptop can now do (confirmed again this pass: this
   Claude sandbox still gets a proxy 403 to api.tidesandcurrents.noaa.gov).
   Paste the printed sensitivity/specificity into the submission's
   technical-challenges answer.
3. Re-run `build_hazard.py` **and then `load_db.py` (or restart
   `hourly_update.py`'s cron loop, now that its own reload bug is fixed)**
   on the laptop's real data — this is what actually gets corrected
   `near_inlet` flags into the *live* database. Both halves of this used
   to be silently broken: `build_hazard.py` alone wouldn't have recomputed
   the flag (fixed last pass), and even after that fix, `hourly_update.py`
   alone wouldn't have pushed a same-row-count correction into Postgres
   (fixed this pass). Re-running just one of the two would not have been
   enough — worth doing deliberately now that both are fixed, not just
   trusting "it'll pick it up on the next cron tick."
4. Record demo footage: the time slider across a real flood cycle, then
   the profile switch showing the same trip flood-blind vs. flood-aware
   for adult vs. vehicle. The app is confirmed running end to end on real
   data (and, this pass, on a live local server hit with real HTTP
   requests), so this is unblocked.
5. iOS app: see `ios/README.md` — Swift source is written, reviewed line
   by line against the real API/DB response shapes, and ready to open in
   Xcode, but has never actually compiled — needs a Mac, since neither
   Claude sandbox can run a Swift toolchain. This is the single biggest
   unverified risk left in the project.

Test coverage gap (previously item 5 here) is **done** — see the top two
sections: `test_dem.py`, `test_streets.py`, `test_segments.py`, `test_db.py`,
`test_api.py`, `test_build_hazard_script.py`, `test_hourly_update.py` all
added and passing; `tidestep/` package coverage is 95% (`pytest --cov`).

## Uncommitted work
Everything from this session and the previous ones is still sitting
uncommitted (repo policy: Claude never runs `git add`/`commit`/`push` —
see `CLAUDE.md`). `scripts/hourly_update.py`'s *portable-strftime* fix,
`docs/LIMITATIONS.md`, and an earlier `docs/STATUS.md` are already
committed and pushed (`8d779c6`, confirmed via `git log origin/main`).
Everything else described in this file is still local-only. Files touched
**this pass (tenth)**:
- Modified: `ios/TideStep/Models.swift` (`optimize_order`-only fields on
  `MultiStopTripProperties`, `optimizeOrder` on `MultiStopRequest`),
  `ios/TideStep/APIClient.swift` (`routeMultiStop()` gains
  `optimizeOrder` param), `ios/TideStep/TideStepViewModel.swift`
  (`bestDeparture` published state + `loadBestDeparture()`; multi-stop
  published state + `addStop()`/`clearStops()`/`planTrip()`),
  `ios/TideStep/RiskMapView.swift` (`HazardColor.legColors`/`forLeg()`,
  tap-gesture branch on `multiStopMode`, stop-marker and per-leg-polyline
  drawing), `ios/TideStep/ContentView.swift` (`bestDepartureStrip`,
  `multiStopPanel`, `tripSummary()`), `ios/README.md` ("API coverage" /
  "Known gaps" rewritten), `docs/STATUS.md` (this section).
- From the ninth pass (already uncommitted, unchanged by this pass):
  `tidestep/routing.py` (`route_multi_stop_optimized()`,
  `OptimizedTripPlan`, `MAX_OPTIMIZE_STOPS`), `tidestep/api.py`
  (`optimize_order` field + branch on `POST /api/route/multi_stop`),
  `frontend/index.html` ("find the best order to visit stops in"
  checkbox, visit-order relabeling, non-200 error display),
  `tests/test_routing.py` (+4), `tests/test_api.py` (+4),
  `tests/test_integration.py` (+2), `docs/LIMITATIONS.md`,
  `docs/NOVELTY.md` (+1 entry).
- From the seventh pass and earlier (already uncommitted, unchanged by
  this pass): `tidestep/db.py` (`always_safe_nodes()`), the rest of
  `tidestep/routing.py`/`api.py`/`frontend/index.html`'s prior feature
  work, `README.md`, `tidestep/config.py`, `tidestep/segments.py`,
  `scripts/build_hazard.py`, `scripts/hourly_update.py`,
  `requirements-dev.txt`, `scripts/dev_seed.py`,
  `scripts/validate_stage9.py`, `tidestep/validate.py`, and every earlier
  test file — nothing described anywhere in this file has reached
  `origin/main` since `8d779c6`.

Suggested split, each buildable in one `git add` + `git commit`:
1. `docs/STATUS.md docs/NOVELTY.md docs/LIMITATIONS.md README.md` — docs.
2. `tidestep/ scripts/ tests/ requirements-dev.txt` — code + tests (every
   feature and fix across all nine passes: predictive alerting, dev_seed
   fixture, validate.py, api/db hardening, the near_inlet caching fix, the
   hourly_update.py sync_db fix, time-aware routing + trip advisory,
   route_best_departure(), multi-stop trips + evacuation routing, and
   this pass's route_multi_stop_optimized()).
3. `frontend/index.html` — the UI for every routing feature above (small
   enough to call out on its own so a reviewer can see exactly what
   changed in the demo-facing surface).
4. `ios/` — the SwiftUI client, on its own since it's unreviewed by any
   compiler.
Or, more simply, one commit for everything:
```
git add -A
git commit -m "routing: brute-force stop-order optimization + iOS best-departure/multi-stop screens"
git push
```
**Coordinate with your teammate before running this** — this repo's
`.git` working tree is shared between both of your sessions (see the
eighth pass's concurrent-edit note above), so confirm neither of you has
uncommitted work the other doesn't know about before either of you runs
`git add`/`commit`.

## Notes
- `ofs_water_level` returns 6-minute data; we take the hourly max.
- CO-OPS gaps come back as empty strings; `_to_series` drops them.
