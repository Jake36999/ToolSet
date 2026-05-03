"""LM Studio adapter using the canonical Local Tool Assist action set."""

from local_tool_assist_mcp.adapters.base import ProviderAdapter
from local_tool_assist_mcp.mcp_server import APPROVED_TOOLS
from local_tool_assist_mcp.workflow import run_guided_repository_investigation
from local_tool_assist_mcp.adapters.openai_responses_adapter import dispatch_openai_tool_call


class LMStudioAdapter(ProviderAdapter):
    """Adapter for LM Studio OpenAI-compatible function calling."""

    def __init__(self, output_root: str = "") -> None:
        self._output_root = output_root

    def list_tools(self) -> list:
        return [{"name": name} for name in sorted(APPROVED_TOOLS)]

    def execute_tool(self, name: str, arguments: dict) -> dict:
        return dispatch_openai_tool_call(name, arguments, output_root=self._output_root)

    def run_guided_workflow(self, objective: str, target_repo: str) -> dict:
        return run_guided_repository_investigation(
            objective=objective,
            target_repo=target_repo,
            output_root=self._output_root,
        )
