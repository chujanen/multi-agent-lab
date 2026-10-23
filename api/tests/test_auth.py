import pytest

import auth
from conftest import CLIENT, TENANT


def check(token, jwks_client):
    return auth.validate_token(token, tenant_id=TENANT, client_id=CLIENT, jwks_client=jwks_client)


def test_valid_token_returns_user(make_token, jwks_client):
    user = check(make_token(), jwks_client)
    assert user == {"oid": "33333333-3333-3333-3333-333333333333", "upn": "someone@example.com", "name": "Some One"}


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"aud": "other"}, "audience"),
        ({"iss": "https://sts.windows.net/x/"}, "issuer"),
        ({"exp": 1}, "expired"),
        ({"scp": None}, "scope"),
        ({"scp": "read"}, "scope"),
        ({"oid": None}, "oid"),
    ],
)
def test_rejects(make_token, jwks_client, overrides, reason):
    with pytest.raises(auth.AuthError) as info:
        check(make_token(**overrides), jwks_client)
    assert reason in str(info.value)


def test_rejects_bad_signature(make_token, jwks_client):
    token = make_token()
    head, body, sig = token.split(".")
    with pytest.raises(auth.AuthError):
        check(".".join([head, body, sig[:-4] + "AAAA"]), jwks_client)
