"""The PydanticAI agent: Claude, the gateway toolset, and usage reporting.

Model defaults (all overridable through ``AgentSettings``):

* ``claude-opus-5-5`` with adaptive thinking (always on for this model) at its default
  ``medium`` effort;
* server-side fallbacks (``fallbacks: "default"``, beta ``server-side-fallback-2026-07-01``):
  a request a safety classifier declines is re-run on Anthropic's recommended fallback
  model inside the same call. The usage report then names the model that answered, and
  the gateway prices it (unknown models at its conservative fallback rate);
* automatic prompt caching (tools and instructions are identical across threads);
* eager input streaming of tool arguments. PydanticAI raises ``IncompleteToolCall`` for
  a call cut off by ``max_tokens``, and the gateway validates every argument against the
  tool's schema, so a truncated call is never executed.
"""

from __future__ import annotations

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.capabilities.instrumentation import Instrumentation
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings

from .settings import AgentSettings
from .toolset import AgentDeps, GatewayToolset
from .usage import GatewayUsageReporter

SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"

INSTRUCTIONS = """\
You are a finance-operations assistant working on the company's CRM. You can act only \
through the tools provided; every call passes through a governed gateway that enforces \
access policy, budgets and human approval.

- Look facts up with the tools rather than assuming them, and quote the figures you used.
- Some changes, such as credit-limit updates, are held for human review. A held call has \
not run: never describe it as done. When a reviewer decides, you receive the outcome; \
report it exactly, including a rejection and its reason.
- Do not repeat a call the gateway reports as held, rejected or of unknown outcome.
- Finish with a short summary: what you found, what changed, and anything still pending.\
"""

AgentType = Agent[AgentDeps, str | DeferredToolRequests]


def claude_model(settings: AgentSettings) -> tuple[Model, ModelSettings]:
    if settings.anthropic_api_key is None:
        msg = "ANTHROPIC_API_KEY (or AGENT_ANTHROPIC_API_KEY) is not set"
        raise ValueError(msg)
    model = AnthropicModel(
        settings.model,
        provider=AnthropicProvider(api_key=settings.anthropic_api_key.get_secret_value()),
    )
    model_settings = AnthropicModelSettings(
        max_tokens=settings.max_tokens,
        anthropic_thinking={"type": "adaptive"},
        anthropic_effort=settings.effort,
        anthropic_cache=True,
        anthropic_cache_instructions=True,
        anthropic_eager_input_streaming=True,
    )
    if settings.server_side_fallbacks:
        model_settings["anthropic_betas"] = [SERVER_SIDE_FALLBACK_BETA]
        model_settings["extra_body"] = {"fallbacks": "default"}
    return model, model_settings


def build_agent(
    model: Model,
    model_settings: ModelSettings | None = None,
    *,
    trace_content: bool = False,
) -> AgentType:
    """The agent. ``trace_content=False`` keeps prompts and tool payloads (CRM data) out
    of trace spans; token counts, models and timings are still recorded."""
    return Agent(
        model,
        output_type=[str, DeferredToolRequests],
        deps_type=AgentDeps,
        instructions=INSTRUCTIONS,
        toolsets=[GatewayToolset()],
        capabilities=[
            GatewayUsageReporter(),
            Instrumentation(
                InstrumentationSettings(include_content=trace_content, include_binary_content=False)
            ),
        ],
        model_settings=model_settings,
        retries=2,
        name="crm-copilot",
    )
