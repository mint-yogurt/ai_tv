"""LLM backends behind one call: complete_json(system, prompt, schema, tier) -> dict.

There is deliberately no Claude-subscription backend: scripted use of a consumer plan isn't
clearly allowed, so the pipeline only calls an API key or an OpenAI-compatible provider. The
default is "manual" (see pipeline.StageContext.write): stages wait for someone to write the answer.

The pipeline never talks to a provider directly, so switching from Claude to DeepSeek
(or anything else) is a config change. Tiers: "strong" for screenplays, "small" for
cheap jobs like tagging and inventing product names. A stage can name its own model instead
([llm.stage_models], pipeline v2), and attach images (contact sheets) or a video (a review proxy).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
import urllib.error
import urllib.request

from .config import Config


class LLMUnavailable(RuntimeError):
    """No backend configured, or the configured one can't be used right now."""


class LLMError(RuntimeError):
    pass


class Backend:
    name = "base"

    def complete_json(self, system: str, prompt: str, schema: dict, tier: str = "strong",
                      reasoning: str | None = None, model: str | None = None,
                      images: list[Path] | None = None, video: Path | None = None) -> dict:
        """reasoning: "low", "medium" or "high" thinking effort, where the backend supports it.
        model: overrides the tier's model. images / video: files the model looks at with the prompt."""
        raise NotImplementedError


def data_url(path: Path) -> str:
    """A file as a base64 data: URL (OpenRouter takes images and videos this way)."""
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode()}"


class AnthropicAPI(Backend):
    """Anthropic API key, billed per token. Uses structured outputs for the JSON."""

    name = "anthropic"

    def __init__(self, opts: dict):
        key = os.environ.get(opts.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        if not key:
            raise LLMUnavailable(f"set {opts.get('api_key_env', 'ANTHROPIC_API_KEY')} to use the anthropic backend")
        import anthropic

        self.client = anthropic.Anthropic(api_key=key)
        self.models = {
            "strong": opts.get("model_strong", "claude-opus-5-5"),
            "small": opts.get("model_small", "claude-haiku-5-5"),
        }

    def complete_json(self, system, prompt, schema, tier="strong", reasoning=None, model=None, images=None, video=None):
        if video:
            raise LLMError("the anthropic backend can't watch video; use openai_compat with a video model")
        model = model or self.models[tier]
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image", "source": {"type": "base64", "media_type": mimetypes.guess_type(str(p))[0] or "image/png",
                                         "data": base64.b64encode(Path(p).read_bytes()).decode()}} for p in images or []]
        kwargs = dict(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content if images else prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if model.startswith("claude-opus-5") or model.startswith("claude-fable-5"):
            # On a safety decline, let the API re-run the request on a fallback model.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        response = self.client.beta.messages.create(**kwargs)
        self.last_usage = {"model": model, "provider": "anthropic", "prompt_tokens": response.usage.input_tokens,
                           "completion_tokens": response.usage.output_tokens, "reasoning_tokens": None, "cost_usd": None}
        if response.stop_reason == "refusal":
            raise LLMError("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("the response hit max_tokens before finishing")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)


class OpenAICompatible(Backend):
    """Any OpenAI-style /chat/completions endpoint (DeepSeek, OpenRouter, a local server).

    Asks for strict JSON-schema output. On OpenRouter, require_parameters keeps the request off
    providers that would ignore the schema, and usage.cost is the call's price in dollars
    (kept in last_usage for the production log).
    """

    name = "openai_compat"

    def __init__(self, opts: dict):
        self.key = os.environ.get(opts.get("api_key_env", ""), "")
        if not self.key:
            raise LLMUnavailable(f"set {opts.get('api_key_env')} to use the openai_compat backend")
        self.url = opts.get("base_url", "").rstrip("/") + "/chat/completions"
        self.openrouter = "openrouter.ai" in self.url
        self.models = {"strong": opts.get("model_strong"), "small": opts.get("model_small")}
        self.max_tokens = int(opts.get("max_tokens", 16000))
        self.timeout = int(opts.get("timeout_seconds", 600))
        self.last_usage: dict = {}

    def complete_json(self, system, prompt, schema, tier="strong", reasoning=None, model=None, images=None, video=None):
        content = prompt
        if images or video:
            content = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in images or []]
            if video:
                content.append({"type": "video_url", "video_url": {"url": data_url(video)}})
        model = model or self.models[tier]
        max_tokens = self.max_tokens
        if self.openrouter:  # never ask for more than the model can write (some stop well short of 64000)
            from . import prices
            max_tokens = min(max_tokens, prices.max_output(model) or max_tokens)
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system + "\n\nReply with one JSON object matching this JSON Schema:\n"
                 + json.dumps(schema)},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": re.sub(r"[^A-Za-z0-9_-]", "_", schema.get("title", "answer")), "strict": True, "schema": schema}},
        }
        if self.openrouter:
            body.update(provider={"require_parameters": True}, usage={"include": True})
            if reasoning:
                body["reasoning"] = {"effort": reasoning}
        try:
            data = self._post(body)
        except LLMError as exc:
            if "said 400" not in str(exc) and "said 404" not in str(exc):
                raise
            # Strict mode only takes schemas with every property required; fall back to plain JSON mode
            # (the schema is still in the system prompt, and the pipeline validates the answer).
            body["response_format"] = {"type": "json_object"}
            data = self._post(body)
        if "error" in data:
            raise LLMError(f"provider error: {data['error']}")
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        self.last_usage = {"model": data.get("model", body["model"]), "provider": data.get("provider", ""),
                           "prompt_tokens": usage.get("prompt_tokens"),
                           "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                           "completion_tokens": usage.get("completion_tokens"), "cost_usd": usage.get("cost")}
        if choice.get("finish_reason") == "length":
            raise LLMError(f"the response hit max_tokens ({max_tokens}) before finishing; "
                           f"{usage.get('completion_tokens')} tokens out, ${usage.get('cost') or 0:.4f}")
        return _parse_json_text(choice["message"].get("content") or "")


    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise LLMError(f"{self.url} said {exc.code}: {exc.read()[:500].decode(errors='replace')}") from exc


class Fake(Backend):
    """Returns canned answers. Used by the tests; never configured for real runs."""

    name = "fake"

    def __init__(self, answers: dict | None = None):
        self.answers = answers or {}
        self.calls: list[dict] = []

    def complete_json(self, system, prompt, schema, tier="strong", reasoning=None, model=None, images=None, video=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema, "tier": tier, "model": model,
                           "images": list(images or []), "video": video})
        return self.answers.get(schema.get("title", ""), {"title": "Fake", "logline": "Fake", "beats": []})


def _parse_json_text(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model did not return valid JSON: {exc}; got {text[:200]!r}") from exc


# "manual" isn't here: the pipeline handles it by saving the request and waiting for an answer.
BACKENDS = {"anthropic": AnthropicAPI, "openai_compat": OpenAICompatible}


def get(cfg: Config) -> Backend:
    name = cfg.section("llm").get("backend", "none")
    if name in ("", "none", "manual"):
        raise LLMUnavailable("no LLM backend configured: set [llm] backend in config/aitv.toml")
    if name not in BACKENDS:
        raise LLMUnavailable(f"unknown LLM backend {name!r}")
    return BACKENDS[name](cfg.section(f"llm.{name}"))
