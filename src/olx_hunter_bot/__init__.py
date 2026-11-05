import logging
import os
import sys


def main() -> None:
    import uvicorn

    from olx_hunter_bot.config import get_settings

    if os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is set — it would shadow CLAUDE_CODE_OAUTH_TOKEN. Unset it.")

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    uvicorn.run(
        "olx_hunter_bot.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
