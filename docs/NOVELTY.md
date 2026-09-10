# Why TideStep is different — and why that matters for judging

This is a working document, not marketing copy: every claim below is checked
against what TideStep's code actually does (see `tidestep/floodmodel.py`,
`tidestep/hazard.py`, `tidestep/routing.py`) and against public descriptions
of specific past Congressional App Challenge (CAC) winners, cited by name.
Where a comparison app's exact internals aren't publicly documented, this
file says so rather than guessing.

## The one-sentence version

Most flood apps answer "how bad could it get" for a river reach or a whole
county. TideStep answers "will *this specific street* be underwater at
*this specific hour*, and is it safe for *this specific person* to be on
it" — turning a regional hazard forecast into a street-level, per-person,
time-stamped decision, tied to routing you'd actually use to get somewhere.
That shift — hazard map to decision tool — is where the novelty lives, and
it's a pattern that shows up across the CAC winners judges have already
rewarded (below).

## Head-to-head with the closest existing entries

### CoFIS (Community-Oriented Flood Information System) — Kento Sugiyama, IA-01, 2025 special-category winner

CoFIS is the most directly comparable prior app: a browser-based tool for
"scenario-based flood inundation mapping and impact analysis" that lets
communities view inundation scenarios and estimated damage across U.S.
river reaches, computed client-side in the browser. It's a genuinely
strong entry and a good technical benchmark — which is exactly why the
differences matter:

- **Scenario vs. forecast.** CoFIS is explicitly *scenario-based* — a user
  picks a hypothetical water level ("what if the river hits 14 ft") and
  sees the modeled inundation. TideStep doesn't ask the user to pick a
  scenario; it pulls NOAA's actual NYOFS storm-surge-corrected tide
  forecast for the next 24 hours (`tidestep/coops.py`) and tells you what
  *will* happen, hour by hour, starting from now. A user opens the app the
  morning of a king tide and sees "Cove Rd floods at 2pm today," not a
  slider they have to reason about themselves.
- **River reach vs. street segment.** CoFIS's unit of output is a river
  reach — useful for regional risk communication, not for "can I drive to
  work." TideStep's unit of output is an individual OSM street edge
  (`tidestep/segments.py`), each with its own minimum elevation sampled
  from 1m LiDAR and its own computed flood depth for the current forecast
  hour.
- **No routing, no personalization.** CoFIS shows you where water will be;
  it doesn't tell you whether you personally can safely cross it, or how
  to get where you're going instead. TideStep's hazard model
  (`tidestep/hazard.py`) applies published depth×velocity safety
  thresholds separately for children, adults, and three vehicle classes
  (UK Environment Agency FD2321; UNSW Water Research Laboratory), and its
  router (`tidestep/routing.py`) computes an actual flood-avoiding path
  and can tell you the exact hour your usual route becomes impassable
  (`Router.route_window`, `scripts/hourly_update.py`).

None of this makes CoFIS worse at what it's for — regional flood-impact
communication is a real, different problem. It's evidence that flood
modeling is a proven, judge-rewarded CAC category, and that TideStep's
specific angle (street-level, per-person, predictive, routed) is the part
of that space CoFIS and similar entries leave open.

### Watershape — Jason Powell, SC-07, 2025 regional winner

Watershape is a procedurally generated weather/flood *simulation* — users
randomize invented terrain, add water, and trigger storms to explore fluid
dynamics interactively. It's a strong educational tool for understanding
how flooding behaves in the abstract. TideStep is the applied inverse: no
generated terrain, no hypothetical storms — every input is a real
measurement (NOAA tide gauge readings, USGS LiDAR elevation, real OSM
streets) about one real place, and the output is usable the same day by
someone deciding whether to drive down Cove Road. Watershape teaches how
floods work; TideStep tells you if the flood is happening under your feet
this afternoon.

### RoadWatch — Vaibhav Sitaraman & Eric Dai, NJ-06, 2025 special-category winner

RoadWatch uses AI-powered dashcams to detect and report road hazards
(potholes, broken streetlights) after the fact, for municipal repair
prioritization. It's a strong example of the pattern "instrument the road
network, act on individual segments" — which TideStep also does — but for
a reactive, infrastructure-maintenance problem rather than a predictive,
personal-safety one. TideStep's segments carry a forecast (what will
happen in the next 24 hours), not a damage report (what already happened).

### BoostT1D — Aaron Prager, MA-04, 2025 national winner

BoostT1D is outside TideStep's domain (Type 1 diabetes management via AI
food-photo insulin estimation) but its winning pattern is directly
relevant to how TideStep should be judged: it doesn't invent a new medical
device, it takes an established clinical practice (carb counting for
insulin dosing) and makes it fast and low-friction with a phone camera.
The novelty judges rewarded was in *removing friction from a decision
someone already has to make*, not in the underlying domain being new.
TideStep follows the same shape — coastal residents already informally
track "does my road flood at high tide"; TideStep replaces guesswork and
tide-table math with a phone-glance answer and a routed alternative.

### VERDIS, OptiSense, Computerpreter — 2025 regional/national winners, pattern reference

These three (drone-based crop-disease detection, optical non-invasive
glucose sensing, real-time ASL translation) share nothing domain-specific
with TideStep, but share the trait that got them recognized: each takes a
public dataset or sensor stream that already exists (multispectral
imagery, optical reflectance, video) and a published, peer-reviewed
methodology for interpreting it, and packages that into a tool a
non-specialist can use immediately. TideStep does the same thing with
public NOAA tide/surge forecasts, public USGS LiDAR, and published
flood-hazard engineering thresholds (FD2321, UNSW WRL) — no invented
science, a real engineering synthesis of existing published science into a
tool nobody had packaged this way for this problem.

