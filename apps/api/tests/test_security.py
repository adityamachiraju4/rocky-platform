"""Unit tests for app.core.security.

These tests are framework-independent and set the required environment
variables via a fixture so the module's lazy configuration resolves.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from jose import jwt

from app.core import security
from app.core.security import (
    ExpiredTokenError,
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

SECRET = "test-secret-key-do-not-use-in-prod"
ALGORITHM = "HS256"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", ALGORITHM)
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")


# --------------------------------------------------------------------------- #
# Password hashing / verification
# --------------------------------------------------------------------------- #
def test_hash_password_uses_argon2id() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    # Hashing is salted: same input -> different hashes.
    assert hashed != hash_password("correct horse battery staple")


def test_verify_password_success() -> None:
    hashed = hash_password("s3cr3t-pass")
    assert verify_password("s3cr3t-pass", hashed) is True


def test_verify_password_failure() -> None:
    hashed = hash_password("s3cr3t-pass")
    assert verify_password("wrong-pass", hashed) is False


def test_verify_password_invalid_hash_returns_false() -> None:
    assert verify_password("anything", "not-a-real-hash") is False


# --------------------------------------------------------------------------- #
# Access token creation
# --------------------------------------------------------------------------- #
def test_create_access_token_claims() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    claims = decode_token(token)

    assert claims["sub"] == str(user_id)
    assert claims["type"] == "access"
    assert "iat" in claims
    assert "exp" in claims
    assert claims["exp"] > claims["iat"]


def test_decode_access_token_with_expected_type() -> None:
    token = create_access_token(uuid.uuid4())
    claims = decode_token(token, expected_type="access")
    assert claims["type"] == "access"


# --------------------------------------------------------------------------- #
# Refresh token creation
# --------------------------------------------------------------------------- #
def test_create_refresh_token_claims() -> None:
    user_id = uuid.uuid4()
    token = create_refresh_token(user_id)
    claims = decode_token(token)

    assert claims["sub"] == str(user_id)
    assert claims["type"] == "refresh"
    assert claims["exp"] > claims["iat"]


def test_refresh_token_rejected_when_access_expected() -> None:
    token = create_refresh_token(uuid.uuid4())
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


def test_access_token_rejected_when_refresh_expected() -> None:
    token = create_access_token(uuid.uuid4())
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="refresh")


# --------------------------------------------------------------------------- #
# Expired token rejection
# --------------------------------------------------------------------------- #
def test_expired_token_rejected() -> None:
    token = create_access_token(
        uuid.uuid4(), expires_delta=timedelta(seconds=-1)
    )
    with pytest.raises(ExpiredTokenError):
        decode_token(token)


def test_expired_refresh_token_rejected() -> None:
    token = create_refresh_token(
        uuid.uuid4(), expires_delta=timedelta(days=-1)
    )
    with pytest.raises(ExpiredTokenError):
        decode_token(token)


# --------------------------------------------------------------------------- #
# Invalid token rejection
# --------------------------------------------------------------------------- #
def test_malformed_token_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token("this.is.not.a.jwt")


def test_wrong_signature_rejected() -> None:
    # Token signed with a different key must fail signature verification.
    forged = jwt.encode(
        {"sub": "x", "type": "access"},
        "a-different-secret",
        algorithm=ALGORITHM,
    )
    with pytest.raises(InvalidTokenError):
        decode_token(forged)


def test_missing_secret_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(security.SecurityError):
        create_access_token(uuid.uuid4())
