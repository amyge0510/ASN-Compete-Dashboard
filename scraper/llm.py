"""Provider-portable LLM access — Anthropic (default) or OpenAI.

The pipeline needs exactly two LLM capabilities, so exactly two functions
are abstracted; everything provider-specific lives in this one file and
analyze.py / discovery.py stay provider-agnostic:

  structured(user, schema, ...)   -> schema-validated JSON dict
  web_research(prompt, ...)       -> prose findings backed by live web search

Provider selection: LLM_PROVIDER=anthropic|openai, or auto-detected from
which API key is set (Anthropic wins if both are present).

Environment:
  Anthropic: ANTHROPIC_API_KEY, ANTHROPIC_MODEL (default claude-opus-4-8),
             ANTHROPIC_BASE_URL (centralized gateway)
  OpenAI:    OPENAI_API_KEY, OPENAI_MODEL (default gpt-5),
             OPENAI_BASE_URL (Azure OpenAI / centralized gateway)

The openai package is only imported when actually selected, so it is an
optional dependency (pip install openai).
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

MAX_CONTINUATIONS = 5  # Anthropic pause_turn resume cap


def provider() -> str:
    explicit = os.environ.get("LLM_PROVIDER", "").lower()
    if explicit in ("anthropic", "openai"):
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "anthropic"  # fails at call time with the SDK's clear missing-key error


def _anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-8"


def _openai_model() -> str:
    # `or` (not a default arg) so an empty string — e.g. an unset CI repo
    # variable rendered as "" — falls back correctly.
    return os.environ.get("OPENAI_MODEL") or "gpt-5"


def _openai_client():
    from openai import OpenAI
    # Always pass an explicit, non-empty base URL: with base_url=None the SDK
    # re-reads the env var itself, so an empty OPENAI_BASE_URL (an unset CI
    # variable rendered as "") becomes a scheme-less URL and every request
    # dies with UnsupportedProtocol.
    return OpenAI(base_url=os.environ.get("OPENAI_BASE_URL")
                  or "https://api.openai.com/v1")


def structured(user: str, schema: dict, system: str | None = None,
               max_tokens: int = 16000) -> dict:
    """One LLM call returning JSON guaranteed to match the given schema."""
    if provider() == "openai":
        client = _openai_client()
        messages = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": "user", "content": user}]
        response = client.chat.completions.create(
            model=_openai_model(),
            max_completion_tokens=max_tokens,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": {
                "name": "result", "strict": True, "schema": schema}},
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("Analysis truncated — raise max_tokens or lower the batch size")
        if not choice.message.content:
            refusal = getattr(choice.message, "refusal", None)
            raise RuntimeError(
                f"Model returned no content (finish_reason={choice.finish_reason}, "
                f"refusal={refusal!r})")
        return json.loads(choice.message.content)

    from anthropic import Anthropic
    client = Anthropic()  # reads ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
    response = client.messages.create(
        model=_anthropic_model(),
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        **({"system": system} if system else {}),
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Analysis truncated — raise max_tokens or lower the batch size")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(
            f"Model returned no text block (stop_reason={response.stop_reason})")
    return json.loads(text)


def web_research(prompt: str, max_searches: int = 8) -> str:
    """One web-search-backed research call; returns the model's prose findings."""
    if provider() == "openai":
        client = _openai_client()
        response = client.responses.create(
            model=_openai_model(),
            tools=[{"type": "web_search"}],
            max_tool_calls=max_searches,  # cost ceiling, mirrors Anthropic max_uses
            input=prompt,
        )
        return response.output_text

    from anthropic import Anthropic
    client = Anthropic()
    tools = [{"type": "web_search_20260209", "name": "web_search",
              "max_uses": max_searches}]
    messages = [{"role": "user", "content": prompt}]
    for _ in range(MAX_CONTINUATIONS):
        response = client.messages.create(
            model=_anthropic_model(),
            max_tokens=16000,
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            if response.stop_reason == "max_tokens":
                logger.warning("web research hit the output token cap — "
                               "findings may be truncated")
            return "".join(b.text for b in response.content if b.type == "text")
        messages.append({"role": "assistant", "content": response.content})

    logger.warning("web research did not complete within %d continuations",
                   MAX_CONTINUATIONS)
    return ""
