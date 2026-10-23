"""Entra v2 delegated-token validation. App-only tokens (no scp) are rejected on purpose."""

import jwt


class AuthError(Exception):
    pass


def validate_token(token, *, tenant_id, client_id, jwks_client=None):
    jwks_client = jwks_client or jwt.PyJWKClient(
        "https://login.microsoftonline.com/%s/discovery/v2.0/keys" % tenant_id
    )
    try:
        key = jwks_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            issuer="https://login.microsoftonline.com/%s/v2.0" % tenant_id,
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError("expired") from e
    except jwt.InvalidAudienceError as e:
        raise AuthError("audience") from e
    except jwt.InvalidIssuerError as e:
        raise AuthError("issuer") from e
    except jwt.PyJWTError as e:
        raise AuthError("invalid: " + type(e).__name__) from e
    if "access" not in str(claims.get("scp", "")).split():
        raise AuthError("scope: delegated 'access' scope required")
    if not claims.get("oid"):
        raise AuthError("oid missing")
    return {"oid": claims["oid"], "upn": claims.get("preferred_username"), "name": claims.get("name")}
