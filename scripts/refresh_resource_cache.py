"""Refresh prepared resource snapshots for API readers."""

import argparse

from tools.resource_cache import refresh_all_resource_caches, refresh_resource_cache


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("urban", "rural", "all"), default="all")
    args = parser.parse_args()
    result = (
        refresh_all_resource_caches()
        if args.scope == "all"
        else {args.scope: refresh_resource_cache(args.scope)}
    )
    for scope, payload in result.items():
        print(f"{scope}: {len(payload['resources'])} resources at {payload['refreshed_at']}")
