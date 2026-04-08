"""Password hashing using bcrypt directly (avoiding passlib compatibility issues).

bcrypt has a 72-byte password limit. We truncate longer inputs to fit.
Cost 12 is a reasonable balance of security and speed for login throughput.
"""
import bcrypt

BCRYPT_COST = 12
MAX_PASSWORD_BYTES = 72  # bcrypt hard limit


def _prepare(password: str) -> bytes:
    """Encode and truncate password to bcrypt's 72-byte limit."""
    if not isinstance(password, bytes):
        password = password.encode("utf-8")
    return password[:MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt cost 12."""
    hashed = bcrypt.hashpw(_prepare(password), bcrypt.gensalt(rounds=BCRYPT_COST))
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("utf-8"))
    except Exception:
        return False
