import secrets
from hashlib import sha512


def encrypt_with_salt(
    plaintext: str,
    hashfunc: callable = sha512,
    salt: bytes | None = None,
    salt_size: int = 8,
) -> str:
    if salt is None:
        salt = secrets.token_hex(salt_size).encode("utf-8")
    return salt.hex() + hashfunc(salt + plaintext.encode("utf-8")).hexdigest()


def verify_with_salt(
    salted_ciphertext: str | bytes,
    plaintext: str,
    hashfunc: callable = sha512,
    salt_size: int = 8,
) -> bool:
    if isinstance(salted_ciphertext, str):
        salted_ciphertext = bytes.fromhex(salted_ciphertext)
    salt = salted_ciphertext[: salt_size * 2]
    ciphertext = salted_ciphertext[salt_size * 2 :]

    return hashfunc(salt + plaintext.encode("utf-8")).digest() == ciphertext
