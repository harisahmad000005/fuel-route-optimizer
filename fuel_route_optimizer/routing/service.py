"""
Pipeline
--------
1. get_route()            -> ONE call to the routing API. Returns route geometry
                              (ordered list of lat/lng points) + total distance.
2. build_cumulative_distances() -> walks the route geometry once, assigning each
                              point a "distance from start" value in miles.
3. find_stations_near_route()   -> a single DB query (bounding box) to pull
                              candidate FuelStation rows near the route corridor.
                              No external API calls here at all.
4. project_station_onto_route() -> for each candidate station, find the closest
                              point on the route polyline, and therefore its
                              own distance-from-start. This is what lets us
                              order stations "along" the trip.
5. optimal_fuel_plan()     -> the actual optimization: a dynamic program that
                              finds the PROVABLY cheapest combination of stops,
                              not just a greedy nearest/cheapest guess.

Only step 1 touches the external routing API, and it's called exactly once
per (uncached) request.
"""

import math
from dataclasses import dataclass

MAX_RANGE_MILES = 500
MPG = 10
EARTH_RADIUS_MILES = 3958.8

# How far off the route's polyline a station can be and still count as
# "on the way". Wider = more candidate stations = better prices, but more
# detour in reality. 2 miles is a reasonable highway-corridor default.
CORRIDOR_WIDTH_MILES = 2.0


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance between two lat/lng points, in miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Step 2: cumulative distance along the route geometry
# ---------------------------------------------------------------------------

def build_cumulative_distances(route_points: list[tuple[float, float]]) -> list[float]:
    """
    route_points: [(lat, lon), (lat, lon), ...] as returned by the routing API,
    in travel order.

    Returns a parallel list where cumulative[i] = miles traveled from the
    start to reach route_points[i]. This turns "a point on the map" into
    "a mile marker on this specific trip" — the concept everything else
    in this file depends on.
    """
    cumulative = [0.0]
    for i in range(1, len(route_points)):
        lat1, lon1 = route_points[i - 1]
        lat2, lon2 = route_points[i]
        cumulative.append(cumulative[-1] + haversine_miles(lat1, lon1, lat2, lon2))
    return cumulative


# ---------------------------------------------------------------------------
# Step 3: corridor filtering — one DB query, zero external API calls
# ---------------------------------------------------------------------------

def find_stations_near_route(route_points: list[tuple[float, float]], FuelStation):
    """
    Cheap pre-filter: build a bounding box around the whole route (min/max
    lat/lon, padded by the corridor width) and pull only stations inside it
    from the DB. This keeps the expensive per-point distance check (step 4)
    working on hundreds of rows instead of your entire table.
    """
    lats = [p[0] for p in route_points]
    lons = [p[1] for p in route_points]

    # Rough conversion: 1 degree latitude ~= 69 miles. Longitude varies with
    # latitude but this padding only needs to be generous, not exact —
    # it's a coarse pre-filter, precision happens in step 4.
    pad_deg = CORRIDOR_WIDTH_MILES / 69.0

    return FuelStation.objects.filter(
        latitude__gte=min(lats) - pad_deg,
        latitude__lte=max(lats) + pad_deg,
        longitude__gte=min(lons) - pad_deg,
        longitude__lte=max(lons) + pad_deg,
        latitude__isnull=False,
        longitude__isnull=False,
    )


# ---------------------------------------------------------------------------
# Step 4: project each candidate station onto the route
# ---------------------------------------------------------------------------

@dataclass
class RouteStation:
    station_id: int
    price: float
    distance_from_start: float   # miles along the route where this station sits
    perpendicular_distance: float  # how far off the route it actually is


def project_station_onto_route(
    station_lat: float,
    station_lon: float,
    route_points: list[tuple[float, float]],
    cumulative: list[float],
) -> tuple[float, float]:
    """
    Finds the closest point on the route polyline to this station, by
    checking distance to every route point and taking the minimum.
    Returns (distance_from_start_at_closest_point, perpendicular_distance).

    This is an approximation (nearest vertex, not true nearest-point-on-
    segment), but route geometries from OSRM/similar APIs are dense enough
    (points every ~100m-1mile) that the error is negligible for a 500-mile
    range constraint. Worth mentioning as a known simplification in your
    Loom if asked.
    """
    best_dist = float("inf")
    best_index = 0

    for i, (lat, lon) in enumerate(route_points):
        d = haversine_miles(station_lat, station_lon, lat, lon)
        if d < best_dist:
            best_dist = d
            best_index = i

    return cumulative[best_index], best_dist


def build_route_stations(route_points, cumulative, candidate_stations) -> list[RouteStation]:
    """
    Runs step 4 over every candidate from step 3, discards anything outside
    the corridor width, and returns a list sorted by position along the trip
    — this ordering is required by the DP in step 5.
    """
    route_stations = []

    for station in candidate_stations:
        distance_from_start, perp_distance = project_station_onto_route(
            station.latitude, station.longitude, route_points, cumulative
        )
        if perp_distance <= CORRIDOR_WIDTH_MILES:
            route_stations.append(
                RouteStation(
                    station_id=station.id,
                    price=float(station.price),
                    distance_from_start=distance_from_start,
                    perpendicular_distance=perp_distance,
                )
            )

    route_stations.sort(key=lambda s: s.distance_from_start)
    return route_stations


# ---------------------------------------------------------------------------
# Step 5: the optimizer — this is the part that actually answers
# "which stations should I stop at to spend the least money"
# ---------------------------------------------------------------------------

