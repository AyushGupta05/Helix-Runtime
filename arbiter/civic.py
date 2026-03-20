from __future__ import annotations

from langchain_mcp_adapters.client import MultiServerMCPClient

from arbiter.settings import Settings, load_settings


def build_civic_client(settings: Settings | None = None) -> MultiServerMCPClient:
    """Create a Civic MCP client using streamable HTTP and Bearer auth."""

    settings = settings or load_settings()
    if not settings.civic_token:
        raise ValueError(
            "CIVIC_TOKEN is required for this autonomous runtime. "
            "Add it to your .env file or environment."
        )

    headers = {
        "Authorization": f"Bearer {settings.civic_token}",
        "Content-Type": "application/json",
    }

    return MultiServerMCPClient(
        {
            "civic": {
                "url": settings.civic_url,
                "transport": "streamable_http",
                "headers": headers,
            }
        }
    )


async def load_civic_tools(settings: Settings | None = None):
    client = build_civic_client(settings)
    return await client.get_tools()
