"""Integration tests for protected Operator API endpoints."""

import pytest
from httpx import AsyncClient

from app.core.settings import Settings


@pytest.mark.asyncio
async def test_admin_api_status(api_client: AsyncClient, test_settings: Settings):
    headers = {"X-Admin-API-Key": test_settings.admin_api_key.get_secret_value()}
    response = await api_client.get("/admin/status", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["business"]["id"] == "brightsmile_dental"
    assert data["bot"]["globally_paused"] is False


@pytest.mark.asyncio
async def test_admin_api_pause_resume_global(api_client: AsyncClient, test_settings: Settings):
    headers = {"X-Admin-API-Key": test_settings.admin_api_key.get_secret_value()}

    # Pause
    pause_resp = await api_client.post(
        "/admin/bot/pause",
        headers=headers,
        json={"reason": "Testing pause", "actor": "tester"},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["paused"] is True

    # Check status
    status_resp = await api_client.get("/admin/status", headers=headers)
    assert status_resp.json()["bot"]["globally_paused"] is True

    # Resume
    resume_resp = await api_client.post(
        "/admin/bot/resume",
        headers=headers,
        json={"actor": "tester"},
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["paused"] is False
