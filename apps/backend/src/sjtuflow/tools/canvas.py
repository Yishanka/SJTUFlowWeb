from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from sjtuflow.tools.registry import ToolContext, ToolRegistry


def _dataclass_list(items):
    return [item.__dict__ for item in items]


STATUS_CLASS_PATTERN = re.compile(
    r"(?:^|[-_])(?:tag|badge|status|state|danger|success|warning|error|primary|info)(?:$|[-_])",
    re.I,
)


class _VisibleTextHTMLParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
    _HIDDEN_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            self._hidden_depth += 1
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS and self._hidden_depth:
            self._hidden_depth -= 1
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth and data.strip():
            self.parts.append(data)


class _PageLinkHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._active: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {name.lower(): value or "" for name, value in attrs}
        href = unescape(attr_map.get("href", "")).strip()
        if href:
            self._active = {
                "href": href,
                "title": unescape(attr_map.get("title", "")).strip(),
                "text_parts": [],
            }

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._active["text_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._active is None:
            return
        text = _normalize_page_text(" ".join(self._active["text_parts"]))
        self.links.append(
            {
                "href": str(self._active["href"]),
                "text": text,
                "title": str(self._active["title"]),
            }
        )
        self._active = None


class _StatusHintHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hints: list[dict[str, str]] = []
        self._stack: list[dict[str, Any]] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _VisibleTextHTMLParser._HIDDEN_TAGS:
            self._hidden_depth += 1
            return
        attr_map = {name.lower(): value or "" for name, value in attrs}
        classes = unescape(attr_map.get("class", "")).strip()
        role = unescape(attr_map.get("role", "")).strip()
        aria = unescape(attr_map.get("aria-label", "")).strip()
        title = unescape(attr_map.get("title", "")).strip()
        self._stack.append(
            {
                "tag": tag,
                "classes": classes,
                "role": role,
                "aria_label": aria,
                "title": title,
                "text_parts": [],
                "status_like": bool(
                    STATUS_CLASS_PATTERN.search(classes)
                    or role in {"alert", "status"}
                    or aria
                    or title
                ),
            }
        )

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        for entry in self._stack:
            entry["text_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VisibleTextHTMLParser._HIDDEN_TAGS and self._hidden_depth:
            self._hidden_depth -= 1
            return
        if not self._stack:
            return
        entry = self._stack.pop()
        if entry["tag"] != tag:
            return
        text = _normalize_page_text(" ".join(entry["text_parts"]))
        fallback = entry["aria_label"] or entry["title"]
        if entry["status_like"] and len(text or fallback) <= 80:
            self.hints.append(
                {
                    "text": text or fallback,
                    "classes": entry["classes"],
                    "role": entry["role"],
                    "aria_label": entry["aria_label"],
                    "title": entry["title"],
                }
            )


def _normalize_page_text(value: str) -> str:
    lines = []
    for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        clean = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _visible_text_from_html(html: str) -> str:
    parser = _VisibleTextHTMLParser()
    try:
        parser.feed(html or "")
    except Exception:
        return ""
    return _normalize_page_text("".join(parser.parts))


def _status_hints_from_html(html: str, *, limit: int = 20) -> list[dict[str, str]]:
    parser = _StatusHintHTMLParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []

    hints: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for hint in parser.hints:
        key = (hint.get("text", ""), hint.get("classes", ""), hint.get("role", ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        hints.append(hint)
        if len(hints) >= limit:
            break
    return hints


def _truncate_text(value: str, max_chars: int) -> tuple[str, bool]:
    limit = max(1000, int(max_chars or 12000))
    text = value[:limit]
    return (f"{text}\n\n[truncated]" if len(value) > limit else text, len(value) > limit)


def _host_matches(host: str, expected: str) -> bool:
    host = host.lower().lstrip(".")
    expected = expected.lower().lstrip(".")
    return host == expected or host.endswith(f".{expected}")


def _redacted_links_from_html(html: str, *, base_url: str, max_links: int = 40) -> list[dict[str, str]]:
    from sjtuflow.tools.media import _redact_url

    parser = _PageLinkHTMLParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []

    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        absolute = urljoin(base_url, link["href"])
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = _redact_url(absolute)
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(
            {
                "url": normalized,
                "text": link.get("text", ""),
                "title": link.get("title", ""),
            }
        )
        if len(links) >= max_links:
            break
    return links


def read_canvas_external_tool_page(
    app,
    url: str,
    *,
    wait_seconds: int = 25,
    max_chars: int = 12000,
    headless: bool = True,
) -> dict[str, Any]:
    """Read visible text from a Canvas external_tools page with the managed browser session."""

    from sjtuflow.tools.media import _browser_profile_dir, _capture_browser_html_page, _redact_url

    value = str(url or "").strip()
    if not value:
        raise ValueError("url is required")
    parsed = urlparse(value)
    canvas_host = urlparse(app.config.canvas.base_url).hostname or "oc.sjtu.edu.cn"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an http(s) Canvas external_tools URL")
    if not _host_matches(parsed.hostname or parsed.netloc, canvas_host) or "/external_tools/" not in parsed.path:
        raise ValueError("url must be a Canvas external_tools URL from the configured Canvas host")

    captured = _capture_browser_html_page(app, value, wait_seconds=wait_seconds, headless=headless)
    final_url = str(captured.get("final_url") or value)
    html = str(captured.get("html") or "")
    title = str(captured.get("title") or "")
    frames = captured.get("frames") if isinstance(captured.get("frames"), list) else []
    login_required = bool(captured.get("login_required"))

    if login_required:
        return {
            "status": "requires_browser_login",
            "url": _redact_url(value),
            "final_url": _redact_url(final_url),
            "title": title,
            "text": "",
            "text_chars": 0,
            "truncated": False,
            "frames": [],
            "links": [],
            "requires_browser_login": True,
            "browser_session": "sjtuflow-managed",
            "browser_profile": str(_browser_profile_dir(app)),
            "storage_state_exists": bool(captured.get("storage_state_exists")),
            "message": "请先在媒体页点击“准备 Canvas 登录态”，完成登录后再读取这个外部工具页面。",
        }

    sections: list[str] = []
    seen_sections: set[str] = set()

    def add_section(label: str, text: str) -> None:
        clean = _normalize_page_text(text)
        if not clean or clean in seen_sections:
            return
        seen_sections.add(clean)
        sections.append(f"{label}\n{clean}" if label else clean)

    add_section("Main page", str(captured.get("text") or "") or _visible_text_from_html(html))
    frame_summaries: list[dict[str, Any]] = []
    links = _redacted_links_from_html(html, base_url=final_url)
    status_hints = _status_hints_from_html(html)
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        frame_url = str(frame.get("url") or "")
        frame_title = str(frame.get("title") or "")
        frame_text = str(frame.get("text") or "") or _visible_text_from_html(str(frame.get("html") or ""))
        frame_html = str(frame.get("html") or "")
        add_section(f"Frame: {_redact_url(frame_url)}", frame_text)
        links.extend(_redacted_links_from_html(frame_html, base_url=frame_url or final_url))
        status_hints.extend(_status_hints_from_html(frame_html))
        frame_summaries.append(
            {
                "url": _redact_url(frame_url),
                "title": frame_title,
                "text_chars": len(_normalize_page_text(frame_text)),
                "has_text": bool(_normalize_page_text(frame_text)),
            }
        )

    combined = _normalize_page_text("\n\n".join(sections))
    text, truncated = _truncate_text(combined, max_chars)
    unique_links: list[dict[str, str]] = []
    seen_links: set[str] = set()
    for link in links:
        link_url = link.get("url", "")
        if not link_url or link_url in seen_links:
            continue
        seen_links.add(link_url)
        unique_links.append(link)
        if len(unique_links) >= 40:
            break

    unique_status_hints: list[dict[str, str]] = []
    seen_status_hints: set[tuple[str, str, str]] = set()
    for hint in status_hints:
        key = (hint.get("text", ""), hint.get("classes", ""), hint.get("role", ""))
        if not key[0] or key in seen_status_hints:
            continue
        seen_status_hints.add(key)
        unique_status_hints.append(hint)
        if len(unique_status_hints) >= 20:
            break

    status = "ok" if combined else "empty"
    message = (
        "Read visible text from the Canvas external tool page."
        if combined
        else "The page opened, but no visible text was captured. It may render after a user action or in a protected widget."
    )
    return {
        "status": status,
        "url": _redact_url(value),
        "final_url": _redact_url(final_url),
        "title": title,
        "text": text,
        "text_chars": len(combined),
        "truncated": truncated,
        "frames": frame_summaries,
        "status_hints": unique_status_hints,
        "links": unique_links,
        "requires_browser_login": False,
        "browser_session": "sjtuflow-managed",
        "browser_profile": str(_browser_profile_dir(app)),
        "storage_state_exists": bool(captured.get("storage_state_exists")),
        "message": message,
    }


def register_canvas_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="canvas.connection_status",
        description="Check whether Canvas configuration has a token and optionally test a lightweight courses request.",
        input_schema={
            "type": "object",
            "properties": {
                "ping": {"type": "boolean", "default": True, "description": "When true, make a small read request."}
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def connection_status(ctx: ToolContext, ping: bool = True):
        config = ctx.app.config.canvas
        status = {
            "base_url": config.base_url,
            "token_configured": bool(config.resolved_token()),
            "token_env": config.access_token_env,
        }
        if not ping:
            return status
        try:
            courses = ctx.app.canvas.list_courses(limit=1)
            status["ok"] = True
            status["sample_courses"] = [course.__dict__ for course in courses]
        except Exception as exc:
            status["ok"] = False
            status["error"] = str(exc)
        return status

    @registry.tool(
        name="canvas.list_courses",
        description="List active Canvas courses for the configured SJTU Canvas account.",
        input_schema={
            "type": "object",
            "properties": {
                "enrollment_state": {"type": "string", "default": "active"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_courses(ctx: ToolContext, enrollment_state: str = "active", limit: int = 50):
        return _dataclass_list(ctx.app.canvas.list_courses(enrollment_state=enrollment_state, limit=limit))

    @registry.tool(
        name="canvas.list_assignments",
        description="List assignments for one Canvas course.",
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "include_past": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_assignments(ctx: ToolContext, course_id: str, include_past: bool = False, limit: int = 100):
        return _dataclass_list(ctx.app.canvas.list_assignments(course_id, include_past=include_past, limit=limit))

    @registry.tool(
        name="canvas.list_upcoming_assignments",
        description="List upcoming assignments across active Canvas courses within a time window.",
        input_schema={
            "type": "object",
            "properties": {
                "window_days": {"type": "integer", "default": 14},
                "limit_courses": {"type": "integer", "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_upcoming_assignments(ctx: ToolContext, window_days: int = 14, limit_courses: int = 50):
        return ctx.app.canvas.list_upcoming_assignments(window_days=window_days, limit_courses=limit_courses)

    @registry.tool(
        name="canvas.list_recent_announcements",
        description="List recent Canvas announcements across active courses.",
        input_schema={
            "type": "object",
            "properties": {
                "since_days": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 20},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_recent_announcements(ctx: ToolContext, since_days: int = 3, limit: int = 20):
        return ctx.app.canvas.list_recent_announcements(since_days=since_days, limit=limit)

    @registry.tool(
        name="canvas.list_files",
        description="List Canvas files for one course or folder.",
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "folder_id": {"type": "string", "description": "Optional folder id."},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_files(ctx: ToolContext, course_id: str, folder_id: str | None = None, limit: int = 100):
        return _dataclass_list(ctx.app.canvas.list_files(course_id, folder_id=folder_id or None, limit=limit))

    @registry.tool(
        name="canvas.list_modules",
        description="List Canvas modules for one course using the Canvas API token.",
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_modules(ctx: ToolContext, course_id: str, limit: int = 100):
        return _dataclass_list(ctx.app.canvas.list_modules(course_id, limit=limit))

    @registry.tool(
        name="canvas.list_module_items",
        description="List module items for one Canvas module using the Canvas API token.",
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "module_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["course_id", "module_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_module_items(ctx: ToolContext, course_id: str, module_id: str, limit: int = 100):
        return _dataclass_list(ctx.app.canvas.list_module_items(course_id, module_id, limit=limit))

    @registry.tool(
        name="canvas.list_course_tabs",
        description=(
            "List Canvas course navigation tabs, including external-tool tabs that may not appear in modules. "
            "Use this to discover attendance/sign-in/考勤/签到 entries in the course left navigation."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_course_tabs(ctx: ToolContext, course_id: str, limit: int = 100):
        return _dataclass_list(ctx.app.canvas.list_course_tabs(course_id, limit=limit))

    @registry.tool(
        name="canvas.list_external_tool_module_items",
        description=(
            "List Canvas module items that look like ExternalTool lecture/media links. "
            "Use this before opening any managed browser window when a user gives a natural-language lecture request."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "limit_modules": {"type": "integer", "default": 50},
                "limit_items_per_module": {"type": "integer", "default": 100},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_external_tool_module_items(
        ctx: ToolContext,
        course_id: str,
        limit_modules: int = 50,
        limit_items_per_module: int = 100,
    ):
        return _dataclass_list(
            ctx.app.canvas.list_external_tool_module_items(
                course_id,
                limit_modules=limit_modules,
                limit_items_per_module=limit_items_per_module,
            )
        )

    @registry.tool(
        name="canvas.read_external_tool_page",
        description=(
            "Open a Canvas external_tools page in SJTUFlow's managed browser session and return visible text. "
            "Use this for attendance,签到,考勤, roll call, or other LTI pages that the Canvas API token cannot read."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Canvas external_tools page URL on the configured Canvas host.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "default": 25,
                    "description": "How long to wait for the external tool page to render.",
                },
                "max_chars": {
                    "type": "integer",
                    "default": 12000,
                    "description": "Maximum number of characters to return from the page text.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def read_external_tool_page(
        ctx: ToolContext,
        url: str,
        wait_seconds: int = 25,
        max_chars: int = 12000,
    ):
        return read_canvas_external_tool_page(
            ctx.app,
            url,
            wait_seconds=wait_seconds,
            max_chars=max_chars,
            headless=True,
        )

    @registry.tool(
        name="canvas.get_file",
        description="Read Canvas file metadata by file id without downloading it.",
        input_schema={
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def get_file(ctx: ToolContext, file_id: str):
        return ctx.app.canvas.get_file(file_id).__dict__

    @registry.tool(
        name="canvas.download_file",
        description="Download a Canvas file into the SJTUFlow data directory after confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "out_dir": {
                    "type": "string",
                    "description": "Optional destination directory. Relative paths resolve from current project.",
                },
                "course_label": {
                    "type": "string",
                    "description": "Optional readable course folder name when out_dir is omitted.",
                },
            },
            "required": ["file_id"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def download_file(ctx: ToolContext, file_id: str, out_dir: str | None = None, course_label: str | None = None):
        if out_dir:
            target_dir = ctx.app.workspace.resolve_write_path(out_dir)
        else:
            target_dir = ctx.app.workspace.canvas_download_dir(course_label)
        file = ctx.app.canvas.download_file(file_id, target_dir)
        return file.__dict__
