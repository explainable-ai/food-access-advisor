"""Explainable, deterministic geospatial prioritization.

The model is a weighted decision model, not predictive ML. Every score is
reproducible from the returned component values and weights.
"""

from dataclasses import dataclass, asdict
from typing import Mapping

from strands import tool

from tools.geo import haversine_miles


@dataclass(frozen=True)
class PriorityWeights:
    food_access_gap: float = 0.25
    poverty: float = 0.20
    no_vehicle: float = 0.15
    population_served: float = 0.20
    transit_burden: float = 0.10
    existing_coverage: float = 0.10

    def normalized(self) -> dict[str, float]:
        values = asdict(self)
        if any(value < 0 for value in values.values()):
            raise ValueError("priority weights cannot be negative")
        total = sum(values.values())
        if total <= 0:
            raise ValueError("at least one priority weight must be positive")
        return {name: value / total for name, value in values.items()}


DEFAULT_WEIGHTS = PriorityWeights()


def _severity_component(tract):
    if tract.get("low_access_half_mile"):
        return 1.0
    if tract.get("low_access_one_mile"):
        return 0.6
    return 0.2


def _nearest_resource(tract, resources):
    if not resources or tract.get("centroid_lat") is None or tract.get("centroid_lon") is None:
        return None
    best, best_distance = None, float("inf")
    for resource in resources:
        if resource.get("lat") is None or resource.get("lon") is None:
            continue
        distance = haversine_miles(tract["centroid_lat"], tract["centroid_lon"], resource["lat"], resource["lon"])
        if distance < best_distance:
            best_distance = distance
            best = {**resource, "distance_miles": round(distance, 2)}
    return best


def _rate(tract, direct_name, numerator_name, denominator_name):
    direct = tract.get(direct_name)
    if direct is not None:
        value = float(direct)
        return max(0.0, min(value / 100 if value > 1 else value, 1.0))
    numerator, denominator = tract.get(numerator_name), tract.get(denominator_name)
    if numerator is None or denominator in (None, 0):
        return None
    return max(0.0, min(float(numerator) / float(denominator), 1.0))


def _min_max(values):
    present = [float(value) for value in values if value is not None]
    if not present:
        return [None for _ in values]
    low, high = min(present), max(present)
    if low == high:
        return [0.5 if value is not None else None for value in values]
    return [(float(value) - low) / (high - low) if value is not None else None for value in values]


def _transit_burden(nearest):
    if nearest is None or nearest.get("transit_minutes") is None:
        return None
    return min(max(float(nearest["transit_minutes"]) / 45, 0), 1.0)


def _coverage(nearest):
    """Existing coverage (higher is better); it is subtracted in scoring."""
    if nearest is None:
        return 0.0
    minutes = nearest.get("transit_minutes")
    burden = min(float(minutes) / 45, 1.0) if minutes is not None else min(nearest["distance_miles"] / 3, 1.0)
    quality = 0.45 if nearest.get("kind") == "convenience" else 1.0
    return max(0.0, (1.0 - burden) * quality)


def _coerce_weights(weights):
    if weights is None:
        return DEFAULT_WEIGHTS
    if isinstance(weights, PriorityWeights):
        return weights
    allowed = set(asdict(DEFAULT_WEIGHTS))
    unknown = set(weights) - allowed
    if unknown:
        raise ValueError(f"unknown priority weights: {sorted(unknown)}")
    return PriorityWeights(**{**asdict(DEFAULT_WEIGHTS), **weights})


def _weighted_score(components, normalized_weights):
    available = {name: value for name, value in components.items() if value is not None}
    denominator = sum(normalized_weights[name] for name in available)
    if not denominator:
        return 0.0, {}
    contributions = {}
    score = 0.0
    for name, value in available.items():
        effective_weight = normalized_weights[name] / denominator
        signed_value = -value if name == "existing_coverage" else value
        contribution = effective_weight * signed_value
        contributions[name] = round(contribution * 100, 2)
        score += contribution
    return round(max(0.0, min(score * 100, 100.0)), 1), contributions


