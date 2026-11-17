import hmac
import os
from functools import lru_cache

from fastapi import Header, HTTPException


@lru_cache(maxsize=1)
def entra_keys(tenant):
    import jwt

    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys", timeout=5)


def user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in required")
    token = authorization[7:]
    if os.getenv("APP_ENV") == "production":
        import jwt

        tenant = os.environ["ENTRA_TENANT"]
        try:
            signing_key = entra_keys(tenant).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=os.environ["ENTRA_AUDIENCE"],
                issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
                options={"require": ["exp", "iss", "aud", "oid", "tid"]},
            )
            if (
                claims["tid"] == tenant
                and claims["oid"] == os.environ["ENTRA_OWNER_OID"]
                and "access_as_user" in claims.get("scp", "").split()
            ):
                return claims["oid"]
        except jwt.PyJWTError:
            pass
    else:
        expected = os.environ.get("LOCAL_ACCESS_TOKEN", "")
        if len(expected) >= 24 and hmac.compare_digest(token.encode(), expected.encode()):
            return "local-owner"
    raise HTTPException(401, "Invalid credentials")
