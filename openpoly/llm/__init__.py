"""LLM — a thin Anthropic API client wrapper for section impls. Resolves an
``api_key_ref``, forces one structured tool call, returns the tool's input
dict."""

from openpoly.llm.client import LLMClient, LLMError

__all__ = ["LLMClient", "LLMError"]
