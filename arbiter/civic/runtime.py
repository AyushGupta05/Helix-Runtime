from __future__ import annotations

from langchain_mcp_adapters.client import MultiServerMCPClient

from arbiter.runtime.config import RuntimeConfig


class CivicRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return bool(self.config.civic_url and self.config.civic_token)

    def client(self) -> MultiServerMCPClient:
        if not self.available():
            raise ValueError("Civic is not configured.")
        return MultiServerMCPClient(
            {
                "civic": {
                    "url": self.config.civic_url,
                    "transport": "streamable_http",
                    "headers": {"Authorization": f"Bearer {self.config.civic_token}"},
                }
            }
        )