def _explanation(contributions, missing):
    labels = {"food_access_gap": "food-access gap", "poverty": "poverty",
              "no_vehicle": "households without a vehicle", "population_served": "population served",
              "transit_burden": "transit burden", "existing_coverage": "existing coverage"}
    positive = [(name, points) for name, points in contributions.items()
                if name != "existing_coverage" and points > 0]
    strongest = sorted(positive, key=lambda item: item[1], reverse=True)[:2]
    text = "Highest weighted contributions: " + (
        ", ".join(f"{labels[name]} (+{points:.1f} points)" for name, points in strongest)
        if strongest else "none"
    ) + "."
    coverage = contributions.get("existing_coverage")
    if coverage is not None and coverage < 0:
        text += f" Existing coverage reduced the score by {abs(coverage):.1f} points."
    if missing:
        text += " Not scored due to missing evidence: " + ", ".join(labels[name] for name in missing) + "."
    return text


def _score_all(tracts, resources, weights):
    nearest = [_nearest_resource(tract, resources) for tract in tracts]
    poverty = [_rate(t, "poverty_rate", "population_below_poverty", "poverty_universe") for t in tracts]
    no_vehicle = [_rate(t, "no_vehicle_rate", "households_no_vehicle", "households_total") for t in tracts]
    population = _min_max([t.get("population") for t in tracts])
    normalized_weights = weights.normalized()
    scored = []
    for index, tract in enumerate(tracts):
        components = {"food_access_gap": _severity_component(tract), "poverty": poverty[index],
                      "no_vehicle": no_vehicle[index], "population_served": population[index],
                      "transit_burden": _transit_burden(nearest[index]),
                      "existing_coverage": _coverage(nearest[index])}
        score, contributions = _weighted_score(components, normalized_weights)
        missing = [name for name, value in components.items() if value is None]
        entry = {**tract, "need_score": score, "score_components": {name: round(value * 100, 1) if value is not None else None for name, value in components.items()},
                 "score_contributions": contributions, "weights_used": {name: round(value, 4) for name, value in normalized_weights.items()},
                 "missing_components": missing, "score_explanation": _explanation(contributions, missing),
                 "nearest_resource_kind": nearest[index].get("kind") if nearest[index] else None,
                 "nearest_resource_miles": nearest[index].get("distance_miles") if nearest[index] else None,
                 "nearest_resource_minutes": nearest[index].get("transit_minutes") if nearest[index] else None}
        scored.append(entry)
    scored.sort(key=lambda item: (-item["need_score"], item["tract_fips"]))
    for rank, item in enumerate(scored, 1):
        item["rank"] = rank
    return scored


def score_all_gaps(tracts: list, resources: list,
                   weights: Mapping[str, float] | None = None) -> list:
    """Score and rank every tract without the expensive sensitivity sweep.

    This is the prepared-data read model used by the county heatmap. It keeps
    the exact same components, missing-evidence rules, tie-break, and score
    explanation as score_gaps; only the top-N truncation and sensitivity
    scenarios are omitted.
    """
    return _score_all(tracts, resources, _coerce_weights(weights))


@tool
def score_gaps(tracts: list, resources: list, top_n: int = 3, weights: Mapping[str, float] | None = None,
               sensitivity_percent: float = 0.20) -> list:
    """Rank tracts with adjustable weights and one-at-a-time sensitivity analysis."""
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    if not 0 <= sensitivity_percent <= 1:
        raise ValueError("sensitivity_percent must be between 0 and 1")
    selected_weights = _coerce_weights(weights)
    baseline = score_all_gaps(tracts, resources, selected_weights)
    ranges = {item["tract_fips"]: {"scores": [item["need_score"]], "ranks": [item["rank"]]} for item in baseline}
    base_values = asdict(selected_weights)
    for name, value in base_values.items():
        for multiplier in (1 - sensitivity_percent, 1 + sensitivity_percent):
            scenario = PriorityWeights(**{**base_values, name: value * multiplier})
            for item in _score_all(tracts, resources, scenario):
                ranges[item["tract_fips"]]["scores"].append(item["need_score"])
                ranges[item["tract_fips"]]["ranks"].append(item["rank"])
    for item in baseline:
        scores = ranges[item["tract_fips"]]["scores"]
        ranks = ranges[item["tract_fips"]]["ranks"]
        item["sensitivity"] = {"percent": sensitivity_percent,
                               "score_min": min(scores), "score_max": max(scores),
                               "rank_best": min(ranks), "rank_worst": max(ranks),
                               "rank_stable": min(ranks) == max(ranks)}
    return baseline[:top_n]