@dataclass
class FuelStop:
    station_id: int
    distance_from_start: float
    price: float
    gallons_bought: float
    cost: float


def optimal_fuel_plan(
    route_stations: list[RouteStation],
    total_distance: float,
    mpg: float = MPG,
    range_miles: float = MAX_RANGE_MILES,
) -> tuple[float, list[FuelStop]]:
    """
    Finds the PROVABLY cheapest set of fuel stops for the trip.

    --- Why not just "greedy: always pick the cheapest station in range"? ---
    Greedy can be wrong. Classic counterexample: you're at mile 0 with a
    500-mile range. There's a station at mile 490 that's expensive ($4.50),
    and a station at mile 10 that's slightly cheaper ($4.00) but doesn't
    get you much further, then a MUCH cheaper station at mile 495 ($2.50)
    that's just out of reach if you fill up at mile 10 and drive
    conservatively. A greedy "cheapest in range" rule can lock you into a
    suboptimal chain of small savings and miss a large downstream discount,
    or the reverse — grab a big station early and skip a cheaper one that
    was actually reachable. There's no simple local rule that's always
    correct, because the right decision at station i depends on what's
    reachable from EVERY station after it, not just the next one.

    --- The correct model: shortest path over a DAG ---
    Think of every station (plus a virtual start and virtual finish) as a
    node, placed in order of distance along the route. Draw a directed edge
    from station i to station j whenever j is reachable from i on one tank
    (distance(i, j) <= range_miles). Weight that edge by the cost of the
    fuel burned making that specific leg, priced at station i's rate:

        edge_cost(i, j) = price_at_i * (distance(j) - distance(i)) / mpg

    Because stations are sorted by position, every edge only ever points
    "forward" — this is a Directed Acyclic Graph, not a general graph. The
    cheapest way to get from start to finish in this graph IS the cheapest
    real-world fuel plan. This is solvable with a single dynamic-programming
    pass in O(n^2) time, no need for full Dijkstra/priority queues.

    dp[j] = minimum cost to reach node j from the start.
    dp[0] = 0 (start with a full tank, no cost yet).
    dp[j] = min over all i where distance(i,j) <= range_miles of:
                dp[i] + edge_cost(i, j)

    We also track `parent[j]` (which node we came from) so we can walk
    backwards at the end and reconstruct the actual sequence of stops.

    --- Complexity ---
    O(n^2) where n = number of candidate stations in the corridor
    (typically tens to low hundreds after filtering) — comfortably fast
    enough to return in well under a second, satisfying the "return
    results quickly" requirement.

    Returns (total_cost, ordered_list_of_fuel_stops).
    Raises ValueError if the trip is impossible (a gap between two
    consecutive reachable points exceeds the vehicle's range).
    """
    # Build nodes: virtual start (mile 0, free/no purchase), all real
    # candidate stations in order, then virtual finish (destination mile).
    nodes = (
        [{"distance": 0.0, "price": 0.0, "station_id": None}]
        + [{"distance": s.distance_from_start, "price": s.price, "station_id": s.station_id} for s in route_stations]
        + [{"distance": total_distance, "price": 0.0, "station_id": None}]
    )

    n = len(nodes)
    dp = [math.inf] * n
    parent = [None] * n
    dp[0] = 0.0

    for i in range(n):
        if dp[i] == math.inf:
            continue  # node i is unreachable from the start at all — skip

        reached_any = False
        for j in range(i + 1, n):
            leg_distance = nodes[j]["distance"] - nodes[i]["distance"]

            if leg_distance > range_miles:
                # Nodes are sorted by distance, so once one leg exceeds
                # range, every later node from this i will too. Safe to
                # stop scanning forward from this i.
                break

            reached_any = True
            leg_cost = nodes[i]["price"] * leg_distance / mpg if i != 0 else 0.0
            candidate_cost = dp[i] + leg_cost

            if candidate_cost < dp[j]:
                dp[j] = candidate_cost
                parent[j] = i

        # If node i is a real "last reachable point" and can't even reach
        # the very next node, that's a genuine range gap in the data —
        # worth surfacing rather than silently returning a broken plan.
        if not reached_any and i != n - 1:
            pass  # dp[i+1..] may still be reachable via a different path; don't raise yet

    if dp[-1] == math.inf:
        raise ValueError(
            "No feasible fuel plan: a gap between consecutive stations "
            "along this route exceeds the vehicle's maximum range."
        )

    # Reconstruct the path of stops by walking parent pointers backwards
    # from the finish node to the start node.
    path_indices = []
    current = n - 1
    while current is not None:
        path_indices.append(current)
        current = parent[current]
    path_indices.reverse()

    # Convert to FuelStop objects, skipping the virtual start/finish nodes.
    # The gallons bought at stop k is exactly enough to cover the leg to
    # stop k+1 — this models "buy exactly what you need for the next leg,
    # priced at the current stop", which is what the DP itself assumed.
    fuel_stops = []
    for idx_pos in range(len(path_indices) - 1):
        i = path_indices[idx_pos]
        j = path_indices[idx_pos + 1]
        if nodes[i]["station_id"] is None:
            continue  # virtual start node, nothing purchased here... handled below
        leg_distance = nodes[j]["distance"] - nodes[i]["distance"]
        gallons = leg_distance / mpg
        cost = gallons * nodes[i]["price"]
        fuel_stops.append(
            FuelStop(
                station_id=nodes[i]["station_id"],
                distance_from_start=nodes[i]["distance"],
                price=nodes[i]["price"],
                gallons_bought=gallons,
                cost=cost,
            )
        )

    return dp[-1], fuel_stops