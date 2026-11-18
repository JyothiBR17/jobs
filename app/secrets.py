"""Job Hunter-only OpenAI secret handling. Keys never enter logs, GitHub or SQL."""
import os
import time
from threading import Lock

_cache = {"key": None, "expires": 0.0}
_lock = Lock()


def get_openai_key():
    direct = os.getenv("OPENAI_API_KEY")
    if direct:
        return direct
    vault = os.getenv("AZURE_KEY_VAULT_URL")
    if not vault or os.getenv("APP_ENV") != "production":
        return None
    with _lock:
        if time.monotonic() < _cache["expires"]:
            return _cache["key"]
    try:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(vault_url=vault,
                              credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
        try:
            key = client.get_secret("openai-project-key").value
        except ResourceNotFoundError:
            key = None
    except Exception:
        # Never leak secret metadata, tracebacks or authentication data to the UI.
        key = None
    with _lock:
        _cache.update(key=key, expires=time.monotonic() + 120)
    return key


def save_openai_key(value):
    if os.getenv("APP_ENV") != "production" or not os.getenv("AZURE_KEY_VAULT_URL"):
        raise ValueError("UK South Key Vault must be provisioned first")
    if not value.startswith("sk-") or not (20 <= len(value) <= 250):
        raise ValueError("Provide a valid project-scoped OpenAI API key")
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=os.environ["AZURE_KEY_VAULT_URL"],
                          credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    client.set_secret("openai-project-key", value)
    with _lock:
        _cache.update(key=value, expires=time.monotonic() + 120)
