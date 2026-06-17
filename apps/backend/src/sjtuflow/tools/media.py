from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from sjtuflow.runtime import AppContext
from sjtuflow.tools.registry import ToolContext, ToolRegistry
from sjtuflow.tools.transcripts import _metadata_for_file, transcript_root
from sjtuflow.utils.text import sanitize_filename


# Extensions we are willing to treat as local media. We never try to bypass
# DRM, logins, or platform restrictions: only files the user already has on
# disk and pointed us at are processed.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v", ".wmv", ".mpg", ".mpeg"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
STREAM_EXTENSIONS = MEDIA_EXTENSIONS | {".m3u8", ".mpd"}
SJTU_CANVAS_HOST = "oc.sjtu.edu.cn"
MEDIA_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>\\]+?(?:\.m3u8|\.mpd|\.mp4|\.m4v|\.mov|\.webm|\.mp3|\.m4a|\.aac|\.wav)(?:\?[^\s\"'<>\\]*)?",
    re.IGNORECASE,
)
DEFAULT_BROWSER_WAIT_SECONDS = 45

CANVAS_MEDIA_LOGIN_MESSAGE = (
    "SJTU Canvas external_tools media pages usually cannot be fetched with a Canvas API token alone. "
    "The user must keep a browser session logged in. SJTUFlow can reuse its own local browser profile after "
    "the user logs in there, or accept an authorized media stream URL / same-session request headers. "
    "SJTUFlow does not bypass authentication, CAPTCHA, DRM, or course permissions. The video stream is not "
    "saved locally; only the generated transcript is saved."
)


class MediaError(RuntimeError):
    """Raised for recoverable media-processing problems (missing tools, bad input)."""


class _MediaHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.candidates: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        for attr_name in ("src", "data-src", "href"):
            value = attr_map.get(attr_name)
            if value:
                self.candidates.append((tag.lower(), attr_name, unescape(value)))


class _CanvasLinkHTMLParser(HTMLParser):
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
        text = " ".join(part.strip() for part in self._active["text_parts"] if part.strip())
        self.links.append(
            {
                "href": str(self._active["href"]),
                "text": text,
                "title": str(self._active["title"]),
            }
        )
        self._active = None


# --------------------------------------------------------------------------- #
# Segment / transcript data model
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(float(self.start), 3), "end": round(float(self.end), 3), "text": self.text.strip()}


@dataclass
class TranscriptResult:
    """In-memory transcript returned by low-level transcription calls."""

    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None
    source: str = ""
    provider: str = ""

    @property
    def text(self) -> str:
        return "\n".join(segment.text.strip() for segment in self.segments if segment.text.strip())

    @property
    def duration_seconds(self) -> float:
        return round(self.segments[-1].end, 3) if self.segments else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "source": self.source,
            "provider": self.provider,
            "duration_seconds": self.duration_seconds,
            "text": self.text,
            "segments": [segment.to_dict() for segment in self.segments],
        }


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe wrapper
# --------------------------------------------------------------------------- #


def _run(cmd: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # binary missing on this machine
        raise MediaError(f"Required tool not found: {cmd[0]}. Install ffmpeg to enable media processing.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{cmd[0]} timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {exc.returncode}"
        raise MediaError(f"{cmd[0]} failed: {tail}") from exc


def _headers_to_ffmpeg_value(headers: dict[str, str] | None) -> str | None:
    if not headers:
        return None
    lines = [f"{key}: {value}\r\n" for key, value in headers.items() if value is not None]
    return "".join(lines) if lines else None


def _cleanup_temp_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #


def canvas_media_access_hint(url: str) -> dict[str, Any]:
    """Explain what is needed before SJTU Canvas media can be streamed.

    Canvas API tokens cover normal Canvas LMS API routes, but media embedded
    behind ``external_tools`` pages is commonly served through the browser
    login session. This function is intentionally descriptive instead of
    pretending a token can fetch the stream.
    """

    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc).lower()
    path = parsed.path.strip("/")
    is_sjtu_canvas = host == SJTU_CANVAS_HOST
    is_external_tool = is_sjtu_canvas and "/external_tools/" in f"/{path}/"

    next_steps = [
        "前端先检查资料库里是否已有该课程/日期的 transcript。",
        "如果没有 transcript，用户需要在浏览器里保持 SJTU Canvas 登录态并打开对应 external_tools 页面。",
        "前端从登录态页面中解析或转交已授权的媒体 stream_url 给本地后端。",
        "后端用 ffmpeg 流式读取媒体，只生成临时音频并转写；视频本体不保存。",
        "转写完成后 transcript 默认保存到本地资料库，后续问答通过 transcripts.list/read 按需读取。",
    ]

    return {
        "url": url,
        "host": host,
        "is_sjtu_canvas": is_sjtu_canvas,
        "is_external_tool": is_external_tool,
        "canvas_token_supported": False if is_external_tool else None,
        "requires_browser_login": bool(is_external_tool),
        "video_saved_locally": False,
        "transcript_saved_by_default": True,
        "status": "requires_browser_session" if is_external_tool else "unknown_media_url",
        "message": CANVAS_MEDIA_LOGIN_MESSAGE,
        "next_steps": next_steps,
    }


def _validate_stream_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MediaError("stream_url must be an authorized http(s) media URL from the logged-in frontend session.")


def _is_probable_stream_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    suffix = Path(parsed.path).suffix.lower()
    return suffix in STREAM_EXTENSIONS


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "***", parsed.fragment))


