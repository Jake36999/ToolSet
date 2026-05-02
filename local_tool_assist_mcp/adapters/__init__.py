"""Provider adapters for Local Tool Assist MCP Wrapper.

Each adapter translates a model provider's tool-call protocol into the
approved set of wrapper actions.  Adapters never call Aletheia scripts
directly; they dispatch through mcp_server dispatch helpers.

Available adapters
------------------
openai_responses_adapter  — OpenAI Responses API function-tool loop
"""
