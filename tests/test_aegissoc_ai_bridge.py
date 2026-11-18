"""Job Hunter AI routing: verify UK South preference and allowlisted AegisSOC fallback."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import secrets


class TestModelCredentialRouting(unittest.TestCase):
    def setUp(self):
        self.saved = dict(secrets._azure_cache)
        secrets._azure_cache.update(value=None, expires=0)
        self.addCleanup(lambda: secrets._azure_cache.update(self.saved))
        environment = patch.dict(os.environ, {
            "APP_ENV": "production",
            "AZURE_KEY_VAULT_URL": "https://jobsaiuksouth17kv.vault.azure.net/",
            "OPENAI_API_KEY": "",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def _resolve(self, credentials):
        def secret(name):
            if name not in credentials:
                raise KeyError(name)
            return SimpleNamespace(value=credentials[name])
        fake = SimpleNamespace(get_secret=secret)
        with patch("azure.identity.DefaultAzureCredential"), \
             patch("azure.keyvault.secrets.SecretClient", return_value=fake):
            return secrets.get_ai_backend()

    def test_verified_aegissoc_secondary_key_fallback(self):
        result = self._resolve({
            "shared-aegissoc-openai-endpoint":
                "https://oai-aegissoc-ed8f4826ca81.openai.azure.com/",
            "shared-aegissoc-openai-deployment": "aegissoc-remediation",
            "shared-aegissoc-openai-api-key": "fake-test-key-do-not-use-12345",
        })
        self.assertEqual("azure_openai", result["provider"])
        self.assertEqual("aegissoc_shared", result["source"])
        self.assertEqual("eastus_global", result["model_region"])
        self.assertEqual("aegissoc-remediation", result["deployment"])

    def test_uk_south_deployment_preferred_when_ready(self):
        result = self._resolve({
            "azure-openai-endpoint": "https://jobsaiuksouth17openai.openai.azure.com/",
            "azure-openai-deployment": "jobhunter-chat",
            "azure-openai-api-key": "fake-uk-south-key-do-not-use-123",
            "shared-aegissoc-openai-endpoint":
                "https://oai-aegissoc-ed8f4826ca81.openai.azure.com/",
            "shared-aegissoc-openai-deployment": "aegissoc-remediation",
            "shared-aegissoc-openai-api-key": "fake-test-key-do-not-use-12345",
        })
        self.assertEqual("jobhunter_uksouth", result["source"])
        self.assertEqual("uksouth", result["model_region"])

    def test_cross_project_endpoints_are_explicitly_allowlisted(self):
        result = self._resolve({
            "shared-aegissoc-openai-endpoint": "https://attacker.openai.azure.com/",
            "shared-aegissoc-openai-deployment": "aegissoc-remediation",
            "shared-aegissoc-openai-api-key": "fake-test-key-do-not-use-12345",
        })
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
