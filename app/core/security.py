from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create JWT access token with explicit HS256 algorithm.

    SECURITY: Always uses HS256 algorithm explicitly.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    # Explicitly use HS256 - do not allow algorithm to be configurable
    # This ensures consistency between token creation and verification
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt

def verify_token(token: str) -> Optional[dict]:
    """
    Verify JWT token with explicit algorithm restriction.

    SECURITY: Only HS256 algorithm is accepted to prevent algorithm confusion attacks.
    This prevents attackers from switching to 'none' algorithm or using RS256/ES256
    with a public key when the server expects HS256.
    """
    try:
        # Explicitly restrict to HS256 - do not accept other algorithms
        # This prevents algorithm confusion attacks where an attacker could
        # switch the algorithm to 'none' or use RS256 with a public key
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"],  # STRICT: Only accept HS256
            options={
                "require": ["exp", "sub"],
                "verify_exp": True,
            }
        )
        return payload
    except JWTError as e:
        # Log failed verification attempts for security monitoring
        import logging
        logging.getLogger(__name__).warning(f"JWT verification failed: {e}")
        return None

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
