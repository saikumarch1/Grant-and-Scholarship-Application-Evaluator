"""One chat service for every agent. Works with any OpenAI compatible endpoint through Semantic Kernel."""
import asyncio
import time
from openai import AsyncOpenAI
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.connectors.ai.open_ai.services.open_ai_chat_completion_base import OpenAIChatCompletionBase
from semantic_kernel.contents.function_call_content import FunctionCallContent

from . import config

# Tests replace this to point the agents at a local stand in model server.
client_factory = None

# Patch Semantic Kernel to preserve Google Gemini thought_signature across multi-turn tool calls
_orig_get_tool_calls = OpenAIChatCompletionBase._get_tool_calls_from_chat_choice


def _patched_get_tool_calls(self, choice):
    content = choice.message if hasattr(choice, "message") else choice.delta
    if content and getattr(content, "tool_calls", None):
        res = []
        for tool in content.tool_calls:
            if tool.function is not None:
                metadata = {}
                if hasattr(tool, "extra_content") and tool.extra_content:
                    metadata["extra_content"] = tool.extra_content
                fc = FunctionCallContent(
                    id=tool.id,
                    index=getattr(tool, "index", None),
                    name=tool.function.name,
                    arguments=tool.function.arguments,
                    metadata=metadata,
                )
                res.append(fc)
        return res
    return _orig_get_tool_calls(self, choice)


OpenAIChatCompletionBase._get_tool_calls_from_chat_choice = _patched_get_tool_calls

_orig_fc_to_dict = FunctionCallContent.to_dict


def _patched_fc_to_dict(self):
    d = _orig_fc_to_dict(self)
    if self.metadata and "extra_content" in self.metadata:
        d["extra_content"] = self.metadata["extra_content"]
    return d


FunctionCallContent.to_dict = _patched_fc_to_dict


class RateLimitedCompletions:
    """Paces API calls and transparently retries on transient rate limits (429 / resource exhausted)."""

    def __init__(self, target, min_interval: float = 1.8):
        self._target = target
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._min_interval = min_interval

    async def create(self, **kwargs):
        for attempt in range(6):
            async with self._lock:
                elapsed = time.monotonic() - self._last_call
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
                self._last_call = time.monotonic()
            try:
                return await self._target.create(**kwargs)
            except Exception as err:
                text = str(err).lower()
                is_429 = "429" in text or "rate" in text or "resource" in text or "exhausted" in text
                if is_429 and attempt < 5:
                    wait_s = min(60, 4 * (2 ** attempt))
                    await asyncio.sleep(wait_s)
                    continue
                raise

    def __getattr__(self, name):
        return getattr(self._target, name)


def make_service() -> OpenAIChatCompletion:
    cfg = config.settings()
    model = cfg["model"]
    # If deprecated Gemini model or standard 3.5-flash with low daily free-tier limit is configured, resolve to gemini-3.5-flash-lite
    if model in ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash", "gemini-3.5-flash"):
        model = "gemini-3.5-flash-lite"
    if client_factory:
        client = client_factory()
    else:
        client = AsyncOpenAI(api_key=cfg["api_key"] or "missing", base_url=cfg["base_url"], timeout=cfg["timeout"], max_retries=1)
        if cfg["base_url"] and "googleapis.com" in cfg["base_url"]:
            client.chat.completions = RateLimitedCompletions(client.chat.completions, min_interval=1.8)
    return OpenAIChatCompletion(ai_model_id=model, async_client=client)
