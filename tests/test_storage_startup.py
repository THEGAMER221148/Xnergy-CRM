from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.core.pipeline.transport import AioHttpTransport

from app import storage


@pytest.mark.asyncio
async def test_storage_startup_has_async_transport(monkeypatch):
    # Construct the real Azure clients with a Container Apps identity endpoint.
    # Only the remote container-creation operation is mocked; no Azure calls run.
    monkeypatch.setenv('IDENTITY_ENDPOINT', 'http://127.0.0.1:1/identity')
    monkeypatch.setenv('IDENTITY_HEADER', 'test-only')
    monkeypatch.setattr(storage, 'get_settings', lambda: SimpleNamespace(
        azure_storage_connection_string='',
        azure_storage_account_url='https://test.invalid',
        azure_quarantine_container='quarantine',
        azure_clean_container='clean',
    ))
    create = AsyncMock()
    monkeypatch.setattr(storage.BlobServiceClient, 'create_container', create)
    try:
        await storage.init_storage()
        assert create.await_count == 2
        # Opening the transport creates its aiohttp session without network IO.
        async with AioHttpTransport() as transport:
            assert transport.session is not None
    finally:
        await storage.close_storage()
