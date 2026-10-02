"""User-facing sentences for the agent audit activity log.

The graph still runs the same nodes. This module only chooses the text a
person sees in the activity log. Pattern hits stay clues, not confirmed
vulnerabilities.
"""

from __future__ import annotations

from typing import Any

_NODE_MESSAGES = {
    "validate_request": "已确认这次要审计的范围",
    "ingest_repository": "已读入项目里的源代码",
    "build_manifest": "已列出要审计的文件",
    "static_scan": "已检查常见的危险写法",
    "plan_audit": "已排好分析顺序",
    "aggregate_findings": "已把不同文件里的同类问题合并",
    "dedupe_findings": "已去掉重复的记录",
    "prioritize_findings": "已按严重程度排序",
    "verify_audit_findings": "已做进程内的模式确认，这不是隔离沙箱",
    "generate_report": "已整理审计结论",
}

# Longer needles first so "SELECT * FROM" wins over "SELECT *".
_CLUE_NEEDLES: tuple[tuple[str, str], ...] = (
    ("dangerouslySetInnerHTML", "在 React 里直接插入 HTML"),
    ("innerHTML", "把内容直接写进页面"),
    ("pickle.loads", "反序列化不可信数据"),
    ("subprocess.call", "调用系统命令"),
    ("os.system", "调用系统命令"),
    ("SELECT * FROM", "把 SQL 写进字符串"),
    ("SELECT *", "把 SQL 写进字符串"),
    ("verify=False", "关闭了证书校验"),
    ("password =", "疑似硬编码口令"),
    ("eval(", "使用 eval 执行字符串"),
    ("exec(", "使用 exec 执行字符串"),
    ("md5(", "使用了较弱的哈希"),
)

_CLUE_TITLES = {
    "code injection": "可能的代码注入",
    "insecure deserialization": "不安全的反序列化",
    "command execution": "可能的命令执行",
    "os command injection": "可能的系统命令注入",
    "dom xss sink": "把内容直接写进页面",
    "react xss sink": "在 React 里直接插入 HTML",
    "weak hash": "使用了较弱的哈希",
    "tls verification disabled": "关闭了证书校验",
    "possible sql string": "把 SQL 写进字符串",
    "hardcoded secret pattern": "疑似硬编码口令",
}

_SEVERITY = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
}

_MAX_PATTERN_LINES = 8
_MAX_MODEL_LINES = 8


def user_node_message(
    node_name: str,
    values: dict[str, Any] | None = None,
    *,
    file_total: int = 0,
) -> str | None:
    """One Chinese sentence for a finished graph node, or None to stay quiet."""
    state = values or {}
    if node_name == "analyze_file":
        analyzed, total = progress_counts(state, fallback_total=file_total)
        return analyze_progress_message(analyzed, total)
    base = _NODE_MESSAGES.get(node_name)
    if base is None:
        return None
    if node_name == "build_manifest":
        count = _manifest_count(state) or file_total
        if count > 0:
            return f"已列出 {count} 个要审计的文件"
    if node_name == "plan_audit":
        count = _plan_count(state) or file_total
        if count > 0:
            return f"已排好分析顺序，计划分析 {count} 个文件"
    return base


def analyze_progress_message(analyzed: int, total: int) -> str:
    if total > 0:
        return f"分析进度: {analyzed}/{total} 个文件"
    return f"分析进度: {analyzed} 个文件"


def progress_counts(
    values: dict[str, Any] | None,
    *,
    fallback_total: int = 0,
) -> tuple[int, int]:
    state = values or {}
    analyzed = _int_field(state.get("budget"), "files_analyzed")
    # The manifest is the set actually queued. The snapshot can be larger.
    total = _manifest_count(state) or _plan_count(state) or fallback_total
    return analyzed, total


