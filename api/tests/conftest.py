import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
ISSUER = "https://login.microsoftonline.com/%s/v2.0" % TENANT


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwks_client(rsa_key):
    key = SimpleNamespace(key=rsa_key.public_key())
    return SimpleNamespace(get_signing_key_from_jwt=lambda token: key)


@pytest.fixture
def make_token(rsa_key):
    def _make(**overrides):
        claims = {
            "aud": CLIENT,
            "iss": ISSUER,
            "exp": int(time.time()) + 600,
            "scp": "access",
            "oid": "33333333-3333-3333-3333-333333333333",
            "preferred_username": "someone@example.com",
            "name": "Some One",
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": "k1"})

    return _make
