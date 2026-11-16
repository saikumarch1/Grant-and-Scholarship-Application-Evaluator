"""Base class for the LLM agents.

Each agent owns a Semantic Kernel Kernel with its own plugin of plain Python tool functions.
The model decides which tools to call. A kernel filter turns every tool call into a live event.
"""
import asyncio
import time

from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.connectors.ai.open_ai import OpenAIChatPromptExecutionSettings
from semantic_kernel.contents import ChatHistory
from semantic_kernel.functions import kernel_function

COMMON = (
    "You are one agent in a multi agent scholarship evaluation system. Work only through the tools you are given and the "
    "published rubric. Never invent facts about the applicant. If information is missing, report it with the tool provided "
    "for that and do not guess. Cite rubric line ids such as M1 or E2. Tools that submit results validate them against the "
    "rubric and reply REJECTED with the reason when something is wrong, so read every tool reply and correct yourself. "
    "When your work is complete, reply with one or two plain sentences saying what you concluded."
)


class AgentIncomplete(Exception):
    pass


def _short(value, limit=220):
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _transient(err: Exception) -> bool:
    text = str(err).lower()
    return any(k in text for k in ("429", "rate limit", "timed out", "timeout", "temporarily", "connection", "502", "503", "504"))


class LLMAgent:
    name = "Agent"
    plugin = "agent"
    instructions = ""
    max_rounds = 14

    def __init__(self, state, bus, service, run_id: str = None):
        self.state, self.bus, self.service = state, bus, service
        self.run_id = run_id or state.app_id
        self.kernel = Kernel()
        tools = {fn.__name__: kernel_function(name=fn.__name__, description=(fn.__doc__ or "").strip().split("\n")[0])(fn) for fn in self.tools()}
        self.kernel.add_plugin(tools, plugin_name=self.plugin)
        self.kernel.add_filter("function_invocation", self._on_tool)
        self.history = ChatHistory()
        self.history.add_system_message(f"You are the {self.name}.\n{COMMON}\n\n{self.instructions}")
        behavior = FunctionChoiceBehavior.Auto(auto_invoke=True)
        behavior.maximum_auto_invoke_attempts = self.max_rounds
        self.settings = OpenAIChatPromptExecutionSettings(function_choice_behavior=behavior, temperature=0.1)

    def tools(self) -> list:
        return []

    def incomplete(self) -> list:
        return []

    def emit(self, type_, **data):
        return self.bus.emit(self.run_id, type_, self.name, **data)

    async def _on_tool(self, context, next):
        name = context.function.name
        self.emit("tool_call", tool=name, args={k: _short(v, 160) for k, v in dict(context.arguments).items()})
        await next(context)
        self.emit("tool_result", tool=name, result=_short(getattr(context.result, "value", context.result)))

    async def chat(self, message: str) -> str:
        """Send one user message, let the model call tools until it answers, and return the answer."""
        self.history.add_user_message(message)
        for attempt in range(5):
            try:
                reply = await asyncio.wait_for(
                    self.service.get_chat_message_content(self.history, self.settings, kernel=self.kernel), timeout=240)
                break
            except Exception as err:
                if attempt == 4 or not _transient(err):
                    raise
                self.emit("retry", attempt=attempt + 1, reason=_short(err, 120))
                await asyncio.sleep(min(30, 3 * 2 ** attempt))
        text = str(reply.content or "").strip()
        self.emit("agent_message", text=text)
        return text

    async def run(self, task: str):
        started = time.perf_counter()
        self.emit("agent_start")
        try:
            note = await self.chat(task)
            for _ in range(2):
                todo = self.incomplete()
                if not todo:
                    break
                note = await self.chat(f"Your work is not finished yet: {', '.join(todo)}. Use your tools to complete it.")
            if self.incomplete():
                raise AgentIncomplete(f"{self.name} did not finish: {', '.join(self.incomplete())}")
        except Exception as err:
            self.emit("agent_error", error=_short(err, 300))
            raise
        self.state.notes[self.name] = note
        self.emit("agent_done", seconds=round(time.perf_counter() - started, 2))
        return note
