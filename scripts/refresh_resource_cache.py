"""Refresh urban and rural resource snapshots for API readers."""

from tools.resource_cache import refresh_all_resource_caches


if __name__ == "__main__":
    result = refresh_all_resource_caches()
    for scope, payload in result.items():
        print(f"{scope}: {len(payload['resources'])} resources at {payload['refreshed_at']}")
