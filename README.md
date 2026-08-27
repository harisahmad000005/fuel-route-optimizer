# Fuel Route Optimizer

A Django REST API that calculates a cost-effective fuel plan for a driving route within the United States.

The API accepts a start and finish location, calculates the driving route, finds fuel stations along that route, and returns a recommended set of fuel stops with the estimated fuel consumption and cost.

The project was built as a backend coding assessment focused on route calculation, geospatial station matching, fuel optimization, and API design.

## Table of Contents

- [Assignment](#assignment)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Vehicle Assumptions](#vehicle-assumptions)
- [Fuel Optimization Strategy](#fuel-optimization-strategy)
- [How the Algorithm Works](#how-the-algorithm-works)
- [Worked Example](#worked-example)
- [Why Not Simply "Choose the Cheapest Station"?](#why-not-simply-choose-the-cheapest-station)
- [Station Dataset](#station-dataset)
- [Route Caching](#route-caching)
- [API](#api)
- [Fuel Calculation](#fuel-calculation)
- [Edge Cases](#edge-cases)
- [Data Validation](#data-validation)
- [Design Rationale](#design-rationale)
- [Key Design Decisions](#key-design-decisions)
- [Limitations](#limitations)
- [Future Improvements](#future-improvements)
- [Testing](#testing)
- [Running With Docker](#running-with-docker)
- [Local Configuration](#local-configuration)
- [Summary](#summary)

---

## Assignment

The assignment was to build an API that:

1. Accepts a start location and finish location, both within the USA.
2. Calculates the driving route between them.
3. Returns the route geometry so it can be displayed on a map.
4. Finds fuel stations along the route.
5. Uses the provided fuel-price dataset.
6. Determines cost-effective locations to refuel.
7. Supports routes longer than the vehicle's maximum range.
8. Returns the recommended fuel stops and total fuel cost.

The goal is not simply to choose the cheapest station. The optimizer has to consider:

- How much fuel remains
- How far the next stations are
- Fuel prices
- Vehicle tank capacity and fuel economy
- Whether the destination is reachable
- Whether a cheaper station can be reached without refueling first

---

## Architecture

The application is built with:

- Python
- Django + Django REST Framework
- PostgreSQL
- Docker / Docker Compose
- Shapely, PyProj
- External geocoding API
- External routing API

**High-level flow:**

```text
Client
  |
  v
Django REST API
  |
  +---- Geocode start location
  +---- Geocode finish location
  |
  v
Route Service
  +---- Calculate driving route
  |
  v
Station Matcher
  +---- Find stations near route
  +---- Project stations onto route
  +---- Calculate distance along route
  |
  v
Station Prices
  +---- Attach latest fuel prices
  |
  v
Fuel Optimizer
  +---- Determine fuel stops
  +---- Calculate gallons purchased
  +---- Calculate fuel cost
  |
  v
Route Cache
  |
  v
API Response
```

---

## Project Structure

```text
fuel_route_optimizer/
│
├── routing/
│   ├── services/
│   │   ├── route_service.py
│   │   ├── station_matcher.py
│   │   ├── station_prices.py
│   │   ├── fuel_optimizer.py
│   │   └── ...
│   │
│   ├── models.py
│   ├── views.py
│   ├── serializers.py
│   ├── constants.py
│   └── tests/
│
├── fuel_station_data/
│   └── fuel_station.csv
│
├── docker-compose.yml
└── manage.py
```

The core optimization logic lives in `routing/services/fuel_optimizer.py`.

---

## Vehicle Assumptions

| Parameter          | Value      |
| ------------------ | ---------: |
| Fuel economy        | 10 MPG     |
| Tank capacity        | 50 gallons |
| Starting fuel        | 50 gallons |
| Starting condition | Full tank  |

The vehicle starts the journey with a full 50-gallon tank, giving it a maximum range of:

```text
Maximum range = Tank capacity × MPG = 50 × 10 = 500 miles
```

These values are centralized in `routing/constants.py` so they can be changed without touching the optimization algorithm.

---

## Fuel Optimization Strategy

The optimizer's objective is to **minimize the total cost of fuel required to complete the entire route**. It does not simply select the cheapest fuel station: a station selling fuel at $2.50/gallon is useless if the vehicle can't reach it, and buying expensive fuel at an earlier station may be necessary if the next affordable one is out of range.

**Refueling decision.** To keep the algorithm simple, predictable, and testable, the final implementation does not model arbitrary partial refueling. At every station there are exactly two options:

```text
1. Do not refuel.
2. Fill the tank to capacity.
```

Restricting the decision to a binary choice keeps the state space small enough to search exhaustively while still finding the lowest-cost plan. The optimizer models the problem as a dynamic-programming / shortest-path-style search over vehicle states.

---

## How the Algorithm Works

### Step 1 — Calculate the route

The API geocodes the start and finish locations, e.g.:

```text
Chicago, IL  -> 41.8755616, -87.6244212
Dallas, TX   -> 32.7762719, -96.7968559
```

The routing service then returns the route distance and geometry (a list of longitude/latitude points describing the road path).

### Step 2 — Find stations near the route

The fuel-station dataset contains highway/intersection-based locations, e.g. `I-57, EXIT 283 & US-24`. The station matcher determines which stations fall within a configurable search radius of the route; anything outside that radius is ignored.

### Step 3 — Project stations onto the route

Being geographically close to the route isn't enough — the optimizer also needs to know *where* along the route each station sits. Each station is annotated with:

```json
{
    "station": "FUEL MART #787",
    "distance_from_route_miles": 1.63,
    "distance_along_route_miles": 208.51
}
```

`distance_along_route_miles` lets the optimizer process stations in the order the vehicle actually encounters them.

### Step 4 — Sort stations

Stations are sorted by `distance_along_route_miles`, so a station behind the vehicle is never considered as a future fueling option.

### Step 5 — Attach fuel prices

Each station candidate is enriched with its latest available price, combined with its distance along and from the route.

### Step 6 — Track vehicle state

The optimizer tracks a state consisting of: position, fuel remaining, total cost, previous state, station, and gallons purchased. For example:

```text
Position: 208 miles
Fuel: 40 gallons
Cost: $0
```

If the next station is 300 miles away, the fuel required is `300 / 10 = 30 gallons`. Since 40 gallons remain, the station is reachable without purchasing fuel.

### Step 7 — Two decisions at every station

At each reachable station, the optimizer branches into two possible states:

**Option 1 — Skip the station**

```text
Arrive with: 40 gallons  ->  Buy: 0  ->  Leave with: 40 gallons (cost unchanged)
```

**Option 2 — Fill the tank**

```text
Arrive with: 40 gallons
Tank capacity: 50 gallons
Buy: 50 - 40 = 10 gallons

At $3.00/gallon: cost = 10 × $3.00 = $30
Resulting state: 50 gallons, cost + $30
```

### Step 8 — Prune dominated states

Different decision paths can produce multiple states at the same station. A state is **dominated** — and safely discarded — if another state has both lower cost and equal or greater fuel remaining. For example:

```text
State A: cost $50, fuel 30 gal
State B: cost $60, fuel 25 gal
```

State A dominates State B on both dimensions, so B can be removed. This keeps the search space manageable without sacrificing optimality.

### Step 9 — Check the destination

After processing all stations, the optimizer checks each remaining state: if `(route_distance - current_position) / MPG <= fuel_remaining`, the destination is reachable from that state. Among all reachable states, it picks the one with the lowest total cost.

**Important rule — don't refuel if you can already finish.** If the fuel currently in the tank is enough to reach the destination, the vehicle does not stop, even if a station is available. This avoids unnecessary purchases.

---

## Worked Example

```text
Starting fuel = 50 gallons
MPG = 10
Tank capacity = 50 gallons
Maximum range = 500 miles

Station A = mile 100
Station B = mile 550
Destination = mile 700
```

At Station A: `fuel remaining = 50 - (100 / 10) = 40 gallons`.

Distance from A to B is 450 miles, requiring 45 gallons — more than the 40 gallons on hand, so Station B is unreachable without refueling. Since partial refueling is disabled, the vehicle fills up completely at Station A:

```text
Fuel purchased at A = 50 - 40 = 10 gallons
```

The vehicle reaches Station B with `50 - 45 = 5 gallons`. The remaining distance (B to destination) is 150 miles, requiring 15 gallons — so Station B must also be used to complete the trip.

---

## Why Not Simply "Choose the Cheapest Station"?

```text
Station A = $3.00
Station B = $2.00 (600 miles away, but max range is only 500 miles)
```

Station B is cheaper but unreachable, so it can't be chosen. The optimizer must jointly consider **price** and **reachability**, which is why it evaluates the route sequentially while maintaining fuel and cost state, rather than sorting stations by price alone.

---

## Station Dataset

The supplied CSV contains highway/intersection-based fuel station records, e.g.:

```text
I-44, EXIT 283 & US-69
I-94, EXIT 143 & US-12 & SR-21
I-8, EXIT 119 & SR-85
```

The import command loads station and price data into the database. Coordinates are obtained during import when required — the application does not parse the highway-intersection text during route optimization itself; instead, stations are represented by geographic coordinates and matched against the calculated route.

---

## Route Caching

Route calculation and fuel optimization are expensive (external APIs, geospatial processing), so results are cached. The cache stores the start/finish locations, coordinates, route distance and geometry, the fuel optimization result, stops, and cost, along with a **price fingerprint**. The fingerprint lets the application detect changes in the underlying fuel-price data and avoid returning a stale optimization result.

---

## API

**Endpoint**

```text
POST /api/routes/optimize/
```

**Example request**

```bash
curl -X POST \
    http://localhost:8000/api/routes/optimize/ \
    -H "Content-Type: application/json" \
    -d '{
        "start_location": "Chicago, IL",
        "finish_location": "Dallas, TX"
    }'
```

**Example response**

```json
{
    "start": {
        "location": "Chicago, IL",
        "latitude": 41.8755616,
        "longitude": -87.6244212
    },
    "finish": {
        "location": "Dallas, TX",
        "latitude": 32.7762719,
        "longitude": -96.7968559
    },
    "route": {
        "distance_miles": 966.9231,
        "geometry": []
    },
    "fuel": {
        "total_purchased_gallons": 53.9406,
        "total_consumed_gallons": 96.6923,
        "remaining_gallons": 7.2483,
        "total_cost": "167.5873"
    },
    "stops": [
        {
            "station": "K AND H TRUCK PLAZA",
            "address": "I-57, EXIT 283 & US-24",
            "city": "Gilman",
            "state": "IL",
            "price": "3.0990",
            "distance_along_route_miles": 114.1050,
            "distance_from_route_miles": 1.0226,
            "gallons": 11.4105,
            "cost": "35.3611"
        },
        {
            "station": "ONE9 #301",
            "address": "I-55, EXIT 40",
            "city": "Marston",
            "state": "MO",
            "price": "3.1090",
            "distance_along_route_miles": 539.4063,
            "distance_from_route_miles": 0.2430,
            "gallons": 42.5301,
            "cost": "132.2262"
        }
    ]
}
```

---

## Fuel Calculation

The response exposes four fuel metrics:

| Metric | Formula | Example |
|---|---|---|
| `total_purchased_gallons` | sum of gallons bought at all stops | 53.9406 |
| `total_consumed_gallons` | `route_distance / MPG` | 966.9231 / 10 = 96.6923 |
| `remaining_gallons` | `starting_fuel + total_purchased - total_consumed` | 50 + 53.9406 − 96.6923 = 7.2483 |
| `total_cost` | `Σ(gallons_purchased × station_price)` | 167.5873 |

---

## Edge Cases

**Route within initial range.** If the route is 500 miles or less, the optimizer purchases nothing, even if cheaper stations exist along the way:

```text
Chicago -> destination, distance = 400 miles
{ "total_purchased_gallons": 0, "total_cost": "0" }
```

**Impossible routes.** If no sequence of available stations lets the vehicle reach the destination, the optimizer raises:

```text
ValueError: Destination cannot be reached with the available fuel stations.
```

The API layer converts this into an appropriate HTTP error response.

---

## Data Validation

The optimizer validates that:

- Route distance is not negative
- MPG is greater than zero
- Tank capacity is greater than zero
- Starting fuel is not negative and does not exceed tank capacity
- Stations at or before the start are ignored
- Stations at or after the destination are ignored
- Stations with negative prices are ignored

---

## Design Rationale

**Why dynamic programming instead of a plain greedy?** A rule like "always choose the cheapest station" isn't sufficient, because reachability depends on the vehicle's current fuel state — a cheap station may be too far away, while an expensive one may be unavoidable to reach it. The optimizer keeps multiple candidate states and prunes dominated ones (Step 8), balancing correctness, cost-optimality, and computational cost.

**Why full-tank-only refueling instead of partial amounts?** The initial design allowed arbitrary partial fill amounts (e.g. "buy 3.72 gallons"). That turns the problem into a continuous optimization with a much larger state space. Restricting each stop to a binary choice — skip or fill completely — keeps the algorithm deterministic, the state space bounded, and the behavior easy to reason about and test, at the cost of occasionally buying slightly more fuel than the trip strictly requires.

---

## Key Design Decisions

1. **Route-relative station positions.** Stations are selected using `distance_along_route`, not raw lat/long distance, since the vehicle travels the route in one direction.
2. **Configurable vehicle assumptions.** Vehicle properties live in `routing/constants.py`, making it easy to support a different vehicle profile later.
3. **Geospatial processing.** Shapely, and PyProj handle geographic data and route geometry.
4. **Cached routes.** Avoids repeating expensive route calculation and optimization for identical requests.
5. **Price fingerprinting.** The cache tracks the state of station prices so a route is recalculated when relevant prices change.
6. **`Decimal` for money.** Fuel prices and costs use Python's `Decimal` rather than binary floating point, where monetary precision matters.

---

## Limitations

- Vehicle MPG is constant.
- Fuel prices are treated as fixed during optimization.
- The vehicle always starts with a full tank.
- Refueling fills the tank completely — arbitrary partial refueling is not modeled.
- The vehicle follows the calculated route exactly; detour time/distance to a station is not added to the route distance.
- Traffic and real-time fuel consumption are not modeled.
- Fuel station availability (e.g. out of stock) is not modeled.
- Optimization is based solely on the provided fuel-price dataset.

These assumptions keep the solution focused on the scope of the assessment.

---

## Future Improvements

1. Support arbitrary partial refueling via a continuous optimization approach.
2. Add station detour distance to the fuel calculation.
3. Consider station operating hours.
4. Consider real-time fuel prices.
5. Support vehicle-specific MPG.
6. Support different starting fuel levels.
7. Add traffic-aware routing and estimated driving time.
8. Add fuel station availability.
9. Add asynchronous route calculation for long-running requests.
10. Add more advanced cache invalidation.
11. Add a frontend map visualization.

---

## Testing

The project includes unit and integration tests covering route calculation, station matching and ordering, fuel purchase logic, full-tank refueling, cheaper-station look-ahead, unreachable destinations, dominated-state pruning, and fuel cost calculations, among others.

Run the full test suite:

```bash
cd fuel_route_optimizer
python manage.py test routing
```

```text
Found 31 test(s).
................................
----------------------------------------------------------------------
Ran 31 tests

OK
```

---

## Running With Docker

**Prerequisites:** Docker Desktop, Docker Compose, and the `fuel_station.csv` dataset placed at `fuel_route_optimizer/fuel_station_data/fuel_station.csv`.

**Start the application** (from the repository root):

```bash
docker compose -f fuel_route_optimizer/docker-compose.yml up --build
```

The API is available at `http://localhost:8000/`. The Docker setup includes the Django app, PostgreSQL. The database is exposed on the host at port `5434`.

**Import fuel stations:**

```bash
docker compose -f fuel_route_optimizer/docker-compose.yml exec web \
    python manage.py import_stations \
    fuel_station_data/fuel_station.csv
```

For a faster import when coordinates are already available:

```bash
docker compose -f fuel_route_optimizer/docker-compose.yml exec web \
    python manage.py import_stations \
    fuel_station_data/fuel_station.csv \
    --skip-geocode
```

The import process reads the CSV, validates records, geocodes locations when required, stores coordinates and prices, and updates existing records instead of creating duplicates.

**Stop Docker:**

```bash
docker compose -f fuel_route_optimizer/docker-compose.yml down
```

To also delete the PostgreSQL volume (be careful — this removes persisted data):

```bash
docker compose -f fuel_route_optimizer/docker-compose.yml down -v
```

---

## Local Configuration

For non-Docker development, copy `fuel_route_optimizer/envs/sample_env` to `fuel_route_optimizer/envs/.env` and configure the database and external API credentials.

---

## Summary

The application takes a start location, finish location, and a fuel-price dataset, and produces a driving route, recommended fuel stops, and the total fuel purchased, consumed, and cost. The pipeline combines geocoding, routing, geospatial station matching, station pricing, fuel optimization, and caching behind a REST API.

The fuel optimizer does not blindly choose the cheapest station. It evaluates reachability given current fuel, tracks multiple candidate states, prunes dominated ones, and selects the lowest-cost state that can still reach the destination — producing a cost-effective and testable fuel-routing API.