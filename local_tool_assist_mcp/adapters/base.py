"""Abstract base class for Local Tool Assist provider adapters.

Every concrete adapter translates provider tool-call protocol shapes into
the same eight approved wrapper actions.  No adapter duplicates Aletheia
tool logic or calls scripts directly.

Interface mirrors the design in
``aletheia_toolchain/docs/local_tool_assist_provider_integrations.md``.
"""

from abc import ABC, abstractmethod


class ProviderAdapter(ABC):
    """Translate provider tool-call protocol into wrapper actions."""

    @abstractmethod
    def list_tools(self) -> list:
        """Return provider-formatted tool definitions for the 8 wrapper actions."""
        ...

    @abstractmethod
    def execute_tool(self, name: str, arguments: dict) -> dict:
        """Execute one wrapper action; return a JSON-serialisable result dict."""
        ...

    @abstractmethod
    def run_guided_workflow(self, objective: str, target_repo: str) -> dict:
        """Run the full guided workflow; return final artifact paths."""
        ...
