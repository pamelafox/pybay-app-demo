import pytest

from fastapi_app.dependencies import get_azure_credential


@pytest.mark.asyncio
async def test_get_azure_credential(mock_session_env, mock_azure_credential):
    result = await get_azure_credential()
    token = result.get_token("https://vault.azure.net")
    assert token.expires_on == 9999999999
    assert token.token == ""
