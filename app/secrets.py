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


_azure_cache = {"value": None, "expires": 0.0}
_azure_lock = Lock()


def get_azure_ai_settings():
    """Fetch actual UK South Azure OpenAI credentials from the Job Hunter vault.

    All three secrets are created by the dedicated Azure AI provisioning workflow.
    A missing deployment is not treated as a configured model.
    """
    vault = os.getenv("AZURE_KEY_VAULT_URL")
    if not vault or os.getenv("APP_ENV") != "production":
        return None
    with _azure_lock:
        if time.monotonic() < _azure_cache["expires"]:
            return _azure_cache["value"]
    settings = None
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(
            vault_url=vault,
            credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
        )
        endpoint = client.get_secret("azure-openai-endpoint").value
        deployment = client.get_secret("azure-openai-deployment").value
        key = client.get_secret("azure-openai-api-key").value
        if (
            endpoint == "https://jobsaiuksouth17openai.openai.azure.com/"
            and deployment == "jobhunter-chat"
            and key and len(key) >= 20
        ):
            settings = {"endpoint": endpoint, "deployment": deployment, "key": key}
    except Exception:
        # Missing/denied secrets are a non-ready state, not a successful AI deployment.
        settings = None
    with _azure_lock:
        _azure_cache.update(value=settings, expires=time.monotonic() + (300 if settings else 45))
    return settings


def get_ai_backend():
    """Prefer UK South Azure OpenAI; fall back to an explicitly configured OpenAI key."""
    azure = get_azure_ai_settings()
    if azure is not None:
        return {"provider": "azure_openai", **azure}
    key = get_openai_key()
    if key:
        return {"provider": "openai", "key": key}
    return None
