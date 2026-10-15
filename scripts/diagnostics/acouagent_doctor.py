"""Secret-safe install and runtime diagnostics for AcouAgent."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "acouagent-doctor.v1"
TOKEN_NAMES = ("MCP_AUTH_TOKEN", "EXECUTOR_SHARED_TOKEN")
PLACEHOLDER_MARKERS = ("replace-with", "changeme", "placeholder", "example")


@dataclass(frozen=True)
class Check:
    check_id: str
    status: str
    message: str


def _run(arguments: list[str], root: Path, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip():
            values[key.strip()] = value.strip()
    return values


def _safe_version(value: str) -> str:
    cleaned = "".join(char for char in value.strip() if char.isalnum() or char in ".+_-")
    return cleaned[:80] or "unknown"


def _request(url: str, timeout: float = 4.0) -> tuple[int, bytes] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status), response.read(262145)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _configuration_checks(root: Path, values: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    env_path = root / ".env"
    if not env_path.is_file():
        checks.append(Check("configuration.env", "fail", ".env 不存在；先运行安装器的 prepare-only。"))
        return checks
    checks.append(Check("configuration.env", "pass", ".env 已存在。"))

    bad_tokens = []
    for name in TOKEN_NAMES:
        value = values.get(name, "")
        lowered = value.lower()
        if len(value) < 32 or any(marker in lowered for marker in PLACEHOLDER_MARKERS):
            bad_tokens.append(name)
    if values.get(TOKEN_NAMES[0]) and values.get(TOKEN_NAMES[0]) == values.get(TOKEN_NAMES[1]):
        bad_tokens.extend(TOKEN_NAMES)
    if bad_tokens:
        names = "、".join(sorted(set(bad_tokens)))
        checks.append(Check(
            "configuration.tokens",
            "fail",
            f"{names} 缺失、过短、仍是占位符或彼此相同；诊断不会显示实际值。",
        ))
    else:
        checks.append(Check("configuration.tokens", "pass", "两个本地令牌均已配置且彼此不同。"))

    backend = values.get("ORCHESTRATION_BACKEND", "local").lower()
    if backend not in {"local", "dify"}:
        checks.append(Check("configuration.backend", "fail", "ORCHESTRATION_BACKEND 取值无效。"))
    else:
        checks.append(Check("configuration.backend", "pass", f"编排后端为 {backend}。"))

    profile_fields = (values.get("MODEL_BASE_URL", ""), values.get("MODEL_NAME", ""),
                      values.get("MODEL_API_KEY", ""))
    profile_json = values.get("MODEL_PROFILES_JSON", "")
    if backend == "local" and profile_json:
        try:
            profile_document = json.loads(profile_json)
        except (json.JSONDecodeError, UnicodeError, TypeError):
            profile_document = None
        if not isinstance(profile_document, dict) or not profile_document:
            checks.append(Check(
                "configuration.model",
                "fail",
                "MODEL_PROFILES_JSON 不是非空 JSON 对象；诊断不会显示其内容。",
            ))
        else:
            checks.append(Check("configuration.model", "pass", "检测到可解析的多模型 profile 配置。"))
    elif backend == "local" and any(profile_fields) and not all(profile_fields):
        checks.append(Check(
            "configuration.model",
            "fail",
            "长期模型 profile 只配置了部分必需字段。",
        ))
    elif backend == "local" and not all(profile_fields):
        checks.append(Check(
            "configuration.model",
            "warn",
            "未配置长期模型 profile；启动后可在门户注册临时 BYOK。",
        ))
    elif backend == "local":
        checks.append(Check("configuration.model", "pass", "检测到长期模型 profile 配置。"))
    else:
        checks.append(Check("configuration.model", "pass", "Dify 兼容模式由 Dify 管理内部模型。"))
    return checks


def _host_checks(root: Path) -> list[Check]:
    checks = [
        Check(
            "host.platform",
            "pass",
            f"主机为 {platform.system() or 'unknown'} / {platform.machine() or 'unknown'}。",
        )
    ]
    free_gib = shutil.disk_usage(root).free / (1024 ** 3)
    checks.append(Check(
        "host.disk",
        "pass" if free_gib >= 5 else "warn",
        f"项目所在磁盘可用空间约 {free_gib:.1f} GiB。"
        + ("" if free_gib >= 5 else " 建议至少保留 5 GiB。"),
    ))
    return checks


def _docker_checks(root: Path) -> list[Check]:
    if not shutil.which("docker"):
        return [Check("docker.cli", "fail", "未找到 docker 命令。")]
    checks = [Check("docker.cli", "pass", "已找到 docker 命令。")]
    try:
        engine = _run(["docker", "version", "--format", "{{.Server.Version}}"], root)
        compose = _run(["docker", "compose", "version", "--short"], root)
    except (subprocess.TimeoutExpired, OSError):
        return checks + [Check("docker.engine", "fail", "Docker Engine 检查超时或无法启动。")]
    checks.append(Check(
        "docker.engine",
        "pass" if engine.returncode == 0 else "fail",
        f"Docker Engine {_safe_version(engine.stdout)} 可访问。"
        if engine.returncode == 0 else "Docker Engine 不可访问。",
    ))
    checks.append(Check(
        "docker.compose",
        "pass" if compose.returncode == 0 else "fail",
        f"Docker Compose {_safe_version(compose.stdout)} 可用。"
        if compose.returncode == 0 else "Docker Compose 不可用。",
    ))
    if engine.returncode == 0 and compose.returncode == 0:
        try:
            rendered = _run(["docker", "compose", "config", "--quiet"], root)
            checks.append(Check(
                "docker.compose_config",
                "pass" if rendered.returncode == 0 else "fail",
                "Compose 配置可解析。" if rendered.returncode == 0 else "Compose 配置解析失败。",
            ))
        except (subprocess.TimeoutExpired, OSError):
            checks.append(Check("docker.compose_config", "fail", "Compose 配置检查超时。"))
    return checks


def _port_check(values: dict[str, str]) -> Check:
    bind_ip = values.get("DIFY_MCP_BIND_IP", "127.0.0.1")
    probe_ip = "0.0.0.0" if bind_ip in {"", "0.0.0.0"} else bind_ip
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((probe_ip, 8001))
    except OSError:
        return Check("host.port_8001", "warn", "端口 8001 已占用；若网关未运行，请释放该端口。")
    finally:
        sock.close()
    return Check("host.port_8001", "pass", "端口 8001 可用于网关。")


def _runtime_checks(root: Path) -> list[Check]:
    checks: list[Check] = []
    health = _request("http://127.0.0.1:8001/health")
    if health != (200, b"ok"):
        return [Check("runtime.health", "fail", "网关 /health 未返回 ok。")]
    checks.append(Check("runtime.health", "pass", "网关 /health 正常。"))

    profiles = _request("http://127.0.0.1:8001/api/model-profiles")
    try:
        profile_payload = json.loads(profiles[1]) if profiles and profiles[0] == 200 else None
    except (json.JSONDecodeError, UnicodeError, TypeError):
        profile_payload = None
    if isinstance(profile_payload, dict) and profile_payload.get("orchestration_backend") in {"local", "dify"}:
        configured = sum(
            1 for item in profile_payload.get("profiles", [])
            if isinstance(item, dict) and item.get("configured") is True
        )
        checks.append(Check(
            "runtime.model_profiles",
            "pass" if configured else "warn",
            f"模型 profile 接口正常，已配置 {configured} 个长期 profile。"
            if configured else "模型 profile 接口正常；当前可在门户注册临时 BYOK。",
        ))
    else:
        checks.append(Check("runtime.model_profiles", "fail", "模型 profile 接口返回无效。"))

    try:
        skills = _run(
            ["docker", "exec", "dify-mcp", "python", "-m",
             "scripts.skill_library.local_basic", "status-all"],
            root,
            timeout=60,
        )
        skill_payload = json.loads(skills.stdout) if skills.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, UnicodeError):
        skill_payload = None
    ready = bool(
        isinstance(skill_payload, dict)
        and skill_payload.get("ready") is True
        and skill_payload.get("ready_count") == 17
    )
    checks.append(Check(
        "runtime.skills",
        "pass" if ready else "fail",
        "17 项固定技能已完成本机自举。" if ready else "固定技能库存未达到 17/17 ready。",
    ))

    try:
        audit = _run(
            ["docker", "exec", "dify-mcp", "python", "-m",
             "scripts.skill_library.audit_report"],
            root,
            timeout=60,
        )
        backup_api = _run(
            ["docker", "exec", "dify-mcp", "python", "-c",
             "from app.skill_store import SkillStore; assert callable(SkillStore.backup_to)"],
            root,
        )
    except (subprocess.TimeoutExpired, OSError):
        audit = backup_api = None
    checks.append(Check(
        "runtime.skill_audit",
        "pass" if audit is not None and audit.returncode == 0 else "fail",
        "技能库结构审计通过。" if audit is not None and audit.returncode == 0
        else "技能库结构审计失败。",
    ))
    checks.append(Check(
        "runtime.backup_api",
        "pass" if backup_api is not None and backup_api.returncode == 0 else "fail",
        "SQLite 在线备份入口可用。" if backup_api is not None and backup_api.returncode == 0
        else "SQLite 在线备份入口不可用。",
    ))

    checks.extend(_container_boundary_checks(root))
    checks.append(_source_identity_check(root))
    return checks


def _container_boundary_checks(root: Path) -> list[Check]:
    template = (
        "{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}|"
        "{{json .HostConfig.SecurityOpt}}|{{.HostConfig.PidsLimit}}"
    )
    try:
        result = _run(["docker", "inspect", "jwave-executor", "--format", template], root)
    except (subprocess.TimeoutExpired, OSError):
        result = None
    if result is None or result.returncode != 0:
        return [Check("runtime.executor_boundary", "fail", "无法检查 executor 容器边界。")]
    parts = result.stdout.strip().split("|")
    boundary_ok = (
        len(parts) == 4
        and parts[0].lower() == "true"
        and '"ALL"' in parts[1]
        and "no-new-privileges:true" in parts[2]
        and parts[3].isdigit()
        and int(parts[3]) > 0
    )
    checks = [Check(
        "runtime.executor_boundary",
        "pass" if boundary_ok else "fail",
        "executor 保持只读、cap-drop、no-new-privileges 和进程上限。"
        if boundary_ok else "executor 安全边界不完整。",
    )]

    try:
        networks = _run(
            ["docker", "inspect", "jwave-executor", "--format",
             "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}\n{{end}}"],
            root,
        )
        names = [name.strip() for name in networks.stdout.splitlines() if name.strip()]
        internal = any(
            _run(["docker", "network", "inspect", name, "--format", "{{.Internal}}"], root).stdout
            .strip().lower() == "true"
            for name in names[:8]
        )
    except (subprocess.TimeoutExpired, OSError):
        internal = False
    checks.append(Check(
        "runtime.executor_network",
        "pass" if internal else "fail",
        "executor 至少连接一个 internal 网络。" if internal else "未确认 executor 的 internal 网络。",
    ))
    return checks


def _source_identity_check(root: Path) -> Check:
    local = root / "app" / "local_workflow.py"
    if not local.is_file():
        return Check("runtime.source_identity", "fail", "缺少本地 local_workflow.py。")
    expected = hashlib.sha256(local.read_bytes()).hexdigest()
    try:
        result = _run(
            ["docker", "exec", "dify-mcp", "sha256sum", "/app/app/local_workflow.py"],
            root,
        )
        actual = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else ""
    except (subprocess.TimeoutExpired, OSError):
        actual = ""
    return Check(
        "runtime.source_identity",
        "pass" if actual == expected else "fail",
        "运行网关源码与当前检出一致。" if actual == expected else "运行网关源码与当前检出不一致。",
    )


def diagnose(root: Path, requested_mode: str) -> dict[str, Any]:
    root = root.resolve()
    values = _env(root / ".env")
    checks = _host_checks(root)
    checks.extend(_configuration_checks(root, values))
    checks.extend(_docker_checks(root))

    health = _request("http://127.0.0.1:8001/health")
    mode = requested_mode
    if mode == "auto":
        mode = "runtime" if health == (200, b"ok") else "prepare"
        if mode == "prepare":
            checks.append(Check(
                "runtime.auto_detection",
                "warn",
                "未检测到运行中的本地网关，本次只执行安装前诊断。",
            ))
    if mode == "runtime":
        checks.extend(_runtime_checks(root))
    else:
        checks.append(_port_check(values))

    statuses = {item.status for item in checks}
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "overall": overall,
        "checks": [asdict(item) for item in checks],
    }


def _render_human(report: dict[str, Any]) -> str:
    symbols = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    lines = [f"AcouAgent Doctor · mode={report['mode']} · overall={report['overall'].upper()}"]
    for item in report["checks"]:
        lines.append(f"[{symbols.get(item['status'], item['status'].upper())}] "
                     f"{item['check_id']}: {item['message']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="AcouAgent secret-safe deployment diagnostics")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--mode", choices=("auto", "prepare", "runtime"), default="auto")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    report = diagnose(args.repo_root, args.mode)
    if args.as_json:
        json.dump(report, sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(_render_human(report))
    return 1 if report["overall"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
