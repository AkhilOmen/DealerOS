import hashlib
import secrets


def generate_client_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_client_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_client_secret(secret: str, secret_hash: str) -> bool:
    return secrets.compare_digest(hash_client_secret(secret), secret_hash)
