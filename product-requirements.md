A telegram bot powered with AI agent. For now claude agent sdk should be used.
The aim of agent is to help find good listing on olx. Agent should regularly call olx mcp by user request.
User can have several requests. For example yesterday it asked for laptops and today for good headphones.
Listing lookup stops when user closes the request. User should have ability to manually trigger the lookup.
Agent should have access to web search tool to compare olx prices against new product prices.

System requirements:

Use FastAPI + webhook for hosting a bot. Organise kind of cronjob or scheduled event or any other mechanism that allows
periodic lookups. Also user should be able to speak with the agent directly.