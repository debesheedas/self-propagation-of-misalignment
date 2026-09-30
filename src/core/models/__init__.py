"""Model-client wrappers.

One uniform `chat()` call over the Anthropic Messages API and the OpenAI-compatible Chat Completions API
(OpenRouter, OpenAI, Google), so experiment code never branches on provider. Resolve a model by alias:

    from core.models import get_client
    client = get_client("opus")
    out = client.chat(system=..., messages=[{"role": "user", "content": "hi"}])
    print(out.text, out.usage)

Clients are created lazily and cached per alias.
"""

from .base import ChatResult, ModelClient, get_client

__all__ = ["ChatResult", "ModelClient", "get_client"]
