"""LLM plumbing: an OpenAI-compatible chat model plus small parsing helpers.

The original Dify workflow used the marketplace plugins ``deepseek-v4-pro`` /
``deepseek-coder`` together with provider-side structured output.  Here we only
assume a generic **OpenAI-compatible chat completions endpoint**, which keeps
the workflow portable across DeepSeek / vLLM / Ollama / OpenAI / ...
"""
from __future__ import annotations

from typing import Any, Sequence, Type

from pydantic import BaseModel, Field, StrictBool

from .config import Settings


class GeneratedCode(BaseModel):
    """Structured output of ⑤ 代码生成 (Dify schema property: ``code``)."""

    code: str = Field(default="", description="将代码注入到这里，解释器会自动运行")


class FixedCode(BaseModel):
    """Structured output of ⑦ 代码纠错 (Dify schema property: ``code_2``)."""

    code_2: str = Field(default="", description="将代码注入这里")


class CompletionReview(BaseModel):
    """Structured output of the requirement-completion review node."""

    completed: StrictBool = Field(description="问题中的用户需求是否已经完成")
    missing_requirements: list[str] = Field(
        default_factory=list,
        description="明确要求但实际缺失的交付物；完成时必须为空",
    )
    feedback: str = Field(default="", description="未完成时给代码生成器的具体修改意见")


def strip_code_fences(text: str) -> str:
    """Remove a surrounding markdown code fence (the prompt forbids them, but
    models sometimes add them anyway)."""
    if text is None:
        return ""
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
    if t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.rstrip("\n")


def build_chat_model(settings: Settings, model: str):
    """Return a ``ChatOpenAI`` bound to the configured OpenAI-compatible API."""
    from langchain_openai import ChatOpenAI  # imported lazily: optional at import time

    if not settings.openai_api_key:
        raise RuntimeError("jwave_flow bridge model credentials are not configured")
    kwargs: dict[str, Any] = dict(
        model=model,
        api_key=settings.openai_api_key,
        temperature=settings.temperature,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


def message_text(message: Any) -> str:
    """把 ChatModel 的返回统一成纯文本（部分 provider 返回 content blocks）。"""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def _first_present(data: Any, keys: Sequence[str]) -> str | None:
    if isinstance(data, BaseModel):
        data = data.model_dump()
    if isinstance(data, dict):
        for k in keys:
            if data.get(k):
                return str(data[k])
    return None


def invoke_code_llm(
    llm,
    prompt_value,
    schema: Type[BaseModel],
    keys: Sequence[str],
) -> str:
    """Ask ``llm`` for code, preferring provider-side structured output.

    Falls back to plain text + fence stripping for endpoints that do not
    implement ``response_format`` / tool calling.
    """
    # 1) native structured output (tool calling / json_schema)
    try:
        structured = llm.with_structured_output(schema)
        result = structured.invoke(prompt_value)
        code = _first_present(result, keys)
        if code and code.strip():
            return strip_code_fences(code)
    except Exception:
        pass

    # 2) plain completion
    try:
        message = llm.invoke(prompt_value)
    except Exception as exc:
        return CompletionReview(
            completed=False,
            feedback=f"需求完成度评审调用失败（{type(exc).__name__}），无法确认答案是否完成。",
        )
    text = message.content if hasattr(message, "content") else str(message)
    if isinstance(text, list):  # some providers return content blocks
        text = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in text
        )
    return strip_code_fences(text)


def invoke_completion_review(llm, prompt_value) -> CompletionReview:
    """Ask the reviewer for a strict completion decision.

    Provider-side structured output is preferred.  The text fallback accepts
    only a JSON object that validates against ``CompletionReview``; ambiguous
    prose therefore fails closed instead of being treated as completion.
    """
    try:
        structured = llm.with_structured_output(CompletionReview)
        result = structured.invoke(prompt_value)
        if isinstance(result, CompletionReview):
            return result
        if isinstance(result, BaseModel):
            return CompletionReview.model_validate(result.model_dump())
        return CompletionReview.model_validate(result)
    except Exception:
        message = llm.invoke(prompt_value)
        text = message_text(message).strip()
        candidates = [text]
        if "{" in text and "}" in text:
            candidates.append(text[text.find("{") : text.rfind("}") + 1])
        for candidate in candidates:
            try:
                return CompletionReview.model_validate_json(candidate)
            except Exception:
                continue
        return CompletionReview(
            completed=False,
            feedback="需求完成度评审返回了无法解析的结果，必须重新检查并补充用户需求对应的结果。",
        )