## What's actually new here, stated plainly

1. **Forecast-driven, not scenario-driven, at street granularity.** Every
   comparable flood-mapping CAC entry found in this research works from a
   user-chosen or historical scenario. TideStep is the only one built
   around a live, hours-ahead operational forecast (NOAA NYOFS) resolved
   down to individual street segments.
2. **Hydrologically-connected flood extent, not a bathtub fill.** TideStep's
   flood-fill (`tidestep/floodmodel.py`) uses connected-component labeling
   seeded from open water, so a low-lying inland basin that isn't actually
   plumbed to the sea is correctly *not* flagged as flooded even though its
   elevation is below the tide level — matching NOAA's own Sea Level Rise
   Viewer methodology rather than the naive "everything below the water
   line is wet" approach most hobbyist flood-map projects use. This is
   covered by a real regression test
   (`tests/test_floodmodel.py::test_connectivity_excludes_cut_off_basin`)
   built specifically to catch the naive-bathtub bug if it's ever
   reintroduced.
3. **Safety is a published engineering standard, not a guess.** The
   child/adult/vehicle thresholds come from cited flood-hazard-to-people
   research (UK Environment Agency FD2321's depth×velocity hazard rating,
   UNSW Water Research Laboratory), the same category of source UK and
   Australian emergency-management agencies use for real evacuation
   planning — not an invented "looks risky" cutoff.
4. **Predictive alerting, not just a map.** `scripts/hourly_update.py`
   doesn't just check "is my route blocked right now" — `Router.route_window`
   scans the full forecast window per saved route and tells a user *which
   hour* their usual route will first become unsafe, before it happens.
5. **Cross-platform from day one.** The FastAPI backend
   (`tidestep/api.py`) is the single source of truth for both the Leaflet
   web frontend and the native SwiftUI iOS app (`ios/`) — same hazard
   colors, same routing logic, same data, two real clients, not a web demo
   with an iOS mockup bolted on.
6. **Time-expanded routing, not a static-weight shortcut.** Nearly every
   flood-routing demo (including TideStep's own first version) checks
   hazard once at departure and calls it done — reasonable when a trip is
   short, wrong in general, because a segment 20 minutes into a walk is
   flooded or not based on the tide an hour later, not the tide when you
   left. `Router.route_time_aware()` (`tidestep/routing.py`) is a real
   time-expanded Dijkstra: it converts trip progress into elapsed time
   using per-profile walking speed or the street graph's actual posted-
   speed travel times, and re-checks the hazard table at the forecast
   hour a traveler would truly be on each segment. This is proven, not
   just claimed — `tests/test_routing.py` builds a synthetic detour graph
   where a hazard appears only after the direct path's normal travel
   time, showing the old departure-hour router gives an unsafe answer and
   the new one gives the correct one.
7. **"When is it safe to make this specific trip today," not just "is it
   safe right now."** The `/api/route/advisory` endpoint
   (`Router.route_advisory()`) reports safe/unsafe for a given trip across
   the *entire* 24 h forecast window in one call — turning "check the map
   at noon, check it again at 6" into a single glance answering "when
   today can I make this trip."
8. **"Best way there now," not just "is my usual way blocked."**
   `route_advisory()` (above) still only checks one fixed, flood-blind
   path against each hour — so if that one path floods all day, it
   reports the trip as unsafe all day, even if a real detour would get
   someone there safely right now. `Router.route_best_departure()`
   (`/api/route/best_departure`) closes that gap: it recomputes the
   actual best route for every hour in the forecast window (reusing the
   time-expanded router from item 6), and surfaces the earliest hour a
   real route exists at all, detour included. The frontend's hour strip
   is powered by this endpoint, with a "best time to leave" line showing
   the recommended hour, the route's length/time, and how much longer it
   is than the ideal flood-blind path. Proven with a constructed case
   (`tests/test_routing.py::test_route_best_departure_finds_a_safe_detour_advisory_would_call_unsafe`)
   where the coarser advisory check reports a trip impossible all day
   while the best-departure planner correctly finds a safe detour
   available immediately.

## What TideStep does *not* claim

Being honest here is itself part of a strong submission — judges can tell
when a scope claim is inflated. TideStep does not do real-time river-flow
routing (VERDIS-style multispectral analysis is out of scope), does not
model wave run-up or storm-surge dynamics beyond NOAA's own NYOFS output
(see `docs/LIMITATIONS.md`), and its hydraulic model is still-water ponding
only — it does not simulate flow velocity except as a coarse
near-inlet flag. These are stated explicitly in `docs/LIMITATIONS.md` and
are the honest boundary of a project built by two high schoolers on public
data in a few months, not a claim to have solved coastal hydrodynamics.

## Sources

- CoFIS: [congressionalappchallenge.us/25-ia01](https://www.congressionalappchallenge.us/25-ia01/)
- Watershape: [congressionalappchallenge.us/25-sc07](https://www.congressionalappchallenge.us/25-sc07/)
- 2025 Top Apps roundup (BoostT1D, VERDIS, OptiSense, Computerpreter,
  RoadWatch, CoFIS): [congressionalappchallenge.us/meet-the-2025-cac-top-apps-winners-presented-by-thecoderschool](https://www.congressionalappchallenge.us/meet-the-2025-cac-top-apps-winners-presented-by-thecoderschool/)
- UK Environment Agency, FD2321 "Flood Risks to People" — depth×velocity
  hazard rating methodology.
- UNSW Water Research Laboratory — pedestrian and vehicle flood hazard
  technical reports.
