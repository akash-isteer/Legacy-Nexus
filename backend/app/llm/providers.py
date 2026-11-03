"""LLM provider adapters over plain HTTPS. The LLM is used only for repository Q&A."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

# Shown in Settings. Model names are suggestions only: any model id can be typed in.
PROVIDER_CATALOG: dict[str, dict] = {
    "anthropic": {
        "label": "Anthropic Claude",
        "fields": ["api_key", "model"],
        "default_base_url": "https://api.anthropic.com",
        "model_suggestions": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
        "key_hint": "sk-ant-…",
    },
    "openai": {
        "label": "OpenAI",
        "fields": ["api_key", "model", "base_url"],
        "default_base_url": "https://api.openai.com/v1",
        "model_suggestions": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
        "key_hint": "sk-…",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "fields": ["api_key", "base_url", "deployment", "api_version"],
        "default_base_url": "",
        "default_api_version": "2024-10-21",
        "model_suggestions": [],
        "key_hint": "Azure resource key",
        "base_url_hint": "https://<resource>.openai.azure.com",
    },
    "gemini": {
        "label": "Google Gemini",
        "fields": ["api_key", "model"],
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model_suggestions": ["gemini-2.5-pro", "gemini-2.5-flash"],
        "key_hint": "AIza…",
    },
    "ollama": {
        "label": "Ollama (local, no data leaves your network)",
        "fields": ["model", "base_url"],
        "default_base_url": "http://localhost:11434",
        "model_suggestions": ["qwen2.5-coder:14b", "llama3.1:8b"],
        "key_hint": "",
    },
    "openai_compatible": {
        "label": "OpenAI-compatible endpoint (vLLM, LM Studio, gateways)",
        "fields": ["api_key", "model", "base_url"],
        "default_base_url": "",
        "model_suggestions": [],
        "key_hint": "Optional",
        "base_url_hint": "https://your-gateway/v1",
    },
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    api_version: str = ""
    deployment: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 1200
    timeout_seconds: int = 90

    @property
    def configured(self) -> bool:
        if self.provider not in PROVIDER_CATALOG:
            return False
        if self.provider == "azure_openai":
            return bool(self.api_key and self.base_url and self.deployment)
        if self.provider == "ollama":
            return bool(self.model)
        if self.provider == "openai_compatible":
            return bool(self.model and self.base_url)
        return bool(self.api_key and self.model)

    @property
    def display_model(self) -> str:
        return self.deployment if self.provider == "azure_openai" else self.model


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def complete_json(cfg: LLMConfig, system: str, user: str) -> LLMResult:
    """Send one system+user prompt and return the raw text, asking the provider for JSON output."""
    handler = {
        "anthropic": _anthropic, "openai": _openai, "openai_compatible": _openai, "azure_openai": _azure,
        "gemini": _gemini, "ollama": _ollama,
    }.get(cfg.provider)
    if handler is None:
        raise LLMError(f"Unsupported provider '{cfg.provider}'")
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=cfg.timeout_seconds) as client:
            result = handler(client, cfg, system, user)
    except httpx.TimeoutException as exc:
        raise LLMError(f"The {cfg.provider} request timed out after {cfg.timeout_seconds}s") from exc
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        raise LLMError(f"{cfg.provider} returned HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"Could not reach {cfg.provider}: {exc}") from exc
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def _anthropic(client: httpx.Client, cfg: LLMConfig, system: str, user: str) -> LLMResult:
    base = (cfg.base_url or PROVIDER_CATALOG["anthropic"]["default_base_url"]).rstrip("/")
    r = client.post(f"{base}/v1/messages", headers={
        "x-api-key": cfg.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, json={
        "model": cfg.model, "max_tokens": cfg.max_output_tokens, "temperature": cfg.temperature,
        "system": system, "messages": [{"role": "user", "content": user}],
    })
    r.raise_for_status()
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    usage = data.get("usage", {})
    return LLMResult(text, data.get("model", cfg.model), usage.get("input_tokens", 0), usage.get("output_tokens", 0))


def _openai_body(cfg: LLMConfig, system: str, user: str, include_model: bool = True) -> dict:
    body = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_output_tokens,
        "response_format": {"type": "json_object"},
    }
    if include_model:
        body["model"] = cfg.model
    return body


def _parse_openai(r: httpx.Response, fallback_model: str) -> LLMResult:
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"].get("content") or ""
    usage = data.get("usage") or {}
    return LLMResult(text, data.get("model", fallback_model), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


def _openai(client: httpx.Client, cfg: LLMConfig, system: str, user: str) -> LLMResult:
    base = (cfg.base_url or PROVIDER_CATALOG["openai"]["default_base_url"]).rstrip("/")
    headers = {"content-type": "application/json"}
    if cfg.api_key:
        headers["authorization"] = f"Bearer {cfg.api_key}"
    r = client.post(f"{base}/chat/completions", headers=headers, json=_openai_body(cfg, system, user))
    if r.status_code == 400 and "response_format" in r.text:  # some compatible servers lack JSON mode
        body = _openai_body(cfg, system, user)
        body.pop("response_format")
        r = client.post(f"{base}/chat/completions", headers=headers, json=body)
    return _parse_openai(r, cfg.model)


def _azure(client: httpx.Client, cfg: LLMConfig, system: str, user: str) -> LLMResult:
    version = cfg.api_version or PROVIDER_CATALOG["azure_openai"]["default_api_version"]
    url = f"{cfg.base_url.rstrip('/')}/openai/deployments/{cfg.deployment}/chat/completions"
    r = client.post(url, params={"api-version": version}, headers={"api-key": cfg.api_key},
                    json=_openai_body(cfg, system, user, include_model=False))
    return _parse_openai(r, cfg.deployment)


def _gemini(client: httpx.Client, cfg: LLMConfig, system: str, user: str) -> LLMResult:
    base = (cfg.base_url or PROVIDER_CATALOG["gemini"]["default_base_url"]).rstrip("/")
    # Gemini 3.x "flash/pro" are reasoning models that, left at the default thinking level, can spend
    # 40-120s+ "thinking" before answering and blow past the request timeout. For grounded JSON Q&A we do
    # not need deep reasoning, so request the low thinking level (ignored by non-thinking models).
    r = client.post(f"{base}/models/{cfg.model}:generateContent", headers={"x-goog-api-key": cfg.api_key}, json={
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": cfg.temperature, "maxOutputTokens": cfg.max_output_tokens,
                             "responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}},
    })
    r.raise_for_status()
    data = r.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    usage = data.get("usageMetadata", {})
    return LLMResult("".join(p.get("text", "") for p in parts), cfg.model, usage.get("promptTokenCount", 0),
                     usage.get("candidatesTokenCount", 0))


def _ollama(client: httpx.Client, cfg: LLMConfig, system: str, user: str) -> LLMResult:
    base = (cfg.base_url or PROVIDER_CATALOG["ollama"]["default_base_url"]).rstrip("/")
    r = client.post(f"{base}/api/chat", json={
        "model": cfg.model, "stream": False, "format": "json",
        "options": {"temperature": cfg.temperature, "num_predict": cfg.max_output_tokens},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    })
    r.raise_for_status()
    data = r.json()
    return LLMResult(data.get("message", {}).get("content", ""), data.get("model", cfg.model),
                     data.get("prompt_eval_count", 0), data.get("eval_count", 0))
