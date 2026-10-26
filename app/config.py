"""网关运行配置：集中读取环境变量并校验。

所有模块统一从这里取配置，避免散落 `os.environ` 读取。
"""

import os

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN")
EXECUTOR_SHARED_TOKEN = os.environ.get("EXECUTOR_SHARED_TOKEN")
CODE_RETRY_MAX = int(os.environ.get("CODE_RETRY_MAX", "7"))
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")
DIFY_BASE_URL = os.environ.get("DIFY_BASE_URL", "http://nginx/v1").rstrip("/")
PORTAL_ADMIN_TOKEN = os.environ.get("PORTAL_ADMIN_TOKEN", "")
ORCHESTRATION_BACKEND = os.environ.get("ORCHESTRATION_BACKEND", "local").strip().lower()

# jwave-executor 沙箱内部地址（executor_internal 网络）
EXECUTOR_URL = "http://jwave-executor:8010"

# 可选的通用价格（部署者所用货币 / 百万 tokens）。默认 0 表示未知。
MODEL_PRICE_INPUT = float(os.environ.get("MODEL_PRICE_INPUT", "0"))
MODEL_PRICE_INPUT_CACHE = float(os.environ.get("MODEL_PRICE_INPUT_CACHE", "0"))
MODEL_PRICE_OUTPUT = float(os.environ.get("MODEL_PRICE_OUTPUT", "0"))


def calc_llm_cost(usage: dict) -> float:
    """Estimate one call using operator-provided generic per-million-token prices."""
    usage = usage or {}
    miss = usage.get("prompt_cache_miss_tokens") or usage.get("prompt_tokens") or 0
    hit = usage.get("prompt_cache_hit_tokens") or 0
    out = usage.get("completion_tokens") or 0
    return (miss * MODEL_PRICE_INPUT + hit * MODEL_PRICE_INPUT_CACHE + out * MODEL_PRICE_OUTPUT) / 1_000_000


def validate() -> None:
    """校验必填配置；缺失时抛错阻止启动。"""
    if not MCP_AUTH_TOKEN or len(MCP_AUTH_TOKEN) < 32:
        raise RuntimeError("MCP_AUTH_TOKEN must be set and at least 32 characters long")
    if not EXECUTOR_SHARED_TOKEN or len(EXECUTOR_SHARED_TOKEN) < 32:
        raise RuntimeError("EXECUTOR_SHARED_TOKEN must be set and at least 32 characters long")
    if ORCHESTRATION_BACKEND not in {"dify", "local"}:
        raise RuntimeError("ORCHESTRATION_BACKEND must be dify or local")

# Skill features are opt-in; no flag alone certifies a run or activates a skill.
SKILL_LIBRARY_ENABLED = os.environ.get("SKILL_LIBRARY_ENABLED", "").lower() == "true"
SKILL_RETRIEVAL_ENABLED = os.environ.get("SKILL_RETRIEVAL_ENABLED", "").lower() == "true"
SKILL_INJECTION_ENABLED = os.environ.get("SKILL_INJECTION_ENABLED", "").lower() == "true"
SKILL_LEARNING_ENABLED = os.environ.get("SKILL_LEARNING_ENABLED", "").lower() == "true"
# Authoring HTTP and workers are opt-in. No flag creates schema, profiles or trust.
SKILL_AUTHORING_ENABLED = os.environ.get("SKILL_AUTHORING_ENABLED", "").lower() == "true"
SKILL_VALIDATION_ENABLED = os.environ.get("SKILL_VALIDATION_ENABLED", "").lower() == "true"
SKILL_GENERAL_ADMISSION_ENABLED = os.environ.get("SKILL_GENERAL_ADMISSION_ENABLED", "").lower() == "true"
SKILL_GENERAL_ASSET_ROOT = os.environ.get("SKILL_GENERAL_ASSET_ROOT", "/app/data/skill_assets")
SKILL_GENERAL_REFERENCE_ROOT = os.environ.get("SKILL_GENERAL_REFERENCE_ROOT", "/app/skill-references")
SKILL_GENERAL_RUNTIME_SHA256 = os.environ.get("SKILL_GENERAL_RUNTIME_SHA256", "")
SKILL_BOOTSTRAP_ALL_FIXED = os.environ.get("SKILL_BOOTSTRAP_ALL_FIXED", "").lower() == "true"

# Explicit host-only provenance observation is separate from automatic learning.
SKILL_CASE_OBSERVATION_ENABLED = os.environ.get("SKILL_CASE_OBSERVATION_ENABLED", "").lower() == "true"

# Explicit operator intake is independent of automatic learning. Neither switch
# certifies provenance; local profiles require managed receipts and physics,
# while advanced cross-host stages still require signed authority.
SKILL_MANUAL_INTAKE_ENABLED = os.environ.get("SKILL_MANUAL_INTAKE_ENABLED", "").lower() == "true"

def fixed_intake_enabled():
    return SKILL_LIBRARY_ENABLED is True and (
        SKILL_LEARNING_ENABLED is True or SKILL_MANUAL_INTAKE_ENABLED is True)