def finding_log_lines(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Readable log rows. Tool hits collapse into the clue they repeat."""
    rows: list[dict[str, Any]] = []
    pattern = _pattern_rows(findings)
    model = [item for item in findings if isinstance(item, dict) and not is_pattern_finding(item)]
    if pattern:
        rows.append({"message": "下面是代码里对上的写法，还不是已经确认的漏洞。"})
        for item in pattern[:_MAX_PATTERN_LINES]:
            rows.append(_row(item, model=False))
        hidden = len(pattern) - _MAX_PATTERN_LINES
        if hidden > 0:
            rows.append({"message": f"还有 {hidden} 条同类线索没有逐条写在这里。"})
    if model:
        rows.append({"message": "下面是模型给出的意见，还没有复现。"})
        for item in model[:_MAX_MODEL_LINES]:
            rows.append(_row(item, model=True))
        hidden = len(model) - _MAX_MODEL_LINES
        if hidden > 0:
            rows.append({"message": f"还有 {hidden} 条模型意见没有逐条写在这里。"})
    return rows


def user_outcome_message(
    *,
    status: str,
    files_analyzed: int,
    files_total: int,
    findings: list[dict[str, Any]],
    duration_ms: int,
    coverage: dict[str, Any] | None = None,
    verification_enabled: bool = False,
    verified_count: int = 0,
    error: str | None = None,
) -> str:
    """Closing sentence. Partial runs do not say the audit found vulnerabilities."""
    coverage = coverage or {}
    elapsed = format_elapsed(duration_ms)
    elapsed_bit = f"，用时 {elapsed}" if elapsed else ""
    scope = _scope(files_analyzed, files_total)
    all_files = files_total > 0 and files_analyzed >= files_total
    kind = (status or "").lower()
    if kind == "completed":
        head = f"审计结束，{scope}{elapsed_bit}。"
    elif kind == "paused":
        head = f"审计先停在这里，{scope}{elapsed_bit}。已经得到的结果会保留，可以稍后继续。"
    elif kind == "cancelled":
        head = f"审计已取消，{scope}{elapsed_bit}。"
    elif kind == "failed":
        head = f"审计没有跑完，{scope}{elapsed_bit}。"
    elif not all_files:
        head = f"这次没有看完，{scope}{elapsed_bit}。"
    elif coverage.get("model_unavailable"):
        head = f"文件都扫过了，但没有调用模型，{scope}{elapsed_bit}。"
    else:
        head = f"文件都看过了，但结论还不完整，{scope}{elapsed_bit}。"

    pattern_count, model_count = _split_counts(findings)
    if pattern_count and model_count:
        record = (
            f"记下 {pattern_count + model_count} 条记录，"
            f"其中 {model_count} 条是模型意见，{pattern_count} 条是模式扫描线索。"
        )
    elif pattern_count:
        record = f"记下 {pattern_count} 条模式扫描线索。"
    elif model_count:
        record = f"记下 {model_count} 条模型意见。"
    else:
        record = "这次没有记下具体问题。"

    sentences = [head, record]
    if pattern_count:
        sentences.append("模式扫描只说明代码里出现了这些写法，还不能当成已经确认的漏洞。")
    reason = _coverage_reason(coverage)
    if reason and not ("没有调用模型" in head and "没有配置模型" in reason):
        sentences.append(reason)
    sentences.append(_verification_sentence(verification_enabled, verified_count))
    if kind == "failed" and error:
        clean = " ".join(str(error).split())
        if len(clean) > 180:
            clean = clean[:179] + "…"
        sentences.append(f"原因：{clean}")
    return "".join(sentences)


def format_elapsed(duration_ms: int) -> str:
    if duration_ms <= 0:
        return ""
    if duration_ms < 1000:
        return "不到 1 秒"
    seconds = duration_ms // 1000
    minutes, sec = divmod(seconds, 60)
    if minutes <= 0:
        return f"{sec} 秒"
    hours, minutes = divmod(minutes, 60)
    if hours <= 0:
        return f"{minutes} 分 {sec} 秒"
    return f"{hours} 小时 {minutes} 分"


def is_tool_hit(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "")
    analyzer = str(item.get("analyzer") or "")
    rule = str(item.get("rule_id") or "")
    return title.startswith("Tool hit:") or analyzer.startswith("tool:") or rule.startswith("tool:")


def is_pattern_finding(item: dict[str, Any]) -> bool:
    analyzer = str(item.get("analyzer") or "")
    rule = str(item.get("rule_id") or "")
    return is_tool_hit(item) or analyzer == "heuristic" or rule.startswith("heur:")


def clue_label(item: dict[str, Any]) -> str:
    blob = f"{item.get('rule_id') or ''} {item.get('title') or ''}"
    for needle, label in _CLUE_NEEDLES:
        if needle in blob:
            return label
    title = str(item.get("title") or "").strip()
    mapped = _CLUE_TITLES.get(title.lower())
    if mapped:
        return mapped
    if title.startswith("Tool hit:"):
        title = title[len("Tool hit:") :].strip()
    return title or "可疑写法"


def _row(item: dict[str, Any], *, model: bool) -> dict[str, Any]:
    label = _clean_title(str(item.get("title") or "")) if model else clue_label(item)
    prefix = "模型意见 · " if model else ""
    severity = str(item.get("severity") or "").lower()
    row: dict[str, Any] = {
        "message": f"{prefix}{_location_phrase(item)}：{label}{_severity_suffix(item)}"
    }
    if severity in _SEVERITY:
        row["severity"] = severity
    return row


def _location_phrase(item: dict[str, Any]) -> str:
    path = _short_path(str(item.get("file_path") or "")) or "某个文件"
    if is_tool_hit(item):
        return path
    try:
        line = int(item.get("line_start") or 0)
    except (TypeError, ValueError):
        line = 0
    if line > 0:
        return f"{path} 第 {line} 行"
    return path


def _severity_suffix(item: dict[str, Any]) -> str:
    label = _SEVERITY.get(str(item.get("severity") or "").lower())
    if not label:
        return ""
    return f"（{label}）"


def _clean_title(title: str) -> str:
    text = " ".join(title.split())
    if len(text) > 80:
        return text[:79] + "…"
    return text or "未命名的意见"


def _pattern_rows(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[tuple[str, str]] = []
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for item in findings:
        if not isinstance(item, dict) or not is_pattern_finding(item):
            continue
        key = (_short_path(str(item.get("file_path") or "")), clue_label(item))
        current = chosen.get(key)
        if current is None:
            order.append(key)
            chosen[key] = item
        elif is_tool_hit(current) and not is_tool_hit(item):
            chosen[key] = item
    return [chosen[key] for key in order]


def _split_counts(findings: list[dict[str, Any]]) -> tuple[int, int]:
    pattern = 0
    model = 0
    for item in findings:
        if not isinstance(item, dict):
            continue
        if is_pattern_finding(item):
            pattern += 1
        else:
            model += 1
    return pattern, model


def _coverage_reason(coverage: dict[str, Any]) -> str:
    if coverage.get("model_unavailable"):
        return "没有配置模型，这次只做了代码模式扫描。"
    skipped = coverage.get("skipped_units") or []
    budget = coverage.get("budget_exhausted") or any(
        isinstance(unit, dict) and unit.get("reason") == "budget_exhausted" for unit in skipped
    )
    if budget:
        return "剩下的文件因为分析预算用完，没有继续看。"
    if coverage.get("truncated_units"):
        return "有的文件比较长，只读了前面一部分。"
    if coverage.get("omitted_units"):
        return "有些文件超出本次数量上限，没有排进来。"
    rejected = coverage.get("rejected_findings") or []
    if isinstance(rejected, list) and rejected:
        return f"有 {len(rejected)} 条模型意见因为对不上代码位置，没有收进结果。"
    if coverage.get("planner_error"):
        return "排计划时模型没有返回可用结果，改用了默认顺序。"
    return ""


def _verification_sentence(enabled: bool, verified_count: int) -> str:
    if enabled and verified_count:
        return f"其中 {verified_count} 条和进程内的危险写法对上了。" "这还不是隔离环境里的复现。"
    if enabled:
        return "做了进程内的模式确认，没有条目被标成已确认。这还不是隔离环境里的复现。"
    return "这些结果还没有在沙箱里复现。"


def _scope(files_analyzed: int, files_total: int) -> str:
    if files_total > 0:
        return f"看了 {files_analyzed}/{files_total} 个文件"
    return f"看了 {files_analyzed} 个文件"


def _short_path(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if len(parts) <= 2:
        return "/".join(parts)
    return "/".join(parts[-2:])


def _manifest_count(values: dict[str, Any]) -> int:
    return _len_field(values.get("manifest"), "files")


def _plan_count(values: dict[str, Any]) -> int:
    count = _len_field(values.get("plan"), "tasks")
    if count:
        return count
    pending = values.get("pending_task_ids")
    if isinstance(pending, list):
        return len(pending)
    return 0


def _len_field(obj: Any, name: str) -> int:
    value = _field(obj, name)
    if isinstance(value, list):
        return len(value)
    return 0


def _int_field(obj: Any, name: str) -> int:
    try:
        return int(_field(obj, name) or 0)
    except (TypeError, ValueError):
        return 0


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
