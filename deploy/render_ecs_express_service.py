"""Render the checked ECS Express template without committing Cognito IDs."""

import argparse
import json
import os
from pathlib import Path


REQUIRED_DEPLOYMENT_VALUES = ("COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_ID")


def render_config(template: dict, environment: dict[str, str]) -> dict:
    missing = [name for name in REQUIRED_DEPLOYMENT_VALUES if not environment.get(name, "").strip()]
    if missing:
        raise ValueError(f"Missing required deployment values: {', '.join(missing)}")

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            name = value[2:-1]
            replacement = environment.get(name, "").strip()
            if not replacement:
                raise ValueError(f"Missing required deployment value: {name}")
            return replacement
        return value

    return replace(template)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, default=Path("ecs-express-service.json"))
    parser.add_argument("--output", type=Path, default=Path("ecs-express-service.rendered.json"))
    args = parser.parse_args()
    rendered = render_config(json.loads(args.template.read_text()), dict(os.environ))
    args.output.write_text(json.dumps(rendered, indent=2) + "\n")
    print(f"Wrote deployment config to {args.output}")


if __name__ == "__main__":
    main()
