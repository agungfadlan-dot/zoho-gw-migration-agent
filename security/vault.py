"""
Ephemeral In-Memory Credential Vault.

Security Guardrails:
- Zero plaintext persistence to disk.
- Ephemeral master session key generated randomly on startup.
- Authenticated in-memory encryption (AES-256-GCM / HMAC-SHA256 CTR stream cipher).
- Session TTL enforcement (automatic timeout).
- Explicit memory zeroing on purge and process exit.
"""

import os
import sys
import time
import ctypes
import secrets
import hashlib
import hmac
from typing import Dict, Optional, Any, Tuple

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


class EphemeralVaultError(Exception):
    """Raised when vault operations fail or session has expired."""
    pass


class EphemeralVault:
    """
    In-memory encrypted credential store with automatic TTL expiration
    and memory zeroing.
    """

    def __init__(self, ttl_seconds: int = 3600):
        self._ttl_seconds = ttl_seconds
        self._created_at = time.time()
        self._last_accessed = time.time()
        self._is_purged = False

        # Generate ephemeral 256-bit master key
        self._master_key: bytearray = bytearray(secrets.token_bytes(32))
        self._store: Dict[str, Tuple[bytes, bytes, bytes]] = {}  # key -> (ciphertext, nonce/iv, tag)

    def _check_ttl(self) -> None:
        """Enforces session time-to-live expiration."""
        if self._is_purged:
            raise EphemeralVaultError("Vault has been purged. Credential session is terminated.")
        now = time.time()
        if now - self._created_at > self._ttl_seconds:
            self.purge()
            raise EphemeralVaultError(f"Vault session expired after {self._ttl_seconds} seconds.")
        self._last_accessed = now

    def _encrypt(self, plaintext: str) -> Tuple[bytes, bytes, bytes]:
        """Encrypts a plaintext secret into ciphertext, nonce, and tag."""
        raw_bytes = plaintext.encode("utf-8")
        if HAS_CRYPTOGRAPHY:
            aesgcm = AESGCM(bytes(self._master_key))
            nonce = secrets.token_bytes(12)
            # AESGCM.encrypt appends 16-byte tag to ciphertext
            encrypted = aesgcm.encrypt(nonce, raw_bytes, None)
            ciphertext = encrypted[:-16]
            tag = encrypted[-16:]
            return ciphertext, nonce, tag
        else:
            # Secure authenticated stream cipher fallback (HMAC-SHA256 CTR + HMAC-SHA256 Auth Tag)
            nonce = secrets.token_bytes(16)
            enc_key = hmac.new(bytes(self._master_key), b"enc-key" + nonce, hashlib.sha256).digest()
            auth_key = hmac.new(bytes(self._master_key), b"auth-key" + nonce, hashlib.sha256).digest()

            keystream = bytearray()
            counter = 0
            while len(keystream) < len(raw_bytes):
                block = hmac.new(enc_key, counter.to_bytes(4, "big"), hashlib.sha256).digest()
                keystream.extend(block)
                counter += 1

            ciphertext = bytes(b ^ k for b, k in zip(raw_bytes, keystream[:len(raw_bytes)]))
            tag = hmac.new(auth_key, nonce + ciphertext, hashlib.sha256).digest()[:16]
            return ciphertext, nonce, tag

    def _decrypt(self, ciphertext: bytes, nonce: bytes, tag: bytes) -> str:
        """Decrypts and authenticates ciphertext back to plaintext."""
        if HAS_CRYPTOGRAPHY:
            aesgcm = AESGCM(bytes(self._master_key))
            decrypted = aesgcm.decrypt(nonce, ciphertext + tag, None)
            return decrypted.decode("utf-8")
        else:
            auth_key = hmac.new(bytes(self._master_key), b"auth-key" + nonce, hashlib.sha256).digest()
            expected_tag = hmac.new(auth_key, nonce + ciphertext, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(tag, expected_tag):
                raise EphemeralVaultError("Vault integrity verification failed: tag mismatch.")

            enc_key = hmac.new(bytes(self._master_key), b"enc-key" + nonce, hashlib.sha256).digest()
            keystream = bytearray()
            counter = 0
            while len(keystream) < len(ciphertext):
                block = hmac.new(enc_key, counter.to_bytes(4, "big"), hashlib.sha256).digest()
                keystream.extend(block)
                counter += 1

            plaintext_bytes = bytes(c ^ k for c, k in zip(ciphertext, keystream[:len(ciphertext)]))
            return plaintext_bytes.decode("utf-8")

    def store(self, key: str, value: str) -> None:
        """Stores a secret under the given key in encrypted memory."""
        self._check_ttl()
        if not isinstance(value, str):
            raise TypeError("Vault only stores string values.")
        self._store[key] = self._encrypt(value)

    def retrieve(self, key: str) -> Optional[str]:
        """Retrieves and decrypts a secret from memory."""
        self._check_ttl()
        if key not in self._store:
            return None
        ciphertext, nonce, tag = self._store[key]
        return self._decrypt(ciphertext, nonce, tag)

    def has(self, key: str) -> bool:
        """Checks if a key exists in the vault without decrypting."""
        self._check_ttl()
        return key in self._store

    def purge(self) -> None:
        """Securely zeroes out master key and stored secrets in memory."""
        if self._is_purged:
            return

        # Zero out master key memory
        for i in range(len(self._master_key)):
            self._master_key[i] = 0

        # Clear dictionary references
        self._store.clear()
        self._is_purged = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.purge()

    def __del__(self):
        self.purge()
