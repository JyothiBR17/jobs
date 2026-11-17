import os
from pathlib import Path
from uuid import uuid4


def store_resume(content):
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
        raise ValueError("Upload a valid PDF")
    key = f"{uuid4()}.pdf"
    account = os.getenv("BLOB_ACCOUNT_URL")
    if account:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        client = BlobClient(account, os.getenv("BLOB_CONTAINER", "jobsai-resumes"), key, credential=DefaultAzureCredential())
        client.upload_blob(content, overwrite=False)
    elif os.getenv("APP_ENV") == "production":
        raise RuntimeError("Azure Blob Storage is required in production")
    else:
        folder = Path(os.getenv("PRIVATE_DIR", "private"))
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(folder / key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    return key
