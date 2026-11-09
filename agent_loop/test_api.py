"""One tiny API call to confirm the key and the connection (about $0.01). Run from the workspace root:
  python agent_loop/test_api.py"""
import asyncio, sys
sys.path.insert(0, 'agent_loop')
from loop_manager import api_key
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage


async def main():
    k = api_key()
    if not k:
        sys.exit('No API key: set ANTHROPIC_API_KEY or create agent_loop/api_key.txt')
    print('key starts with', k[:13] + '...')
    opts = ClaudeAgentOptions(model='claude-sonnet-5-5', tools=[], max_turns=1, max_budget_usd=0.05,
                              setting_sources=[], env={'ANTHROPIC_API_KEY': k})
    async for m in query(prompt='Reply with exactly: API OK', options=opts):
        if isinstance(m, ResultMessage):
            print('result:', m.result, '| error:', m.is_error, m.errors or '', '| cost $', m.total_cost_usd)

asyncio.run(main())
