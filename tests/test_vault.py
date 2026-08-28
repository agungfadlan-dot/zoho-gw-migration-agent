"""
Unit tests for security/vault.py
"""

import time
import unittest
import sys
import os

# Ensure package root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.vault import EphemeralVault, EphemeralVaultError


class TestEphemeralVault(unittest.TestCase):

    def test_store_and_retrieve_secret(self):
        with EphemeralVault() as vault:
            vault.store("zoho_client_secret", "super_secret_12345")
            vault.store("google_sa_key", '{"private_key": "some_key"}')

            self.assertTrue(vault.has("zoho_client_secret"))
            self.assertTrue(vault.has("google_sa_key"))
            self.assertFalse(vault.has("non_existent"))

            self.assertEqual(vault.retrieve("zoho_client_secret"), "super_secret_12345")
            self.assertEqual(vault.retrieve("google_sa_key"), '{"private_key": "some_key"}')

    def test_memory_purge(self):
        vault = EphemeralVault()
        vault.store("token", "secret_token_val")
        self.assertEqual(vault.retrieve("token"), "secret_token_val")

        vault.purge()

        with self.assertRaises(EphemeralVaultError):
            vault.retrieve("token")

    def test_ttl_expiration(self):
        # 1-second TTL for testing
        vault = EphemeralVault(ttl_seconds=1)
        vault.store("temp_key", "temporary_value")
        self.assertEqual(vault.retrieve("temp_key"), "temporary_value")

        time.sleep(1.2)

        with self.assertRaises(EphemeralVaultError):
            vault.retrieve("temp_key")


if __name__ == "__main__":
    unittest.main()
