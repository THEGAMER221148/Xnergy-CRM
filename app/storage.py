import hashlib
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from .config import get_settings

_service = None
_credential = None


async def init_storage():
    global _service, _credential

    s = get_settings()

    if s.azure_storage_connection_string:
        _service = BlobServiceClient.from_connection_string(
            s.azure_storage_connection_string
        )
    else:
        _credential = DefaultAzureCredential()
        _service = BlobServiceClient(
            account_url=s.azure_storage_account_url,
            credential=_credential,
        )

    for name in (s.azure_quarantine_container, s.azure_clean_container):
        try:
            await _service.create_container(name)
        except Exception as e:
            # Existing containers are expected; all other initialization
            # failures are fatal so the application does not start broken.
            message = str(e).lower()
            if (
                "containeralreadyexists" not in message
                and "container already exists" not in message
            ):
                raise


async def close_storage():
    global _service, _credential

    if _service:
        await _service.close()

    if _credential:
        await _credential.close()


def object_key(tenant_id, deal_id, doc_id):
    return f"{tenant_id}/{deal_id}/{doc_id}"


async def upload_quarantine(
    key: str,
    data: bytes,
    content_type: str | None,
    metadata: dict,
):
    s = get_settings()

    blob = _service.get_blob_client(
        container=s.azure_quarantine_container,
        blob=key,
    )

    md = {
        k: str(v)[:1024]
        for k, v in metadata.items()
        if v is not None
    }

    await blob.upload_blob(
        data,
        overwrite=False,
        metadata=md,
    )

    return hashlib.sha256(data).hexdigest()


async def delete_quarantine(key: str):
    s = get_settings()

    blob = _service.get_blob_client(
        container=s.azure_quarantine_container,
        blob=key,
    )

    await blob.delete_blob(delete_snapshots="include")


async def promote_clean(key: str):
    """Promote a trusted scanned blob from quarantine to clean storage."""

    s = get_settings()

    src = _service.get_blob_client(
        container=s.azure_quarantine_container,
        blob=key,
    )

    dst = _service.get_blob_client(
        container=s.azure_clean_container,
        blob=key,
    )

    await dst.start_copy_from_url(src.url)

    # Wait for Azure's server-side copy operation to complete before
    # deleting the quarantine copy.
    for _ in range(60):
        dp = await dst.get_blob_properties()

        if dp.copy.status == "success":
            await src.delete_blob(delete_snapshots="include")
            return dp.etag

        if dp.copy.status == "failed":
            raise RuntimeError("Blob promotion copy failed")

        import asyncio
        await asyncio.sleep(0.5)

    raise TimeoutError("Blob promotion copy timed out")


async def clean_blob_stream(key: str):
    s = get_settings()

    blob = _service.get_blob_client(
        container=s.azure_clean_container,
        blob=key,
    )

    return await blob.download_blob()


async def blob_properties(container: str, key: str):
    return await _service.get_blob_client(
        container=container,
        blob=key,
    ).get_blob_properties()
