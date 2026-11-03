"""Provider adapters: request shape and response parsing, against a mocked HTTP transport."""
import json

import httpx
import pytest

from app.llm import providers
from app.llm.providers import LLMConfig, LLMError, complete_json

ANSWER = '{"answer": "ok", "not_found": false, "confidence": 0.9, "claims": []}'


def _mock(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(providers.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_anthropic(monkeypatch):
    def handler(req: httpx.Request):
        assert req.url == "https://api.anthropic.com/v1/messages"
        assert req.headers["x-api-key"] == "k"
        body = json.loads(req.content)
        assert body["system"] and body["messages"][0]["role"] == "user"
        return httpx.Response(200, json={"model": "claude-x", "content": [{"type": "text", "text": ANSWER}],
                                         "usage": {"input_tokens": 10, "output_tokens": 5}})
    _mock(monkeypatch, handler)
    r = complete_json(LLMConfig("anthropic", "claude-x", api_key="k"), "sys", "user")
    assert r.text == ANSWER and r.input_tokens == 10


def test_openai_json_mode(monkeypatch):
    def handler(req):
        assert req.url == "https://api.openai.com/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer k"
        assert json.loads(req.content)["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": ANSWER}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
    _mock(monkeypatch, handler)
    assert complete_json(LLMConfig("openai", "gpt", api_key="k"), "s", "u").output_tokens == 2


def test_azure(monkeypatch):
    def handler(req):
        assert str(req.url).startswith("https://res.openai.azure.com/openai/deployments/dep/chat/completions?api-version=")
        assert req.headers["api-key"] == "k"
        return httpx.Response(200, json={"choices": [{"message": {"content": ANSWER}}]})
    _mock(monkeypatch, handler)
    cfg = LLMConfig("azure_openai", "", api_key="k", base_url="https://res.openai.azure.com", deployment="dep")
    assert cfg.configured and complete_json(cfg, "s", "u").text == ANSWER


def test_gemini(monkeypatch):
    def handler(req):
        assert "/models/gemini-x:generateContent" in str(req.url) and req.headers["x-goog-api-key"] == "k"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": ANSWER}]}}]})
    _mock(monkeypatch, handler)
    assert complete_json(LLMConfig("gemini", "gemini-x", api_key="k"), "s", "u").text == ANSWER


def test_ollama_needs_no_key(monkeypatch):
    def handler(req):
        assert req.url == "http://localhost:11434/api/chat" and json.loads(req.content)["format"] == "json"
        return httpx.Response(200, json={"message": {"content": ANSWER}, "model": "llama"})
    _mock(monkeypatch, handler)
    cfg = LLMConfig("ollama", "llama")
    assert cfg.configured and complete_json(cfg, "s", "u").model == "llama"


def test_http_errors_become_llm_errors(monkeypatch):
    _mock(monkeypatch, lambda req: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(LLMError, match="HTTP 401"):
        complete_json(LLMConfig("openai", "gpt", api_key="bad"), "s", "u")