def safe_resolution_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload)
    for sensitive_key in ("request_headers", "cookies", "html", "network_urls"):
        safe.pop(sensitive_key, None)
    stream_url = str(safe.pop("stream_url", "") or "")
    safe["stream_url_available"] = bool(stream_url)
    safe["display_url"] = _redact_url(stream_url) if stream_url else str(safe.get("display_url") or "")
    safe["source"] = "<omitted>"
    safe_candidates: list[dict[str, Any]] = []
    for candidate in safe.get("candidates") or []:
        item = dict(candidate)
        candidate_url = str(item.pop("stream_url", "") or "")
        item["stream_url_available"] = bool(candidate_url)
        item["display_url"] = _redact_url(candidate_url) if candidate_url else str(item.get("display_url") or "")
        safe_candidates.append(item)
    safe["candidates"] = safe_candidates
    return safe


def _metadata_without_transcript(payload: dict[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in payload.items() if key != "transcript"}
    if "stream_url" in safe:
        safe["display_url"] = _redact_url(str(safe.pop("stream_url") or ""))
    return safe


def _source_kind(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return "url"
    if "<" in value and ">" in value:
        return "html"
    return "path_or_html"


def _fetch_authorized_html(url: str, request_headers: dict[str, str] | None = None) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise MediaError("page_url must be http(s)")
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "SJTUFlow/0.1",
    }
    headers.update(request_headers or {})
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise MediaError("Media page requires a logged-in browser session or valid same-session headers.") from exc
        raise MediaError(f"Media page fetch failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise MediaError(f"Media page fetch failed: {exc.reason}") from exc


def _browser_profile_dir(app: AppContext) -> Path:
    return app.workspace.assert_safe_write_path(app.workspace.state_dir / "browser" / "canvas")


def _looks_like_canvas_login_page(url: str, html: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc).lower()
    haystack = f"{url}\n{html[:5000]}".lower()
    login_hosts = ("jaccount.sjtu.edu.cn", "login.sjtu.edu.cn", "id.sjtu.edu.cn")
    login_terms = ("login", "jaccount", "统一身份认证", "登录", "password", "captcha")
    return any(host == item or host.endswith(f".{item}") for item in login_hosts) or any(
        term in haystack for term in login_terms
    )


def _cookie_domain_matches(cookie_domain: str, host: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    host = host.lower()
    return host == domain or host.endswith(f".{domain}")


def _cookie_header_for_url(url: str, cookies: list[dict[str, Any]]) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    pairs: list[str] = []
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "")
        cookie_path = str(cookie.get("path") or "/")
        if not name or not _cookie_domain_matches(domain, host):
            continue
        if cookie_path != "/" and not path.startswith(cookie_path):
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _browser_request_headers(
    stream_url: str,
    *,
    referer: str = "",
    user_agent: str = "",
    cookies: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    if user_agent:
        headers["User-Agent"] = user_agent
    cookie_header = _cookie_header_for_url(stream_url, cookies or [])
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _capture_browser_media_page(
    app: AppContext,
    url: str,
    *,
    wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
    headless: bool = False,
) -> dict[str, Any]:
    """Open a local managed browser profile and capture media URLs from a page."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MediaError("Playwright is not installed. Run `uv sync` and then `uv run playwright install chromium`.") from exc

    profile_dir = _browser_profile_dir(app)
    profile_dir.mkdir(parents=True, exist_ok=True)
    wait_ms = max(1, int(wait_seconds)) * 1000
    seen_urls: set[str] = set()
    final_url = url
    html = ""
    user_agent = ""
    cookies: list[dict[str, Any]] = []

    def remember(value: str) -> None:
        if value:
            seen_urls.add(value)

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                accept_downloads=False,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.on("request", lambda request: remember(request.url))
                page.on("response", lambda response: remember(response.url))
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=wait_ms)
                except PlaywrightTimeoutError:
                    pass

                deadline = time.monotonic() + max(1, int(wait_seconds))
                while time.monotonic() < deadline:
                    try:
                        final_url = page.url
                        html = page.content()
                        resource_urls = page.evaluate(
                            "() => performance.getEntriesByType('resource').map((entry) => entry.name).filter(Boolean)"
                        )
                        dom_urls = page.evaluate(
                            """
                            () => Array.from(document.querySelectorAll('video,audio,source,a'))
                              .flatMap((element) => ['src', 'href', 'data-src']
                                .map((name) => element.getAttribute(name))
                                .filter(Boolean))
                            """
                        )
                        for item in (resource_urls or []) + (dom_urls or []):
                            remember(str(item))
                    except PlaywrightError:
                        pass

                    html_candidates = _extract_stream_candidates(html, base_url=final_url)
                    network_candidates = [item for item in seen_urls if _is_probable_stream_url(item)]
                    if html_candidates or network_candidates:
                        break
                    page.wait_for_timeout(1000)

                try:
                    final_url = page.url
                    html = page.content()
                    user_agent = str(page.evaluate("() => navigator.userAgent") or "")
                except PlaywrightError:
                    pass
                try:
                    cookies = [dict(cookie) for cookie in context.cookies()]
                except PlaywrightError:
                    cookies = []
            finally:
                context.close()
    except PlaywrightError as exc:
        detail = str(exc)
        if "Executable doesn't exist" in detail or "playwright install" in detail:
            raise MediaError("Playwright Chromium is not installed. Run `uv run playwright install chromium`.") from exc
        raise MediaError(f"Browser media capture failed: {detail}") from exc

    return {
        "url": url,
        "final_url": final_url,
        "html": html,
        "network_urls": sorted(seen_urls),
        "cookies": cookies,
        "user_agent": user_agent,
        "profile_dir": str(profile_dir),
        "login_required": _looks_like_canvas_login_page(final_url, html),
    }


def _capture_browser_html_page(
    app: AppContext,
    url: str,
    *,
    wait_seconds: int = 20,
    headless: bool = False,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MediaError("Playwright is not installed. Run `uv sync` and then `uv run playwright install chromium`.") from exc

    profile_dir = _browser_profile_dir(app)
    profile_dir.mkdir(parents=True, exist_ok=True)
    wait_ms = max(1, int(wait_seconds)) * 1000
    final_url = url
    html = ""
    title = ""

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                accept_downloads=False,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=wait_ms)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(min(wait_ms, 3000))
                final_url = page.url
                html = page.content()
                title = page.title()
            finally:
                context.close()
    except PlaywrightError as exc:
        detail = str(exc)
        if "Executable doesn't exist" in detail or "playwright install" in detail:
            raise MediaError("Playwright Chromium is not installed. Run `uv run playwright install chromium`.") from exc
        raise MediaError(f"Browser page capture failed: {detail}") from exc

    return {
        "url": url,
        "final_url": final_url,
        "html": html,
        "title": title,
        "profile_dir": str(profile_dir),
        "login_required": _looks_like_canvas_login_page(final_url, html),
    }


def _candidate_record(url: str, *, source: str, tag: str = "", attr: str = "") -> dict[str, Any]:
    parsed = urlparse(url)
    return {
        "stream_url": url,
        "display_url": _redact_url(url),
        "host": parsed.hostname or parsed.netloc,
        "extension": Path(parsed.path).suffix.lower(),
        "source": source,
        "tag": tag,
        "attribute": attr,
    }


def _extract_stream_candidates(html: str, *, base_url: str = "") -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(value: str, *, source: str, tag: str = "", attr: str = "") -> None:
        absolute = urljoin(base_url, unescape(value.strip()))
        if not _is_probable_stream_url(absolute) or absolute in seen:
            return
        seen.add(absolute)
        candidates.append(_candidate_record(absolute, source=source, tag=tag, attr=attr))

    parser = _MediaHTMLParser()
    parser.feed(html)
    for tag, attr, value in parser.candidates:
        add(value, source="html_attr", tag=tag, attr=attr)
    for match in MEDIA_URL_PATTERN.finditer(html):
        add(match.group(0), source="inline_text")
    return candidates


def resolve_media_stream(
    app: AppContext,
    source: str,
    *,
    request_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve a local HTML snippet/page URL/direct stream URL into media stream candidates."""

    value = source.strip()
    if not value:
        raise ValueError("source is required")
    kind = _source_kind(value)
    parsed = urlparse(value)
    html = ""
    base_url = ""
    status = "resolved"

    if _is_probable_stream_url(value):
        candidates = [_candidate_record(value, source="direct_url")]
    elif kind == "url":
        hint = canvas_media_access_hint(value)
        if hint["requires_browser_login"] and not request_headers:
            return {
                **hint,
                "status": "requires_browser_session",
                "source_kind": "canvas_external_tool_page",
                "candidates": [],
            }
        html = _fetch_authorized_html(value, request_headers=request_headers)
        base_url = value
        candidates = _extract_stream_candidates(html, base_url=base_url)
        if not candidates:
            status = "no_stream_found"
    else:
        try:
            path = app.workspace.resolve_read_path(value)
            if path.exists() and path.is_file():
                html = path.read_text(encoding="utf-8", errors="replace")
                base_url = ""
            else:
                html = value
        except ValueError:
            html = value
        candidates = _extract_stream_candidates(html, base_url=base_url)
        if not candidates:
            status = "no_stream_found"

    primary = candidates[0]["stream_url"] if candidates else ""
    return {
        "status": status,
        "source": source,
        "source_kind": kind,
        "stream_url": primary,
        "display_url": _redact_url(primary) if primary else "",
        "candidates": candidates,
        "requires_browser_login": False,
        "video_saved_locally": False,
        "transcript_saved_by_default": True,
    }


def resolve_canvas_page_media(
    app: AppContext,
    url: str,
    *,
    wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
    headless: bool = False,
) -> dict[str, Any]:
    """Resolve a Canvas external_tools media page through SJTUFlow's browser profile.

    The first call may open a Chromium window where the user logs in. The
    profile is stored under the local state directory and reused on later calls.
    """

    value = url.strip()
    if not value:
        raise ValueError("url is required")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MediaError("url must be an http(s) Canvas page URL")

    hint = canvas_media_access_hint(value)
    captured = _capture_browser_media_page(app, value, wait_seconds=wait_seconds, headless=headless)
    final_url = str(captured.get("final_url") or value)
    html = str(captured.get("html") or "")
    cookies = captured.get("cookies") if isinstance(captured.get("cookies"), list) else []
    user_agent = str(captured.get("user_agent") or "")

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(record: dict[str, Any]) -> None:
        stream_url = str(record.get("stream_url") or "")
        if not stream_url or stream_url in seen:
            return
        seen.add(stream_url)
        candidates.append(record)

    for item in captured.get("network_urls") or []:
        stream_url = urljoin(final_url, str(item))
        if _is_probable_stream_url(stream_url):
            add_candidate(_candidate_record(stream_url, source="browser_network"))
    for candidate in _extract_stream_candidates(html, base_url=final_url):
        candidate = dict(candidate)
        candidate["source"] = f"browser_{candidate.get('source') or 'html'}"
        add_candidate(candidate)

    primary = candidates[0]["stream_url"] if candidates else ""
    login_required = bool(captured.get("login_required"))
    if primary:
        status = "resolved"
        message = "Media stream resolved from the SJTUFlow managed browser session."
    elif login_required:
        status = "requires_browser_login"
        message = (
            "SJTUFlow opened its managed browser profile, but the page still appears to require Canvas login. "
            "Log in in that browser window, then run the request again."
        )
    else:
        status = "no_stream_found"
        message = (
            "SJTUFlow loaded the page with its managed browser profile but did not observe a supported media stream. "
            "Open/play the lecture in the browser window and retry, or provide a direct authorized media URL for debugging."
        )

    request_headers = (
        _browser_request_headers(primary, referer=final_url, user_agent=user_agent, cookies=cookies) if primary else {}
    )
    return {
        "status": status,
        "source": value,
        "source_kind": "canvas_external_tool_page" if hint["is_external_tool"] else "browser_page",
        "stream_url": primary,
        "display_url": _redact_url(primary) if primary else "",
        "candidates": candidates,
        "request_headers": request_headers,
        "requires_browser_login": status == "requires_browser_login",
        "browser_session": "sjtuflow-managed",
        "browser_profile": str(captured.get("profile_dir") or _browser_profile_dir(app)),
        "final_url": final_url,
        "message": message,
        "video_saved_locally": False,
        "transcript_saved_by_default": True,
    }


def _course_page_url(app: AppContext, course_id: str, page: str) -> str:
    base = app.config.canvas.base_url.rstrip("/")
    quoted_course_id = quote(str(course_id), safe="")
    if page == "home":
        return f"{base}/courses/{quoted_course_id}"
    if page == "modules":
        return f"{base}/courses/{quoted_course_id}/modules"
    return f"{base}/courses/{quoted_course_id}/{page.lstrip('/')}"


def _external_tool_candidates_from_html(html: str, *, base_url: str) -> list[dict[str, Any]]:
    parser = _CanvasLinkHTMLParser()
    parser.feed(html)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in parser.links:
        href = urljoin(base_url, link["href"])
        parsed = urlparse(href)
        if "/external_tools/" not in parsed.path:
            continue
        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(
            {
                "url": normalized,
                "display_url": _redact_url(normalized),
                "text": link.get("text", ""),
                "title": link.get("title", ""),
            }
        )
    return candidates


def find_canvas_media_pages(
    app: AppContext,
    course_id: str,
    *,
    query: str = "",
    pages: list[str] | None = None,
    wait_seconds: int = 20,
    max_candidates: int = 20,
    headless: bool = False,
) -> dict[str, Any]:
    """Find external_tools candidates in a Canvas course through the managed browser."""

    course_id = str(course_id).strip()
    if not course_id:
        raise ValueError("course_id is required")

    page_names = pages or ["home", "modules"]
    all_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    visited_pages: list[dict[str, Any]] = []
    login_required = False

    query_terms = [term.lower() for term in re.split(r"\s+", query.strip()) if term.strip()]

    for page_name in page_names:
        url = _course_page_url(app, course_id, page_name)
        captured = _capture_browser_html_page(app, url, wait_seconds=wait_seconds, headless=headless)
        final_url = str(captured.get("final_url") or url)
        html = str(captured.get("html") or "")
        page_login_required = bool(captured.get("login_required"))
        login_required = login_required or page_login_required
        candidates = _external_tool_candidates_from_html(html, base_url=final_url)
        visited_pages.append(
            {
                "page": page_name,
                "url": _redact_url(url),
                "final_url": _redact_url(final_url),
                "title": str(captured.get("title") or ""),
                "login_required": page_login_required,
                "candidate_count": len(candidates),
            }
        )
        for candidate in candidates:
            candidate_url = str(candidate.get("url") or "")
            if not candidate_url or candidate_url in seen:
                continue
            text_for_score = " ".join(
                str(candidate.get(key) or "").lower() for key in ("text", "title", "url")
            )
            score = sum(1 for term in query_terms if term in text_for_score)
            item = dict(candidate)
            item["score"] = score
            item["source_page"] = page_name
            seen.add(candidate_url)
            all_candidates.append(item)

    all_candidates.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("title") or item.get("text") or "")))
    all_candidates = all_candidates[: max(1, int(max_candidates))]
    status = "found" if all_candidates else "requires_browser_login" if login_required else "no_candidates"
    message = ""
    if status == "requires_browser_login":
        message = "Log in to Canvas in the SJTUFlow managed browser window, then retry the page search."
    elif status == "no_candidates":
        message = "No Canvas external_tools links were found on the checked course pages."

    return {
        "status": status,
        "course_id": course_id,
        "query": query,
        "visited_pages": visited_pages,
        "candidates": all_candidates,
        "requires_browser_login": status == "requires_browser_login",
        "browser_session": "sjtuflow-managed",
        "browser_profile": str(_browser_profile_dir(app)),
        "message": message,
    }


