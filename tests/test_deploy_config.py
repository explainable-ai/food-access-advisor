import json
from pathlib import Path

import pytest

from deploy.render_ecs_express_service import render_config


ROOT = Path(__file__).parent.parent


def test_ecs_template_requires_and_renders_real_cognito_identifiers():
    template = json.loads((ROOT / "ecs-express-service.json").read_text())
    with pytest.raises(ValueError, match="COGNITO_USER_POOL_ID"):
        render_config(template, {})

    rendered = render_config(template, {
        "COGNITO_USER_POOL_ID": "us-east-1_realpool",
        "COGNITO_APP_CLIENT_ID": "realclient",
    })
    environment = {item["name"]: item["value"] for item in rendered["primaryContainer"]["environment"]}
    assert environment["FOOD_ACCESS_AUTH_MODE"] == "required"
    assert environment["COGNITO_USER_POOL_ID"] == "us-east-1_realpool"
    assert environment["COGNITO_APP_CLIENT_ID"] == "realclient"
    assert environment["ROUTING_PROVIDER"] == "aws_location"
