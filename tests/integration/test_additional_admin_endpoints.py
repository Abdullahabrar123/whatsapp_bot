"""Integration tests for all admin and health endpoints."""

import pytest
from httpx import AsyncClient

from app.core.settings import Settings


@pytest.mark.asyncio
async def test_health_live_and_ready(api_client: AsyncClient):
    live_resp = await api_client.get("/health/live")
    assert live_resp.status_code == 200
    assert live_resp.json()["status"] == "alive"

    ready_resp = await api_client.get("/health/ready")
    assert ready_resp.status_code == 200
    assert ready_resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_metrics_endpoint(api_client: AsyncClient):
    metrics_resp = await api_client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert (
        "process_virtual_memory_bytes" in metrics_resp.text
        or "python_info" in metrics_resp.text
    )


@pytest.mark.asyncio
async def test_admin_suppression_and_reindex(
    api_client: AsyncClient, test_settings: Settings
):
    headers = {"X-Admin-API-Key": test_settings.admin_api_key.get_secret_value()}

    # Add suppression
    supp_resp = await api_client.post(
        "/admin/suppressions",
        headers=headers,
        json={"phone": "+447400123456", "reason": "operator_block", "detail": "Test detail"},
    )
    assert supp_resp.status_code == 200
    assert supp_resp.json()["suppressed"] is True

    # List suppressions
    list_resp = await api_client.get("/admin/suppressions", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1

    # Reindex knowledge
    reindex_resp = await api_client.post("/admin/knowledge/reindex", headers=headers)
    assert reindex_resp.status_code == 200
    assert reindex_resp.json()["status"] == "reindexed"
