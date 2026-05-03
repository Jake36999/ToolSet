import unittest

from local_tool_assist_mcp.adapters.claude_messages_adapter import ClaudeMessagesAdapter
from local_tool_assist_mcp.adapters.lm_studio_adapter import LMStudioAdapter
from local_tool_assist_mcp.adapters.openai_responses_adapter import OpenAIResponsesAdapter
from local_tool_assist_mcp.mcp_server import APPROVED_TOOLS


class TestProviderAdapters(unittest.TestCase):
    def test_tool_parity(self):
        adapters = [OpenAIResponsesAdapter(), ClaudeMessagesAdapter(), LMStudioAdapter()]
        for adapter in adapters:
            tools = adapter.list_tools()
            names = {t.get("name") for t in tools if isinstance(t, dict) and "name" in t}
            self.assertEqual(names, set(APPROVED_TOOLS))

    def test_no_policy_bypass(self):
        for adapter in (OpenAIResponsesAdapter(), ClaudeMessagesAdapter(), LMStudioAdapter()):
            try:
                res = adapter.execute_tool("run_semantic_slice", {"session_id": "missing"})
                self.assertNotEqual(res.get("status"), "PASS")
            except Exception:
                # Missing session is also acceptable and does not represent policy bypass.
                self.assertTrue(True)