def probe_media(app: AppContext, path: str) -> dict[str, Any]:
    """Inspect a local media file with ffprobe (duration, codecs, streams)."""

    target = app.workspace.resolve_read_path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-print_format",
            "json",
            str(target),
        ],
        timeout=120,
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}

    fmt = payload.get("format", {}) if isinstance(payload, dict) else {}
    streams = payload.get("streams", []) if isinstance(payload, dict) else []
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]

    duration = fmt.get("duration")
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None

    return {
        "path": str(target),
        "filename": target.name,
        "extension": target.suffix.lower(),
        "size_bytes": target.stat().st_size,
        "duration_seconds": duration_seconds,
        "format_name": fmt.get("format_name"),
        "bit_rate": fmt.get("bit_rate"),
        "has_audio": bool(audio_streams),
        "has_video": bool(video_streams),
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
        "video_codec": video_streams[0].get("codec_name") if video_streams else None,
        "sample_rate": audio_streams[0].get("sample_rate") if audio_streams else None,
        "channels": audio_streams[0].get("channels") if audio_streams else None,
    }


# --------------------------------------------------------------------------- #
# extract_audio
# --------------------------------------------------------------------------- #


def extract_audio(
    app: AppContext,
    path: str,
    out_dir: str | None = None,
    *,
    sample_rate: int = 16000,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Extract a mono 16 kHz WAV from a local video/audio file using ffmpeg.

    The 16 kHz mono WAV format matches what local ASR models (Whisper family)
    expect, so the output feeds directly into :func:`transcribe_media`.
    """

    source = app.workspace.resolve_read_path(path)
    if not source.exists():
        raise FileNotFoundError(str(source))

    if out_dir:
        base = app.workspace.resolve_write_path(out_dir)
    else:
        base = app.workspace.assert_safe_write_path(app.workspace.data_dir / "extracted")
    base.mkdir(parents=True, exist_ok=True)

    target = app.workspace.assert_safe_write_path(base / f"{sanitize_filename(source.stem)}.wav")
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} exists; set overwrite=true to replace it")

    _run(
        [
            "ffmpeg",
            "-y" if overwrite else "-n",
            "-i",
            str(source),
            "-vn",  # drop video
            "-ac",
            "1",  # mono
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )

    app.audit.record("media_extract_audio", {"source": str(source), "output": str(target)})
    return {
        "source": str(source),
        "output": str(target),
        "sample_rate": sample_rate,
        "channels": 1,
        "size_bytes": target.stat().st_size if target.exists() else 0,
    }


# --------------------------------------------------------------------------- #
# transcription
# --------------------------------------------------------------------------- #


def _transcribe_faster_whisper(audio_path: Path, language: str | None, model_size: str) -> TranscriptResult:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise MediaError("faster-whisper is not installed. Run: pip install faster-whisper") from exc

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    raw_segments, info = model.transcribe(str(audio_path), language=language)
    segments = [
        TranscriptSegment(start=segment.start or 0.0, end=segment.end or 0.0, text=segment.text or "")
        for segment in raw_segments
    ]
    detected = getattr(info, "language", None) or language
    return TranscriptResult(segments=segments, language=detected, provider="local-whisper")


def _transcribe_openai_whisper(audio_path: Path, language: str | None, model_size: str) -> TranscriptResult:
    try:
        import whisper
    except ImportError as exc:
        raise MediaError("openai-whisper is not installed. Run: pip install openai-whisper") from exc

    model = whisper.load_model(model_size)
    payload = model.transcribe(str(audio_path), language=language)
    segments = [
        TranscriptSegment(
            start=float(segment.get("start", 0.0)),
            end=float(segment.get("end", 0.0)),
            text=str(segment.get("text", "")),
        )
        for segment in payload.get("segments", [])
    ]
    return TranscriptResult(segments=segments, language=payload.get("language") or language, provider="local-whisper")


def transcribe_media(
    app: AppContext,
    path: str,
    provider: str = "local-whisper",
    language: str | None = None,
    *,
    model_size: str = "base",
    progress: Callable[[float, str], None] | None = None,
) -> TranscriptResult:
    """Transcribe a local media file to timed segments.

    The source may be a media file (video/audio) or an already-extracted WAV.
    For non-WAV input, audio is extracted with ffmpeg first. The result is
    returned in memory for low-level callers. The Web demo should prefer
    :func:`transcribe_media_and_save`, which persists the transcript by default.
    """

    source = app.workspace.resolve_read_path(path)
    if not source.exists():
        raise FileNotFoundError(str(source))

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    report(0.05, f"Preparing {source.name}")

    # Decide which audio file to feed the ASR backend.
    audio_path = source
    if source.suffix.lower() != ".wav":
        report(0.1, "Extracting audio with ffmpeg")
        extracted = extract_audio(app, str(source))
        audio_path = Path(extracted["output"])

    report(0.4, f"Transcribing with {provider}")
    if provider in {"local-whisper", "faster-whisper", "whisper"}:
        result = _transcribe_faster_whisper(audio_path, language, model_size)
    elif provider == "openai-whisper":
        result = _transcribe_openai_whisper(audio_path, language, model_size)
    else:
        raise MediaError(f"Unknown transcription provider: {provider}")

    result.source = str(source)
    report(0.95, "Transcription complete")
    app.audit.record(
        "media_transcribe",
        {"source": str(source), "provider": result.provider, "segments": len(result.segments)},
    )
    return result


def transcribe_media_and_save(
    app: AppContext,
    path: str,
    title: str | None = None,
    provider: str = "local-whisper",
    language: str | None = None,
    description: str = "",
    *,
    overwrite: bool = False,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Transcribe a local media file and save the transcript immediately."""

    result = transcribe_media(app, path, provider=provider, language=language, progress=progress)
    source = result.source or str(app.workspace.resolve_read_path(path))
    transcript_title = title or Path(source).stem
    metadata = save_transcript(
        app,
        transcript_title,
        result.text,
        source=source,
        description=description,
        segments=[segment.to_dict() for segment in result.segments],
        language=result.language,
        overwrite=overwrite,
    )
    metadata["transcript"] = result.to_dict()
    return metadata


def transcribe_stream_to_transcript(
    app: AppContext,
    stream_url: str,
    title: str,
    provider: str = "local-whisper",
    language: str | None = None,
    description: str = "",
    *,
    overwrite: bool = False,
    request_headers: dict[str, str] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Stream an authorized media URL into a temp WAV, transcribe it, and save the transcript.

    The source video itself is never persisted. Only a temporary audio file is
    used during the transcription step.
    """

    _validate_stream_url(stream_url)

    cache_dir = app.workspace.assert_safe_write_path(app.workspace.state_dir / "cache" / "media-streams")
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(title).lower().replace(" ", "-") or "stream"
    temp_audio = app.workspace.assert_safe_write_path(
        cache_dir / f"{safe_title}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{time.time_ns()}.wav"
    )

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    report(0.05, "Preparing authorized media stream")
    headers_value = _headers_to_ffmpeg_value(request_headers)
    cmd = [
        "ffmpeg",
        "-y",
    ]
    if headers_value:
        cmd.extend(["-headers", headers_value])
    cmd.extend(
        [
            "-i",
            stream_url,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(temp_audio),
        ]
    )

    try:
        report(0.15, "Reading stream and extracting audio")
        _run(cmd, timeout=3600)
        report(0.45, "Transcribing stream audio")
        result = transcribe_media(
            app,
            str(temp_audio),
            provider=provider,
            language=language,
            progress=lambda fraction, message: report(0.45 + fraction * 0.45, message),
        )
        redacted_stream_url = _redact_url(stream_url)
        result.source = redacted_stream_url
        report(0.95, "Saving transcript")
        metadata = save_transcript(
            app,
            title,
            result.text,
            source=redacted_stream_url,
            description=description,
            segments=[segment.to_dict() for segment in result.segments],
            language=result.language,
            overwrite=overwrite,
        )
        metadata["transcript"] = result.to_dict()
        metadata["display_url"] = _redact_url(stream_url)
        metadata["video_saved_locally"] = False
        app.audit.record(
            "media_transcribe_stream",
            {
                "source": redacted_stream_url,
                "title": title,
                "provider": result.provider,
                "segments": len(result.segments),
            },
        )
        report(1.0, "Done")
        return metadata
    finally:
        _cleanup_temp_file(temp_audio)


def transcribe_resolved_media_source(
    app: AppContext,
    source: str,
    title: str,
    provider: str = "local-whisper",
    language: str | None = None,
    description: str = "",
    *,
    overwrite: bool = False,
    request_headers: dict[str, str] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Resolve a page/snippet/direct URL, then stream-transcribe and save the transcript."""

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    report(0.02, "Resolving media source")
    resolved = resolve_media_stream(app, source, request_headers=request_headers)
    stream_url = resolved.get("stream_url") or ""
    if not stream_url:
        if resolved.get("status") == "requires_browser_session":
            raise MediaError(CANVAS_MEDIA_LOGIN_MESSAGE)
        raise MediaError("No media stream URL found in the provided source.")

    result = transcribe_stream_to_transcript(
        app,
        stream_url,
        title,
        provider=provider,
        language=language,
        description=description,
        overwrite=overwrite,
        request_headers=request_headers,
        progress=lambda fraction, message: report(0.05 + fraction * 0.95, message),
    )
    result["resolved_media"] = {
        "source_kind": resolved.get("source_kind"),
        "display_url": resolved.get("display_url"),
        "candidate_count": len(resolved.get("candidates") or []),
    }
    return result


def transcribe_canvas_page_media(
    app: AppContext,
    url: str,
    title: str,
    provider: str = "local-whisper",
    language: str | None = None,
    description: str = "",
    *,
    overwrite: bool = False,
    wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
    headless: bool = False,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Resolve a Canvas media page with the managed browser, then transcribe and save."""

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    report(0.02, "Resolving Canvas media page in managed browser")
    resolved = resolve_canvas_page_media(app, url, wait_seconds=wait_seconds, headless=headless)
    stream_url = str(resolved.get("stream_url") or "")
    if not stream_url:
        message = str(resolved.get("message") or "No media stream URL found in the Canvas page.")
        raise MediaError(message)

    result = transcribe_stream_to_transcript(
        app,
        stream_url,
        title,
        provider=provider,
        language=language,
        description=description,
        overwrite=overwrite,
        request_headers=resolved.get("request_headers") if isinstance(resolved.get("request_headers"), dict) else None,
        progress=lambda fraction, message: report(0.05 + fraction * 0.95, message),
    )
    result["resolved_media"] = {
        "source_kind": resolved.get("source_kind"),
        "display_url": resolved.get("display_url"),
        "candidate_count": len(resolved.get("candidates") or []),
        "browser_session": resolved.get("browser_session"),
        "final_url": resolved.get("final_url"),
    }
    return result


# --------------------------------------------------------------------------- #
# save transcript (JSON segments + Markdown)
# --------------------------------------------------------------------------- #


def _segments_to_markdown(title: str, source: str, description: str, segments: list[dict[str, Any]]) -> str:
    def stamp(seconds: float) -> str:
        seconds = int(seconds)
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"

    lines = [
        "---",
        f"title: {title}",
        f"source: {source}",
        f"description: {description}",
        f"saved_at: {datetime.now(timezone.utc).isoformat()}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    if description:
        lines += [description, ""]
    for segment in segments:
        start = stamp(segment.get("start", 0.0))
        end = stamp(segment.get("end", 0.0))
        text = str(segment.get("text", "")).strip()
        lines.append(f"**[{start} - {end}]** {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_transcript(
    app: AppContext,
    title: str,
    content: str = "",
    source: str = "",
    description: str = "",
    *,
    segments: list[dict[str, Any]] | None = None,
    language: str | None = None,
    duration_seconds: float | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Persist a transcript to the local library as both JSON and Markdown.

    The JSON keeps ``segments`` (each with ``start``/``end``/``text``) so it can
    be re-rendered or searched; the Markdown is for reading. The JSON is the
    canonical record and is what transcript metadata listing keys off.
    """

    root = transcript_root(app)
    slug = sanitize_filename(title).lower().replace(" ", "-") or "transcript"

    # Normalize segments. If only plain text was provided, store it as one segment.
    normalized: list[dict[str, Any]] = []
    if segments:
        for segment in segments:
            normalized.append(
                {
                    "start": round(float(segment.get("start", 0.0)), 3),
                    "end": round(float(segment.get("end", 0.0)), 3),
                    "text": str(segment.get("text", "")).strip(),
                }
            )
    elif content.strip():
        normalized.append({"start": 0.0, "end": float(duration_seconds or 0.0), "text": content.strip()})

    full_text = content.strip() or "\n".join(seg["text"] for seg in normalized if seg["text"])
    if duration_seconds is None:
        duration_seconds = normalized[-1]["end"] if normalized else 0.0

    json_target = app.workspace.assert_safe_write_path(root / f"{slug}.json")
    md_target = app.workspace.assert_safe_write_path(root / f"{slug}.md")
    if (json_target.exists() or md_target.exists()) and not overwrite:
        raise FileExistsError(f"{slug} already exists; set overwrite=true to replace it")

    payload = {
        "metadata": {
            "title": title,
            "description": description,
            "source": source,
            "language": language,
            "duration_seconds": duration_seconds,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
        "text": full_text,
        "segments": normalized,
    }
    json_target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_target.write_text(_segments_to_markdown(title, source, description, normalized), encoding="utf-8")

    app.audit.record(
        "media_save_transcript",
        {"title": title, "json": str(json_target), "markdown": str(md_target), "overwrite": overwrite},
    )

    metadata = _metadata_for_file(root, json_target)
    metadata["markdown_path"] = str(md_target)
    metadata["segment_count"] = len(normalized)
    return metadata


# --------------------------------------------------------------------------- #
# tool registration
# --------------------------------------------------------------------------- #


def register_media_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="media.canvas_access_hint",
        description=(
            "Explain why a SJTU Canvas external_tools media page may require a browser login session instead of a Canvas token."
        ),
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Canvas page or media URL."}},
            "required": ["url"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def canvas_access_hint(ctx: ToolContext, url: str):
        return canvas_media_access_hint(url)

    @registry.tool(
        name="media.resolve_stream",
        description=(
            "Debug fallback: resolve a direct media URL, local HTML snippet/file, or authorized media page into stream "
            "URL candidates. Prefer media.resolve_canvas_page for Canvas external_tools pages."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Direct stream URL, local HTML snippet/path, or authorized media page URL.",
                }
            },
            "required": ["source"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def resolve_stream(ctx: ToolContext, source: str):
        return safe_resolution_payload(resolve_media_stream(ctx.app, source))

    @registry.tool(
        name="media.resolve_canvas_page",
        description=(
            "Open a SJTU Canvas external_tools media page with SJTUFlow's managed browser profile and resolve media "
            "stream candidates. If the profile is not logged in, this opens a browser window for the user to log in."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Canvas external_tools page URL or another authorized browser media page.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "default": DEFAULT_BROWSER_WAIT_SECONDS,
                    "description": "How long to wait while the page logs in, loads, or starts the player.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def resolve_canvas_page(ctx: ToolContext, url: str, wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS):
        return safe_resolution_payload(resolve_canvas_page_media(ctx.app, url, wait_seconds=wait_seconds))

    @registry.tool(
        name="media.find_canvas_pages",
        description=(
            "Search a Canvas course home/modules page with SJTUFlow's managed browser profile and return "
            "external_tools candidate pages, such as lecture media pages. Use before resolve/transcribe when "
            "the user names a course but has not supplied the exact Canvas media URL."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string", "description": "Canvas course id."},
                "query": {
                    "type": "string",
                    "default": "",
                    "description": "Optional course/date/topic terms used to rank matching links.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "default": 20,
                    "description": "How long to wait for Canvas course pages to load.",
                },
                "max_candidates": {"type": "integer", "default": 20},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def find_canvas_pages(
        ctx: ToolContext,
        course_id: str,
        query: str = "",
        wait_seconds: int = 20,
        max_candidates: int = 20,
    ):
        return find_canvas_media_pages(
            ctx.app,
            course_id,
            query=query,
            wait_seconds=wait_seconds,
            max_candidates=max_candidates,
        )

    @registry.tool(
        name="media.probe",
        description=(
            "Inspect a local video/audio file (duration, codecs, streams) with ffprobe. "
            "Only processes local files the user provides; does not bypass platform DRM or auth."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Local path to a media file."}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def probe(ctx: ToolContext, path: str):
        return probe_media(ctx.app, path)

    @registry.tool(
        name="media.extract_audio",
        description="Extract a mono 16kHz WAV from a local video/audio file using ffmpeg (local write).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "out_dir": {"type": "string", "description": "Optional output directory under the data dir."},
                "overwrite": {"type": "boolean", "default": True},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def extract(ctx: ToolContext, path: str, out_dir: str | None = None, overwrite: bool = True):
        return extract_audio(ctx.app, path, out_dir, overwrite=overwrite)

    @registry.tool(
        name="media.transcribe",
        description=(
            "Transcribe a local media file into timed segments (start/end/text) with local Whisper. "
            "Returns the transcript in memory; it is NOT saved unless media.save_transcript is called."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "provider": {"type": "string", "default": "local-whisper"},
                "language": {"type": "string", "description": "Optional language hint, e.g. 'zh' or 'en'."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def transcribe(ctx: ToolContext, path: str, provider: str = "local-whisper", language: str | None = None):
        result = transcribe_media(ctx.app, path, provider=provider, language=language)
        return result.to_dict()

    @registry.tool(
        name="media.transcribe_and_save",
        description=(
            "Transcribe a local media file and immediately save the transcript to the local library. "
            "This is the recommended demo workflow."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "title": {"type": "string", "description": "Optional transcript title; defaults to the file stem."},
                "provider": {"type": "string", "default": "local-whisper"},
                "language": {"type": "string", "description": "Optional language hint, e.g. 'zh' or 'en'."},
                "description": {"type": "string", "default": ""},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def transcribe_and_save(
        ctx: ToolContext,
        path: str,
        title: str | None = None,
        provider: str = "local-whisper",
        language: str | None = None,
        description: str = "",
        overwrite: bool = False,
    ):
        result = transcribe_media_and_save(
            ctx.app,
            path,
            title=title,
            provider=provider,
            language=language,
            description=description,
            overwrite=overwrite,
        )
        return _metadata_without_transcript(result)

    @registry.tool(
        name="media.transcribe_stream",
        description=(
            "Stream an authorized browser-session media URL into a temporary WAV, transcribe it, and save the transcript. "
            "The source video is never saved locally."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "stream_url": {"type": "string", "description": "Authorized http(s) media stream URL."},
                "title": {"type": "string"},
                "provider": {"type": "string", "default": "local-whisper"},
                "language": {"type": "string", "description": "Optional language hint, e.g. 'zh' or 'en'."},
                "description": {"type": "string", "default": ""},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["stream_url", "title"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def transcribe_stream(
        ctx: ToolContext,
        stream_url: str,
        title: str,
        provider: str = "local-whisper",
        language: str | None = None,
        description: str = "",
        overwrite: bool = False,
    ):
        result = transcribe_stream_to_transcript(
            ctx.app,
            stream_url,
            title,
            provider=provider,
            language=language,
            description=description,
            overwrite=overwrite,
        )
        return _metadata_without_transcript(result)

    @registry.tool(
        name="media.transcribe_source",
        description=(
            "Debug fallback: resolve a direct stream URL or local HTML snippet/file, then stream-transcribe and save the "
            "transcript. Prefer media.transcribe_canvas_page for Canvas external_tools pages."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Direct stream URL, local HTML snippet/path, or authorized media page URL.",
                },
                "title": {"type": "string"},
                "provider": {"type": "string", "default": "local-whisper"},
                "language": {"type": "string", "description": "Optional language hint, e.g. 'zh' or 'en'."},
                "description": {"type": "string", "default": ""},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["source", "title"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def transcribe_source(
        ctx: ToolContext,
        source: str,
        title: str,
        provider: str = "local-whisper",
        language: str | None = None,
        description: str = "",
        overwrite: bool = False,
    ):
        result = transcribe_resolved_media_source(
            ctx.app,
            source,
            title,
            provider=provider,
            language=language,
            description=description,
            overwrite=overwrite,
        )
        return _metadata_without_transcript(result)

    @registry.tool(
        name="media.transcribe_canvas_page",
        description=(
            "Use SJTUFlow's managed browser profile to open a Canvas media page, resolve the authorized stream, "
            "stream-transcribe it, and save the transcript. The source video is never saved locally."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Canvas external_tools media page URL."},
                "title": {"type": "string"},
                "provider": {"type": "string", "default": "local-whisper"},
                "language": {"type": "string", "description": "Optional language hint, e.g. 'zh' or 'en'."},
                "description": {"type": "string", "default": ""},
                "overwrite": {"type": "boolean", "default": False},
                "wait_seconds": {
                    "type": "integer",
                    "default": DEFAULT_BROWSER_WAIT_SECONDS,
                    "description": "How long to wait while the page logs in, loads, or starts the player.",
                },
            },
            "required": ["url", "title"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def transcribe_canvas_page(
        ctx: ToolContext,
        url: str,
        title: str,
        provider: str = "local-whisper",
        language: str | None = None,
        description: str = "",
        overwrite: bool = False,
        wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
    ):
        result = transcribe_canvas_page_media(
            ctx.app,
            url,
            title,
            provider=provider,
            language=language,
            description=description,
            overwrite=overwrite,
            wait_seconds=wait_seconds,
        )
        return _metadata_without_transcript(result)

    @registry.tool(
        name="media.save_transcript",
        description="Save a transcript to the local library as JSON (segments) and Markdown after confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string", "description": "Full transcript text (optional if segments given)."},
                "source": {"type": "string"},
                "description": {"type": "string"},
                "segments": {
                    "type": "array",
                    "description": "Optional timed segments with start, end, text.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "text": {"type": "string"},
                        },
                    },
                },
                "language": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def save(
        ctx: ToolContext,
        title: str,
        content: str = "",
        source: str = "",
        description: str = "",
        segments: list[dict[str, Any]] | None = None,
        language: str | None = None,
        overwrite: bool = False,
    ):
        return save_transcript(
            ctx.app,
            title,
            content,
            source,
            description,
            segments=segments,
            language=language,
            overwrite=overwrite,
        )
