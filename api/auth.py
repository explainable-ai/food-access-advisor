"""Cognito access-token verification for staff-only API actions."""

import os
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient


bearer = HTTPBearer(auto_error=False)


def _setting(name: str) -> str:
    return os.getenv(name, "").strip()


def _auth_mode() -> str:
    mode = _setting("FOOD_ACCESS_AUTH_MODE").lower() or "required"
    if mode not in {"required", "disabled"}:
        raise HTTPException(status_code=503, detail="FOOD_ACCESS_AUTH_MODE must be required or disabled")
    return mode


def _configuration() -> tuple[str, str, str, str]:
    region = _setting("COGNITO_REGION") or _setting("AWS_REGION") or "us-east-1"
    pool_id = _setting("COGNITO_USER_POOL_ID")
    client_id = _setting("COGNITO_APP_CLIENT_ID")
    required_group = _setting("COGNITO_STAFF_GROUP") or "staff"
    if not pool_id or not client_id:
        raise HTTPException(
            status_code=503,
            detail="Staff authentication is enabled but Cognito is not configured",
        )
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    return issuer, client_id, required_group, f"{issuer}/.well-known/jwks.json"


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_access_token(token: str) -> dict[str, Any]:
    issuer, client_id, _required_group, jwks_url = _configuration()
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False, "require": ["exp", "iss", "sub", "token_use"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized("The access token is invalid or expired") from exc
    if claims.get("token_use") != "access":
        raise _unauthorized("An Amazon Cognito access token is required")
    if claims.get("client_id") != client_id:
        raise _unauthorized("The access token was not issued for this application")
    return claims


def require_staff_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
) -> dict[str, Any]:
    """Return verified Cognito claims or reject a staff-only request.

    ``disabled`` exists only for local tests and development. The deployed
    default is fail-closed: missing Cognito configuration returns 503 and a
    missing/invalid bearer token returns 401.
    """
    if _auth_mode() == "disabled":
        return {"sub": "local-development", "username": "local-development", "cognito:groups": ["staff"]}
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Sign in with a staff account to perform this action")
    claims = _decode_access_token(credentials.credentials)
    required_group = _configuration()[2]
    groups = claims.get("cognito:groups", [])
    if required_group not in groups:
        raise HTTPException(status_code=403, detail=f"Membership in the {required_group!r} group is required")
    return claims
