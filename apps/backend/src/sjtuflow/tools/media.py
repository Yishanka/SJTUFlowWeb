from __future__ import annotations

import json
import re
import subprocess
import time
from http.cookiejar import Cookie, CookieJar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
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
SJTU_COURSES_HOST = "courses.sjtu.edu.cn"
SJTU_CANVAS_VIDEO_TOOL_ID = "9487"
MEDIA_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>\\]+?(?:\.m3u8|\.mpd|\.mp4|\.m4v|\.mov|\.webm|\.mp3|\.m4a|\.aac|\.wav)(?:\?[^\s\"'<>\\]*)?",
    re.IGNORECASE,
)
DEFAULT_BROWSER_WAIT_SECONDS = 45
DEFAULT_LOGIN_WAIT_SECONDS = 120
DEFAULT_COURSE_PAGE_WAIT_SECONDS = 20
HTTP_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
MAX_MODEL_SELECTION_ITEMS = 40

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


@dataclass(frozen=True)
class FasterWhisperOptions:
    model: str = "base"
    model_path: str = ""
    download_root: str = ""
    local_files_only: bool = False
    device: str = "cpu"
    compute_type: str = "int8"

    @property
    def model_size_or_path(self) -> str:
        return self.model_path or self.model or "base"


@dataclass
class CanvasLectureStream:
    quality: str
    url: str
    request_headers: dict[str, str] = field(default_factory=dict)

    def to_candidate(self) -> dict[str, Any]:
        parsed = urlparse(self.url)
        return {
            "stream_url": self.url,
            "display_url": _redact_url(self.url),
            "host": parsed.hostname or parsed.netloc,
            "extension": Path(parsed.path).suffix.lower() or ".m3u8",
            "source": "sjtu_lti_video_api",
            "quality": self.quality,
            "request_headers": self.request_headers or None,
        }


@dataclass
class CanvasLectureVideo:
    id: str
    name: str
    category: str
    teacher: str = ""
    classroom: str = ""
    begin_time: str = ""
    end_time: str = ""
    status: str = ""
    streams: list[CanvasLectureStream] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self, index: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "teacher": self.teacher,
            "classroom": self.classroom,
            "begin_time": self.begin_time,
            "end_time": self.end_time,
            "status": self.status,
            "stream_count": len(self.streams),
        }
        if index is not None:
            payload["index"] = index
        return payload


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
        item.pop("request_headers", None)
        candidate_url = str(item.pop("stream_url", "") or "")
        item["stream_url_available"] = bool(candidate_url)
        item["display_url"] = _redact_url(candidate_url) if candidate_url else str(item.get("display_url") or "")
        safe_candidates.append(item)
    safe["candidates"] = safe_candidates
    return safe


def _safe_canvas_request_plan(plan: dict[str, Any]) -> dict[str, Any]:
    safe = dict(plan)
    safe_pages: list[dict[str, Any]] = []
    for page in safe.get("selected_pages") or []:
        item = dict(page)
        resolved = item.get("resolved_media")
        if isinstance(resolved, dict):
            item["resolved_media"] = safe_resolution_payload(resolved)
        safe_pages.append(item)
    safe["selected_pages"] = safe_pages

    safe_streams: list[dict[str, Any]] = []
    for stream in safe.get("selected_streams") or []:
        item = dict(stream)
        item.pop("request_headers", None)
        stream_url = str(item.pop("stream_url", "") or "")
        item["stream_url_available"] = bool(stream_url)
        item["display_url"] = _redact_url(stream_url) if stream_url else str(item.get("display_url") or "")
        safe_streams.append(item)
    safe["selected_streams"] = safe_streams
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


def _extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in HTTP_URL_PATTERN.finditer(text):
        value = match.group(0).rstrip(").,，。;；]")
        if value and value not in seen:
            seen.add(value)
            urls.append(value)
    return urls


def _text_terms(value: str) -> list[str]:
    terms: list[str] = []
    for raw in re.split(r"[\s,，。:：;；/\\|()\[\]{}<>\"'`~!@#$%^&*_+=?-]+", value.lower()):
        term = raw.strip()
        if not term:
            continue
        if term in {"the", "and", "or", "a", "an", "of", "to", "in", "on", "for", "with", "课程", "老师", "视频"}:
            continue
        terms.append(term)
    return terms


def _cjk_bigrams(value: str) -> set[str]:
    chars = [char for char in value if "\u4e00" <= char <= "\u9fff"]
    return {chars[index] + chars[index + 1] for index in range(len(chars) - 1)}


def _score_text_match(text: str, query: str) -> int:
    haystack = text.lower()
    score = 0
    for term in _text_terms(query):
        if term in haystack:
            score += 3
        elif len(term) >= 3 and any(bigram in haystack for bigram in _cjk_bigrams(term)):
            score += sum(1 for bigram in _cjk_bigrams(term) if bigram in haystack)
    return score


def _json_object_from_text(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _model_choice(
    app: AppContext,
    *,
    task: str,
    choices: list[dict[str, Any]],
    system_prompt: str,
    user_prompt: str,
    allow_multiple: bool = False,
) -> dict[str, Any]:
    """Ask the configured LLM to choose from indexed, already-sanitized choices.

    The model only receives metadata and array indexes. Sensitive stream URLs,
    cookies and request headers stay inside the backend process.
    """

    if not choices or app.config.model.provider in {"mock", "dry-run"}:
        return {"strategy": "heuristic", "indexes": [], "reason": "No real model configured."}
    try:
        from sjtuflow.llm.mock_provider import build_provider

        provider = build_provider(app.config.model.provider, app.config.model)
        response = provider.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            [],
        )
    except Exception as exc:  # fall back instead of failing the media job
        return {"strategy": "heuristic", "indexes": [], "reason": f"Model selection unavailable: {exc}"}

    payload = _json_object_from_text(response.content or "")
    raw_indexes = payload.get("indexes")
    if raw_indexes is None and "index" in payload:
        raw_indexes = [payload.get("index")]
    if not isinstance(raw_indexes, list):
        raw_indexes = []
    indexes: list[int] = []
    for item in raw_indexes:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(choices) and index not in indexes:
            indexes.append(index)
    if indexes and not allow_multiple:
        indexes = indexes[:1]
    return {
        "strategy": "llm",
        "task": task,
        "indexes": indexes,
        "reason": str(payload.get("reason") or response.content or "").strip()[:1000],
    }


def _summarize_courses_for_model(courses: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, course in enumerate(courses[:MAX_MODEL_SELECTION_ITEMS]):
        items.append(
            {
                "index": index,
                "id": str(getattr(course, "id", "")),
                "name": str(getattr(course, "name", "")),
                "code": str(getattr(course, "code", "")),
                "term": str(getattr(course, "term", "")),
                "workflow_state": str(getattr(course, "workflow_state", "")),
            }
        )
    return items


def _select_course(app: AppContext, request: str, courses: list[Any]) -> dict[str, Any]:
    if not courses:
        return {"course": None, "selection": {"strategy": "none", "reason": "No Canvas courses returned."}}

    summaries = _summarize_courses_for_model(courses)
    model_choice = _model_choice(
        app,
        task="select_canvas_course",
        choices=summaries,
        system_prompt=(
            "You select the most likely Canvas course for a student media-transcription request. "
            "Return strict JSON only: {\"indexes\":[number],\"reason\":\"short reason\"}. "
            "Use course name, code, term, and the user's natural language."
        ),
        user_prompt=json.dumps({"request": request, "courses": summaries}, ensure_ascii=False),
    )
    if model_choice["indexes"]:
        index = model_choice["indexes"][0]
        return {"course": courses[index], "selection": model_choice}

    scored: list[tuple[int, int, Any]] = []
    for index, course in enumerate(courses):
        text = " ".join(
            [
                str(getattr(course, "name", "")),
                str(getattr(course, "code", "")),
                str(getattr(course, "term", "")),
                str(getattr(course, "id", "")),
            ]
        )
        scored.append((_score_text_match(text, request), -index, course))
    scored.sort(reverse=True)
    best_score, _, best_course = scored[0]
    return {
        "course": best_course,
        "selection": {
            "strategy": "heuristic",
            "indexes": [courses.index(best_course)],
            "reason": "Selected by keyword overlap." if best_score else "No strong course keyword match; using first active course.",
            "score": best_score,
        },
    }


def _select_canvas_pages(app: AppContext, request: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"pages": [], "selection": {"strategy": "none", "reason": "No Canvas media page candidates."}}

    summaries = []
    for index, item in enumerate(candidates[:MAX_MODEL_SELECTION_ITEMS]):
        summaries.append(
            {
                "index": index,
                "title": item.get("title") or "",
                "text": item.get("text") or "",
                "display_url": item.get("display_url") or item.get("url") or "",
                "source_page": item.get("source_page") or "",
                "score": item.get("score") or 0,
            }
        )
    model_choice = _model_choice(
        app,
        task="select_canvas_media_pages",
        choices=summaries,
        system_prompt=(
            "You select Canvas external_tools page(s) that most likely contain the requested lecture videos. "
            "Return strict JSON only: {\"indexes\":[number],\"reason\":\"short reason\"}. "
            "Pick all pages clearly needed by the request, but avoid unrelated tools like homework systems."
        ),
        user_prompt=json.dumps({"request": request, "pages": summaries}, ensure_ascii=False),
        allow_multiple=True,
    )
    indexes = model_choice["indexes"]
    if not indexes:
        sorted_indexes = sorted(
            range(len(candidates)),
            key=lambda idx: (
                int(candidates[idx].get("score") or 0),
                _score_text_match(
                    " ".join(str(candidates[idx].get(key) or "") for key in ("title", "text", "url")),
                    request,
                ),
                -idx,
            ),
            reverse=True,
        )
        indexes = sorted_indexes[:1]
        model_choice = {
            "strategy": "heuristic",
            "indexes": indexes,
            "reason": "Selected by Canvas link score and keyword overlap.",
        }
    pages = [candidates[index] for index in indexes if 0 <= index < len(candidates)]
    return {"pages": pages, "selection": model_choice}


def _canvas_external_tool_candidates_from_api(app: AppContext, course_id: str, request: str) -> dict[str, Any]:
    """Collect ExternalTool module items through the Canvas API token."""

    items = app.canvas.list_external_tool_module_items(course_id, limit_modules=80, limit_items_per_module=100)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        url = str(getattr(item, "html_url", "") or getattr(item, "external_url", "") or "")
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        else:
            normalized = urljoin(app.config.canvas.base_url.rstrip("/") + "/", url.lstrip("/"))
            parsed = urlparse(normalized)
            normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        if "/external_tools/" not in urlparse(normalized).path or normalized in seen:
            continue
        seen.add(normalized)
        text_for_score = " ".join(
            [
                str(getattr(item, "title", "")),
                str(getattr(item, "type", "")),
                normalized,
            ]
        )
        candidates.append(
            {
                "url": normalized,
                "display_url": _redact_url(normalized),
                "text": str(getattr(item, "title", "")),
                "title": str(getattr(item, "title", "")),
                "score": _score_text_match(text_for_score, request),
                "source_page": "canvas_api_modules",
                "module_id": str(getattr(item, "module_id", "")),
                "item_id": str(getattr(item, "id", "")),
                "item_type": str(getattr(item, "type", "")),
            }
        )
    candidates.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("title") or item.get("text") or "")))
    return {
        "status": "found" if candidates else "no_candidates",
        "course_id": str(course_id),
        "query": request,
        "visited_pages": [{"page": "canvas_api_modules", "candidate_count": len(candidates)}],
        "candidates": candidates,
        "requires_browser_login": False,
        "browser_session": "not_used",
        "message": "" if candidates else "No Canvas ExternalTool module items were found through the Canvas API.",
    }


def _stream_preference(candidate: dict[str, Any], request: str) -> int:
    extension = str(candidate.get("extension") or "").lower()
    source = str(candidate.get("source") or "").lower()
    text = " ".join(str(candidate.get(key) or "") for key in ("display_url", "host", "source", "tag", "attribute"))
    score = _score_text_match(text, request)
    if extension == ".m3u8":
        score += 5
    elif extension in {".mp4", ".m4v", ".mov", ".webm"}:
        score += 4
    elif extension in AUDIO_EXTENSIONS:
        score += 3
    elif extension == ".mpd":
        score += 2
    if "network" in source:
        score += 2
    if "html" in source:
        score += 1
    return score


def _select_page_streams(app: AppContext, request: str, resolved: dict[str, Any]) -> dict[str, Any]:
    candidates = list(resolved.get("candidates") or [])
    if not candidates:
        return {"streams": [], "selection": {"strategy": "none", "reason": "No media streams resolved."}}

    summaries: list[dict[str, Any]] = []
    for index, item in enumerate(candidates[:MAX_MODEL_SELECTION_ITEMS]):
        summaries.append(
            {
                "index": index,
                "display_url": item.get("display_url") or _redact_url(str(item.get("stream_url") or "")),
                "host": item.get("host") or "",
                "extension": item.get("extension") or "",
                "source": item.get("source") or "",
                "tag": item.get("tag") or "",
                "attribute": item.get("attribute") or "",
            }
        )
    model_choice = _model_choice(
        app,
        task="select_canvas_media_streams",
        choices=summaries,
        system_prompt=(
            "You select which media stream(s) should be transcribed from a Canvas video page. "
            "Return strict JSON only: {\"indexes\":[number],\"reason\":\"short reason\"}. "
            "Prefer actual lecture video/audio streams, especially m3u8/mp4 captured from browser network. "
            "Pick multiple only if the request explicitly needs multiple lecture videos."
        ),
        user_prompt=json.dumps(
            {
                "request": request,
                "page": {
                    "source": resolved.get("source"),
                    "final_url": resolved.get("final_url"),
                    "display_url": resolved.get("display_url"),
                },
                "streams": summaries,
            },
            ensure_ascii=False,
        ),
        allow_multiple=True,
    )
    indexes = model_choice["indexes"]
    if not indexes:
        indexes = [
            max(
                range(len(candidates)),
                key=lambda idx: (_stream_preference(candidates[idx], request), -idx),
            )
        ]
        model_choice = {
            "strategy": "heuristic",
            "indexes": indexes,
            "reason": "Selected by stream type/source preference.",
        }

    streams: list[dict[str, Any]] = []
    for index in indexes:
        if not 0 <= index < len(candidates):
            continue
        stream = dict(candidates[index])
        stream["selection_index"] = index
        stream["request_headers"] = (
            resolved.get("request_headers") if isinstance(resolved.get("request_headers"), dict) else None
        )
        stream["page_url"] = resolved.get("source") or ""
        stream["final_url"] = resolved.get("final_url") or ""
        streams.append(stream)
    return {"streams": streams, "selection": model_choice}


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


def _browser_storage_state_path(app: AppContext) -> Path:
    return app.workspace.assert_safe_write_path(app.workspace.state_dir / "browser" / "canvas-storage-state.json")


def _cookie_from_storage_item(item: dict[str, Any]) -> Cookie:
    domain = str(item.get("domain") or "")
    path = str(item.get("path") or "/")
    expires = item.get("expires")
    expires_value: int | None
    try:
        expires_float = float(expires)
        expires_value = int(expires_float) if expires_float > 0 else None
    except (TypeError, ValueError):
        expires_value = None
    return Cookie(
        version=0,
        name=str(item.get("name") or ""),
        value=str(item.get("value") or ""),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=bool(domain),
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=bool(path),
        secure=bool(item.get("secure")),
        expires=expires_value,
        discard=expires_value is None,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": bool(item.get("httpOnly"))},
        rfc2109=False,
    )


def _storage_state_cookiejar(app: AppContext) -> CookieJar:
    path = _browser_storage_state_path(app)
    if not path.exists():
        raise MediaError("Canvas login state is missing. Click Prepare Canvas Login and finish login once.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaError("Canvas login state is unreadable. Prepare Canvas login again.") from exc

    jar = CookieJar()
    for item in payload.get("cookies") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        domain = str(item.get("domain") or "")
        if not name or not domain:
            continue
        jar.set_cookie(_cookie_from_storage_item(item))
    if not any(
        _cookie_domain_matches(cookie.domain, SJTU_CANVAS_HOST)
        or _cookie_domain_matches(cookie.domain, "sjtu.edu.cn")
        for cookie in jar
    ):
        raise MediaError("Canvas login state has no Canvas cookies. Prepare Canvas login again.")
    return jar


def _sjtu_video_opener(app: AppContext):
    jar = _storage_state_cookiejar(app)
    opener = build_opener(HTTPCookieProcessor(jar))
    opener.addheaders = [
        (
            "User-Agent",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        ),
        ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
    ]
    return opener, jar


def _looks_like_canvas_login_page(url: str, html: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc).lower()
    login_hosts = ("jaccount.sjtu.edu.cn", "login.sjtu.edu.cn", "id.sjtu.edu.cn")
    if any(host == item or host.endswith(f".{item}") for item in login_hosts):
        return True
    if host == SJTU_CANVAS_HOST and "/login" in parsed.path.lower():
        return True
    haystack = html[:5000].lower()
    has_password = "type=\"password\"" in haystack or "type='password'" in haystack
    has_login_form = "<form" in haystack and any(term in haystack for term in ("jaccount", "统一身份认证", "captcha"))
    return has_password or has_login_form


def _cookie_domain_matches(cookie_domain: str, host: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    host = host.lower()
    return host == domain or host.endswith(f".{domain}")


def _has_canvas_cookie(cookies: list[dict[str, Any]]) -> bool:
    return any(
        _cookie_domain_matches(str(cookie.get("domain") or ""), SJTU_CANVAS_HOST)
        or _cookie_domain_matches(str(cookie.get("domain") or ""), "sjtu.edu.cn")
        for cookie in cookies
    )


def _cookie_domains(cookies: list[dict[str, Any]]) -> list[str]:
    domains = sorted({str(cookie.get("domain") or "") for cookie in cookies if cookie.get("domain")})
    return domains[:20]


def _canvas_login_ready(url: str, html: str, cookies: list[dict[str, Any]]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc).lower()
    on_canvas = host == SJTU_CANVAS_HOST or host.endswith(f".{SJTU_CANVAS_HOST}")
    return on_canvas and not _looks_like_canvas_login_page(url, html) and _has_canvas_cookie(cookies)


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


def _read_text_response(opener: Any, url: str, *, data: dict[str, Any] | None = None, timeout: int = 30) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    body = None
    if data is not None:
        body = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in {401, 403}:
            raise MediaError("Canvas/SJTU video session is not authorized. Prepare Canvas login again.") from exc
        raise MediaError(f"SJTU video request failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise MediaError(f"SJTU video request failed: {exc.reason}") from exc


def _read_json_response(opener: Any, url: str, *, data: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    text = _read_text_response(opener, url, data=data, timeout=timeout)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MediaError("SJTU video API returned non-JSON data. Prepare Canvas login again.") from exc
    return payload if isinstance(payload, dict) else {}


def _html_inputs(html: str) -> dict[str, str]:
    inputs = re.findall(
        r"<input[^>]*name=[\"']([^\"']+)[\"'][^>]*value=[\"']([^\"']*)[\"']",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    return {unescape(name): unescape(value) for name, value in inputs}


def _lti_form_action(html: str) -> str:
    match = re.search(r"<form[^>]*action=[\"']([^\"']+)[\"']", html, re.IGNORECASE | re.DOTALL)
    return unescape(match.group(1)) if match else ""


def _sjtu_video_lti_launch(app: AppContext, course_id: str) -> tuple[str, Any, CookieJar]:
    opener, jar = _sjtu_video_opener(app)
    url = f"{app.config.canvas.base_url.rstrip('/')}/courses/{quote(str(course_id), safe='')}/external_tools/{SJTU_CANVAS_VIDEO_TOOL_ID}"
    html = _read_text_response(opener, url)
    action = _lti_form_action(html)
    form_data = _html_inputs(html)
    if not action or not form_data:
        raise MediaError(
            "SJTU video LTI launch form was not found. The course may not enable the SJTU lecture video tool "
            "or the Canvas login state has expired."
        )
    landing = _read_text_response(opener, action, data=form_data)
    match = re.search(r'var\s+canvasCourseId\s*=\s*"([^"]+)"', landing)
    if not match:
        raise MediaError(
            "SJTU video LTI launch succeeded but canvasCourseId was not found. "
            "Prepare Canvas login again or open the course video tool once in the managed browser."
        )
    return match.group(1), opener, jar


def _cookie_header_from_jar(jar: CookieJar, url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    pairs: list[str] = []
    for cookie in jar:
        if not _cookie_domain_matches(cookie.domain, host):
            continue
        cookie_path = cookie.path or "/"
        if cookie_path != "/" and not path.startswith(cookie_path):
            continue
        pairs.append(f"{cookie.name}={cookie.value}")
    return "; ".join(pairs)


def _extract_sjtu_video_streams(data_object: dict[str, Any], jar: CookieJar) -> list[CanvasLectureStream]:
    streams: list[CanvasLectureStream] = []
    for item in data_object.get("videoPlayResponseVoList") or []:
        if not isinstance(item, dict):
            continue
        for key, quality in (
            ("rtmpUrlHdv", "hdv"),
            ("rtmpUrlFluency", "fluency"),
            ("rtmpUrlDistinct", "distinct"),
            ("rtmpUrlDefault", "default"),
        ):
            url = str(item.get(key) or "")
            if url:
                headers = {"Referer": f"https://{SJTU_COURSES_HOST}/"}
                cookie_header = _cookie_header_from_jar(jar, url)
                if cookie_header:
                    headers["Cookie"] = cookie_header
                streams.append(CanvasLectureStream(quality=quality, url=url, request_headers=headers))
    return streams


def _sjtu_vod_list(app: AppContext, course_id: str, *, page_size: int = 1000) -> tuple[list[dict[str, Any]], Any, CookieJar]:
    canvas_course_id, opener, jar = _sjtu_video_lti_launch(app, course_id)
    payload = {
        "pageIndex": 1,
        "pageSize": page_size,
        "canvasCourseId": canvas_course_id,
    }
    data = _read_json_response(opener, f"https://{SJTU_COURSES_HOST}/lti/vodVideo/findVodVideoList", data=payload)
    if data.get("code") != 200:
        raise MediaError(f"SJTU VOD list API returned error: {data.get('desc') or data.get('code')}")
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    items = body.get("list") if isinstance(body, dict) else []
    return [item for item in items if isinstance(item, dict)], opener, jar


def _sjtu_vod_video_info(opener: Any, video_id: str) -> dict[str, Any]:
    payload = {
        "playTypeHls": "true",
        "id": video_id,
        "isAudit": "true",
    }
    data = _read_json_response(opener, f"https://{SJTU_COURSES_HOST}/lti/vodVideo/getVodVideoInfos", data=payload)
    if data.get("code") != 200:
        raise MediaError(f"SJTU VOD info API returned error: {data.get('desc') or data.get('code')}")
    body = data.get("body")
    return body if isinstance(body, dict) else {}


def _canvas_lecture_from_vod_item(item: dict[str, Any]) -> CanvasLectureVideo:
    return CanvasLectureVideo(
        id=str(item.get("videoId") or item.get("id") or ""),
        name=str(item.get("videoName") or item.get("courName") or item.get("courseName") or ""),
        category="vod",
        teacher=str(item.get("userName") or item.get("teacherName") or ""),
        classroom=str(item.get("classroomName") or ""),
        begin_time=str(item.get("courseBeginTime") or item.get("courBeginTime") or item.get("startTime") or ""),
        end_time=str(item.get("courseEndTime") or item.get("courEndTime") or item.get("endTime") or ""),
        status=str(item.get("videAuditStatus") or item.get("status") or ""),
        raw=item,
    )


def _load_sjtu_course_vods(app: AppContext, course_id: str, *, fetch_stream_info: bool = True) -> list[CanvasLectureVideo]:
    items, opener, jar = _sjtu_vod_list(app, course_id)
    videos: list[CanvasLectureVideo] = []
    for item in items:
        video = _canvas_lecture_from_vod_item(item)
        if fetch_stream_info and video.id:
            try:
                info = _sjtu_vod_video_info(opener, video.id)
                video.streams = _extract_sjtu_video_streams(info, jar)
                video.name = str(info.get("courName") or info.get("videoName") or video.name)
                video.teacher = str(info.get("teacherName") or info.get("userName") or video.teacher)
                video.begin_time = str(info.get("courBeginTime") or info.get("courseBeginTime") or video.begin_time)
                video.end_time = str(info.get("courEndTime") or info.get("courseEndTime") or video.end_time)
            except Exception:
                pass
        videos.append(video)
    return videos


def _select_sjtu_lecture_videos(app: AppContext, request: str, videos: list[CanvasLectureVideo]) -> dict[str, Any]:
    if not videos:
        return {"videos": [], "selection": {"strategy": "none", "reason": "No SJTU VOD videos returned."}}

    summaries = [video.safe_summary(index) for index, video in enumerate(videos[:MAX_MODEL_SELECTION_ITEMS])]
    model_choice = _model_choice(
        app,
        task="select_sjtu_lecture_video",
        choices=summaries,
        system_prompt=(
            "You select SJTU Canvas lecture recording(s) for a student's natural-language request. "
            "Return strict JSON only: {\"indexes\":[number],\"reason\":\"short reason\"}. "
            "Use lecture name, begin/end time, teacher, and the user's date/topic wording. "
            "Pick multiple only if the request clearly asks for multiple recordings."
        ),
        user_prompt=json.dumps({"request": request, "videos": summaries}, ensure_ascii=False),
        allow_multiple=True,
    )
    indexes = model_choice["indexes"]
    if not indexes:
        sorted_indexes = sorted(
            range(len(videos)),
            key=lambda idx: (
                _score_text_match(
                    " ".join(
                        [
                            videos[idx].name,
                            videos[idx].teacher,
                            videos[idx].classroom,
                            videos[idx].begin_time,
                            videos[idx].end_time,
                        ]
                    ),
                    request,
                ),
                -idx,
            ),
            reverse=True,
        )
        indexes = sorted_indexes[:1]
        model_choice = {
            "strategy": "heuristic",
            "indexes": indexes,
            "reason": "Selected by lecture metadata keyword/date overlap.",
        }
    return {"videos": [videos[index] for index in indexes if 0 <= index < len(videos)], "selection": model_choice}


def _select_sjtu_video_stream(video: CanvasLectureVideo) -> dict[str, Any] | None:
    if not video.streams:
        return None
    quality_rank = {"hdv": 0, "distinct": 1, "default": 2, "fluency": 3}
    stream = sorted(video.streams, key=lambda item: quality_rank.get(item.quality, 9))[0]
    candidate = stream.to_candidate()
    candidate["lecture_id"] = video.id
    candidate["lecture_name"] = video.name
    candidate["page_title"] = video.name
    candidate["page_url"] = f"https://{SJTU_COURSES_HOST}/lti/vodVideo/getVodVideoInfos"
    headers = dict(candidate.get("request_headers") or {})
    headers.setdefault("Referer", f"https://{SJTU_COURSES_HOST}/")
    candidate["request_headers"] = headers
    return candidate


def plan_sjtu_video_transcription(
    app: AppContext,
    request: str,
    *,
    max_candidates: int = 20,
    check_login: bool = True,
    _safe: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    value = request.strip()
    if not value:
        raise ValueError("request is required")

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    login_check: dict[str, Any] | None = None
    if check_login:
        report(0.03, "Checking SJTUFlow Canvas login state")
        login_check = check_canvas_browser_login(app, wait_seconds=6, headless=True)
        if not login_check.get("logged_in"):
            payload = {
                "status": "requires_browser_login",
                "request": value,
                "course": None,
                "course_selection": {"strategy": "none", "reason": "Canvas browser profile is not logged in."},
                "video_search": None,
                "video_selection": {"strategy": "none", "reason": str(login_check.get("message") or "")},
                "selected_videos": [],
                "selected_streams": [],
                "login_check": login_check,
                "message": (
                    "请先在媒体页点击“准备 Canvas 登录态”，在 SJTUFlow 托管浏览器中完成 Canvas 登录，"
                    "然后重新提交同一个媒体转写任务。"
                ),
                "video_saved_locally": False,
                "transcript_saved_by_default": True,
            }
            return _safe_canvas_request_plan(payload) if _safe else payload

    try:
        report(0.05, "Listing Canvas courses")
        courses = app.canvas.list_courses(limit=80)
    except Exception as exc:
        raise MediaError("Unable to list Canvas courses. Configure Canvas token first.") from exc
    selected_course = _select_course(app, value, courses)
    course = selected_course.get("course")
    course_selection = selected_course.get("selection") or {}
    if course is None:
        raise MediaError("No Canvas course could be selected from the configured account.")

    report(0.18, "Loading SJTU lecture recordings through Canvas LTI")
    try:
        videos = _load_sjtu_course_vods(app, str(getattr(course, "id", "")), fetch_stream_info=True)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError(f"Unable to load SJTU lecture recordings: {exc}") from exc

    videos = videos[: max(1, int(max_candidates))]
    video_selection_result = _select_sjtu_lecture_videos(app, value, videos)
    selected_videos = video_selection_result.get("videos") or []
    selected_streams: list[dict[str, Any]] = []
    for video in selected_videos:
        stream = _select_sjtu_video_stream(video)
        if stream:
            selected_streams.append(stream)

    if selected_streams:
        status = "ready"
        message = f"Selected {len(selected_streams)} SJTU lecture recording stream(s)."
    elif selected_videos:
        status = "no_stream_found"
        message = "SJTU lecture recordings were found, but no playable stream URL was returned for the selected recording(s)."
    elif videos:
        status = "no_candidates"
        message = "SJTU lecture recordings were found, but none matched the request."
    else:
        status = "no_candidates"
        message = "No SJTU lecture recordings were returned for the selected Canvas course."

    payload = {
        "status": status,
        "request": value,
        "course": getattr(course, "__dict__", None),
        "course_selection": course_selection,
        "video_search": {
            "source": "sjtu_lti_vod",
            "tool_url": f"{app.config.canvas.base_url.rstrip('/')}/courses/{getattr(course, 'id', '')}/external_tools/{SJTU_CANVAS_VIDEO_TOOL_ID}",
            "candidate_count": len(videos),
            "candidates": [video.safe_summary(index) for index, video in enumerate(videos)],
        },
        "video_selection": video_selection_result.get("selection") or {},
        "selected_videos": [video.safe_summary() for video in selected_videos],
        "selected_streams": selected_streams,
        "login_check": login_check,
        "message": message,
        "video_saved_locally": False,
        "transcript_saved_by_default": True,
    }
    return _safe_canvas_request_plan(payload) if _safe else payload


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


def check_canvas_browser_login(
    app: AppContext,
    *,
    url: str | None = None,
    wait_seconds: int = 6,
    headless: bool = True,
) -> dict[str, Any]:
    """Check whether the managed browser profile already has Canvas login state.

    This never asks the user to log in. It opens the persistent profile briefly,
    verifies Canvas cookies plus page state, then closes the context.
    """

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MediaError("Playwright is not installed. Run `uv sync` and then `uv run playwright install chromium`.") from exc

    target_url = url or app.config.canvas.base_url.rstrip("/")
    profile_dir = _browser_profile_dir(app)
    storage_state_path = _browser_storage_state_path(app)
    profile_dir.mkdir(parents=True, exist_ok=True)
    wait_ms = max(1, int(wait_seconds)) * 1000
    final_url = target_url
    html = ""
    cookies: list[dict[str, Any]] = []

    try:
        with sync_playwright() as playwright:
            browser = None
            context = None
            try:
                if storage_state_path.exists():
                    browser = playwright.chromium.launch(headless=headless)
                    context = browser.new_context(storage_state=str(storage_state_path), accept_downloads=False)
                    page = context.new_page()
                else:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=headless,
                        accept_downloads=False,
                    )
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=wait_ms)
                except PlaywrightTimeoutError:
                    pass

                deadline = time.monotonic() + max(1, int(wait_seconds))
                while time.monotonic() < deadline:
                    try:
                        final_url = page.url
                        html = page.content()
                        cookies = [dict(cookie) for cookie in context.cookies()]
                    except PlaywrightError:
                        pass
                    if _canvas_login_ready(final_url, html, cookies) or _looks_like_canvas_login_page(final_url, html):
                        break
                    page.wait_for_timeout(500)
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
    except PlaywrightError as exc:
        detail = str(exc)
        if "Executable doesn't exist" in detail or "playwright install" in detail:
            raise MediaError("Playwright Chromium is not installed. Run `uv run playwright install chromium`.") from exc
        raise MediaError(f"Browser login check failed: {detail}") from exc

    logged_in = _canvas_login_ready(final_url, html, cookies)
    return {
        "status": "logged_in" if logged_in else "login_required",
        "logged_in": logged_in,
        "url": target_url,
        "final_url": final_url,
        "canvas_cookie_seen": _has_canvas_cookie(cookies),
        "cookie_domains": _cookie_domains(cookies),
        "browser_session": "sjtuflow-managed",
        "browser_profile": str(profile_dir),
        "storage_state": str(storage_state_path),
        "storage_state_exists": storage_state_path.exists(),
        "message": (
            "Canvas login state is available in the SJTUFlow browser profile."
            if logged_in
            else "Canvas login state is not available yet. Click Prepare Canvas Login and finish login once."
        ),
    }


def ensure_canvas_browser_login(
    app: AppContext,
    *,
    url: str | None = None,
    wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS,
    headless: bool = False,
) -> dict[str, Any]:
    """Open the managed browser until Canvas login is available, then close it.

    This is a session setup step. It does not scan for videos. Once cookies are
    present in the persistent profile, later media tasks can reuse them.
    """

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MediaError("Playwright is not installed. Run `uv sync` and then `uv run playwright install chromium`.") from exc

    target_url = url or app.config.canvas.base_url.rstrip("/")
    profile_dir = _browser_profile_dir(app)
    storage_state_path = _browser_storage_state_path(app)
    profile_dir.mkdir(parents=True, exist_ok=True)
    wait_ms = max(1, int(wait_seconds)) * 1000
    final_url = target_url
    html = ""
    cookies: list[dict[str, Any]] = []

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
                    page.goto(target_url, wait_until="domcontentloaded", timeout=wait_ms)
                except PlaywrightTimeoutError:
                    pass

                deadline = time.monotonic() + max(1, int(wait_seconds))
                while time.monotonic() < deadline:
                    try:
                        final_url = page.url
                        html = page.content()
                        cookies = [dict(cookie) for cookie in context.cookies()]
                    except PlaywrightError:
                        pass
                    if _canvas_login_ready(final_url, html, cookies):
                        try:
                            context.storage_state(path=str(storage_state_path))
                            cookies = [dict(cookie) for cookie in context.cookies()]
                        except PlaywrightError:
                            pass
                        break
                    page.wait_for_timeout(1000)
            finally:
                context.close()
    except PlaywrightError as exc:
        detail = str(exc)
        if "Executable doesn't exist" in detail or "playwright install" in detail:
            raise MediaError("Playwright Chromium is not installed. Run `uv run playwright install chromium`.") from exc
        raise MediaError(f"Browser login setup failed: {detail}") from exc

    logged_in = _canvas_login_ready(final_url, html, cookies)
    return {
        "status": "logged_in" if logged_in else "login_required",
        "logged_in": logged_in,
        "url": target_url,
        "final_url": final_url,
        "canvas_cookie_seen": _has_canvas_cookie(cookies),
        "cookie_domains": _cookie_domains(cookies),
        "browser_session": "sjtuflow-managed",
        "browser_profile": str(profile_dir),
        "storage_state": str(storage_state_path),
        "storage_state_exists": storage_state_path.exists(),
        "message": "Canvas login is ready." if logged_in else "Please finish Canvas login in the SJTUFlow browser window.",
    }


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
    storage_state_path = _browser_storage_state_path(app)
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
            browser = None
            context = None
            try:
                if storage_state_path.exists():
                    browser = playwright.chromium.launch(headless=headless)
                    context = browser.new_context(storage_state=str(storage_state_path), accept_downloads=False)
                    page = context.new_page()
                else:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=headless,
                        accept_downloads=False,
                    )
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
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
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
        "storage_state": str(storage_state_path),
        "storage_state_exists": storage_state_path.exists(),
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
    storage_state_path = _browser_storage_state_path(app)
    profile_dir.mkdir(parents=True, exist_ok=True)
    wait_ms = max(1, int(wait_seconds)) * 1000
    final_url = url
    html = ""
    title = ""
    text = ""
    frames: list[dict[str, str]] = []

    def capture_state(page) -> None:
        nonlocal final_url, html, title, text, frames
        try:
            final_url = page.url
            html = page.content()
            title = page.title()
        except PlaywrightError:
            pass
        try:
            text = page.locator("body").inner_text(timeout=750)
        except (PlaywrightError, PlaywrightTimeoutError):
            text = ""

        captured_frames: list[dict[str, str]] = []
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            frame_url = str(frame.url or "")
            if not frame_url or frame_url == "about:blank":
                continue
            frame_title = ""
            frame_text = ""
            frame_html = ""
            try:
                frame_title = str(frame.evaluate("() => document.title") or "")
            except (PlaywrightError, PlaywrightTimeoutError):
                pass
            try:
                frame_text = str(frame.locator("body").inner_text(timeout=750) or "")
            except (PlaywrightError, PlaywrightTimeoutError):
                pass
            try:
                frame_html = str(frame.content() or "")
            except (PlaywrightError, PlaywrightTimeoutError):
                pass
            if frame_text or frame_html:
                captured_frames.append(
                    {
                        "url": frame_url,
                        "title": frame_title,
                        "text": frame_text,
                        "html": frame_html,
                    }
                )
        frames = captured_frames

    try:
        with sync_playwright() as playwright:
            browser = None
            context = None
            try:
                if storage_state_path.exists():
                    browser = playwright.chromium.launch(headless=headless)
                    context = browser.new_context(storage_state=str(storage_state_path), accept_downloads=False)
                    page = context.new_page()
                else:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=headless,
                        accept_downloads=False,
                    )
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=wait_ms)
                except PlaywrightTimeoutError:
                    pass
                deadline = time.monotonic() + max(1, int(wait_seconds))
                while time.monotonic() < deadline:
                    capture_state(page)
                    if not _looks_like_canvas_login_page(final_url, html):
                        break
                    # Keep the managed browser open long enough for first-time
                    # Canvas/JAccount login instead of closing after a flash.
                    page.wait_for_timeout(1000)
                if not _looks_like_canvas_login_page(final_url, html):
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except PlaywrightTimeoutError:
                        pass
                    capture_state(page)
                capture_state(page)
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
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
        "text": text,
        "frames": frames,
        "profile_dir": str(profile_dir),
        "storage_state": str(storage_state_path),
        "storage_state_exists": storage_state_path.exists(),
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
    headless: bool = True,
) -> dict[str, Any]:
    """Resolve a Canvas external_tools media page through SJTUFlow's browser profile.

    This expects the managed browser profile to be logged in already. Use
    ``ensure_canvas_browser_login`` as the explicit setup step when needed.
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
    login_wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS,
    max_candidates: int = 20,
    headless: bool = False,
    progress: Callable[[float, str], None] | None = None,
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

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    for page_offset, page_name in enumerate(page_names):
        url = _course_page_url(app, course_id, page_name)
        report(0.05 + page_offset * 0.4, f"Searching Canvas {page_name} page for lecture links")
        captured = _capture_browser_html_page(app, url, wait_seconds=login_wait_seconds, headless=headless)
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
        if page_login_required and not candidates:
            break
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
        message = (
            "请在 SJTUFlow 打开的浏览器窗口完成 Canvas 登录。"
            "登录完成后，重新提交同一个媒体转写任务。"
        )
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


def _faster_whisper_repo_hint(model: str) -> str:
    if "/" in model:
        return model
    return f"Systran/faster-whisper-{model}"


def _asr_options(app: AppContext, model_size: str | None = None) -> FasterWhisperOptions:
    config = app.config.asr
    model_path = str(config.model_path or "").strip()
    if model_path:
        path = Path(model_path).expanduser()
        if not path.exists():
            raise MediaError(
                f"Configured ASR model_path does not exist: {path}. "
                "Set [asr].model_path to a local faster-whisper/CTranslate2 model directory, "
                "or leave it empty and make the Hugging Face model cache available."
            )
        model_path = str(path.resolve())

    download_root = str(config.download_root or "").strip()
    if download_root:
        download_root = str(Path(download_root).expanduser())

    return FasterWhisperOptions(
        model=model_size or config.model or "base",
        model_path=model_path,
        download_root=download_root,
        local_files_only=bool(config.local_files_only),
        device=config.device or "cpu",
        compute_type=config.compute_type or "int8",
    )


def _exception_text(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


def _is_huggingface_model_resolution_error(exc: BaseException) -> bool:
    text = _exception_text(exc)
    return any(
        marker in text
        for marker in (
            "LocalEntryNotFoundError",
            "Temporary failure in name resolution",
            "NameResolutionError",
            "ConnectError",
            "ConnectionError",
            "snapshot_download",
            "huggingface_hub",
        )
    )


def _asr_model_resolution_message(options: FasterWhisperOptions, exc: BaseException) -> str:
    if options.model_path:
        return (
            f"Local ASR model at {options.model_path} could not be loaded by faster-whisper. "
            "Make sure this is a CTranslate2 faster-whisper model directory containing model.bin, "
            "config.json, tokenizer.json and preprocessor_config.json. "
            f"Original error: {_exception_text(exc)}"
        )

    repo_hint = _faster_whisper_repo_hint(options.model)
    cache_hint = options.download_root or "~/.cache/huggingface/hub"
    offline_hint = (
        "asr.local_files_only=true prevents downloads, and the model was not found in the local cache."
        if options.local_files_only
        else "The backend tried to download it, but Hugging Face is unreachable from this environment."
    )
    return (
        f"ASR model '{options.model}' ({repo_hint}) is not available locally. {offline_hint} "
        f"Cache checked: {cache_hint}. "
        "Fix DNS/network access to huggingface.co and pre-download it with: "
        f"uv run python -c \"from faster_whisper import WhisperModel; "
        f"WhisperModel('{options.model}', device='{options.device}', compute_type='{options.compute_type}')\". "
        "If this machine cannot access Hugging Face, copy a faster-whisper/CTranslate2 model directory locally "
        "and set [asr].model_path to that directory."
    )


def _transcribe_faster_whisper(
    audio_path: Path,
    language: str | None,
    options: FasterWhisperOptions | str,
) -> TranscriptResult:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise MediaError("faster-whisper is not installed. Run: pip install faster-whisper") from exc

    if isinstance(options, str):
        options = FasterWhisperOptions(model=options)

    try:
        model = WhisperModel(
            options.model_size_or_path,
            device=options.device,
            compute_type=options.compute_type,
            download_root=options.download_root or None,
            local_files_only=options.local_files_only,
        )
    except ValueError as exc:
        raise MediaError(
            f"Invalid ASR model '{options.model}'. Use a valid faster-whisper model name, "
            "a Hugging Face model id, or set [asr].model_path to a local CTranslate2 model directory."
        ) from exc
    except Exception as exc:
        if _is_huggingface_model_resolution_error(exc):
            raise MediaError(_asr_model_resolution_message(options, exc)) from exc
        raise

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
    model_size: str | None = None,
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
        result = _transcribe_faster_whisper(audio_path, language, _asr_options(app, model_size))
    elif provider == "openai-whisper":
        result = _transcribe_openai_whisper(audio_path, language, model_size or app.config.asr.model or "base")
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
    headless: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Resolve a Canvas media page with the managed browser, then transcribe and save."""

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    report(0.02, "Resolving Canvas media page in managed browser")
    resolved = resolve_canvas_page_media(app, url, wait_seconds=wait_seconds, headless=headless)
    selected = _select_page_streams(app, " ".join([title, description, url]), resolved)
    streams = selected.get("streams") or []
    if not streams:
        message = str(resolved.get("message") or "No media stream URL found in the Canvas page.")
        raise MediaError(message)
    stream = streams[0]
    stream_url = str(stream.get("stream_url") or "")

    result = transcribe_stream_to_transcript(
        app,
        stream_url,
        title,
        provider=provider,
        language=language,
        description=description,
        overwrite=overwrite,
        request_headers=stream.get("request_headers") if isinstance(stream.get("request_headers"), dict) else None,
        progress=lambda fraction, message: report(0.05 + fraction * 0.95, message),
    )
    result["resolved_media"] = {
        "source_kind": resolved.get("source_kind"),
        "display_url": resolved.get("display_url"),
        "candidate_count": len(resolved.get("candidates") or []),
        "browser_session": resolved.get("browser_session"),
        "final_url": resolved.get("final_url"),
        "selected_stream": safe_resolution_payload({"stream_url": stream_url, "candidates": [stream]})["candidates"][0],
        "stream_selection": selected.get("selection"),
    }
    return result


def plan_canvas_media_transcription(
    app: AppContext,
    request: str,
    *,
    wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
    login_wait_seconds: int | None = None,
    max_candidates: int = 20,
    check_login: bool = True,
    page_headless: bool = True,
    _safe: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Resolve a natural-language Canvas media request into selected pages/streams.

    This is the high-level planning step used by the web UI. It can accept an
    explicit Canvas URL or a natural-language course/date/topic description.
    Course and page/stream selection use the configured LLM when available and
    fall back to deterministic metadata scoring for tests and dry-run setups.
    ``login_wait_seconds`` is kept as a deprecated compatibility parameter; it
    no longer opens an interactive browser during transcription planning.
    """

    value = request.strip()
    if not value:
        raise ValueError("request is required")

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    urls = _extract_urls(value)
    canvas_page_urls = [url for url in urls if urlparse(url).hostname and "/external_tools/" in urlparse(url).path]
    if not canvas_page_urls:
        return plan_sjtu_video_transcription(
            app,
            value,
            max_candidates=max_candidates,
            check_login=check_login,
            _safe=_safe,
            progress=progress,
        )

    course: Any | None = None
    course_selection: dict[str, Any] = {"strategy": "explicit_url", "reason": "The request includes a Canvas media URL."}
    page_candidates_result: dict[str, Any] | None = None

    login_check: dict[str, Any] | None = None
    if check_login:
        report(0.03, "Checking SJTUFlow Canvas login profile")
        login_check = check_canvas_browser_login(app, wait_seconds=6, headless=True)
        if not login_check.get("logged_in"):
            payload = {
                "status": "requires_browser_login",
                "request": value,
                "course": None,
                "course_selection": {"strategy": "none", "reason": "Canvas browser profile is not logged in."},
                "page_search": None,
                "page_selection": {"strategy": "none", "reason": str(login_check.get("message") or "")},
                "selected_pages": [],
                "selected_streams": [],
                "login_check": login_check,
                "message": (
                    "请先在媒体页点击“准备 Canvas 登录态”，在 SJTUFlow 托管浏览器中完成 Canvas 登录，"
                    "然后重新提交同一个媒体转写任务。"
                ),
                "video_saved_locally": False,
                "transcript_saved_by_default": True,
            }
            return _safe_canvas_request_plan(payload) if _safe else payload

    if canvas_page_urls:
        selected_pages = [
            {
                "url": urlunparse((*urlparse(url)[:3], "", "", "")),
                "display_url": _redact_url(url),
                "title": "",
                "text": "",
                "source_page": "explicit",
                "score": 0,
            }
            for url in canvas_page_urls
        ]
        page_selection = {
            "strategy": "explicit_url",
            "indexes": list(range(len(selected_pages))),
            "reason": "The user supplied explicit Canvas external_tools URL(s).",
        }
    else:
        try:
            report(0.04, "Listing Canvas courses")
            courses = app.canvas.list_courses(limit=80)
        except Exception as exc:
            raise MediaError(
                "Unable to list Canvas courses. Configure Canvas token first, or paste a Canvas external_tools URL."
            ) from exc
        report(0.08, "Selecting Canvas course")
        selected_course = _select_course(app, value, courses)
        course = selected_course.get("course")
        course_selection = selected_course.get("selection") or {}
        if course is None:
            raise MediaError("No Canvas course could be selected from the configured account.")

        report(0.12, "Finding Canvas ExternalTool links with Canvas API")
        page_candidates_result = _canvas_external_tool_candidates_from_api(app, str(getattr(course, "id", "")), value)
        if not page_candidates_result.get("candidates"):
            page_candidates_result = {
                "status": "no_candidates",
                "course_id": str(getattr(course, "id", "")),
                "query": value,
                "visited_pages": [{"page": "canvas_api_modules", "candidate_count": 0}],
                "candidates": [],
                "requires_browser_login": False,
                "browser_session": "not_used",
                "message": (
                    "Canvas API did not return module ExternalTool links in this compatibility path. "
                    "Natural-language lecture recording requests should use the SJTU LTI/VOD path."
                ),
            }
        if page_candidates_result.get("requires_browser_login"):
            payload = {
                "status": "requires_browser_login",
                "request": value,
                "course": getattr(course, "__dict__", None),
                "course_selection": course_selection,
                "page_search": page_candidates_result,
                "page_selection": {"strategy": "none", "reason": page_candidates_result.get("message") or ""},
                "selected_pages": [],
                "selected_streams": [],
                "login_check": login_check,
                "message": page_candidates_result.get("message") or "Canvas login is required.",
                "video_saved_locally": False,
                "transcript_saved_by_default": True,
            }
            return _safe_canvas_request_plan(payload) if _safe else payload
        page_selection_result = _select_canvas_pages(app, value, page_candidates_result.get("candidates") or [])
        selected_pages = page_selection_result.get("pages") or []
        page_selection = page_selection_result.get("selection") or {}

    resolved_pages: list[dict[str, Any]] = []
    selected_streams: list[dict[str, Any]] = []
    stream_selections: list[dict[str, Any]] = []
    login_required = False
    no_stream_pages = 0

    total_pages = max(1, len(selected_pages))
    for page_index, page in enumerate(selected_pages):
        url = str(page.get("url") or "")
        if not url:
            continue
        report(0.48 + (page_index / total_pages) * 0.35, f"Opening selected Canvas media page {page_index + 1}")
        resolved = resolve_canvas_page_media(app, url, wait_seconds=wait_seconds, headless=page_headless)
        login_required = login_required or bool(resolved.get("requires_browser_login"))
        report(0.55 + (page_index / total_pages) * 0.35, f"Selecting media stream {page_index + 1}")
        page_streams = _select_page_streams(app, value, resolved)
        streams = page_streams.get("streams") or []
        if not streams:
            no_stream_pages += 1
        for stream in streams:
            item = dict(stream)
            item["page_index"] = page_index
            item["page_title"] = page.get("title") or page.get("text") or ""
            selected_streams.append(item)
        stream_selections.append(
            {
                "page_url": url,
                "selection": page_streams.get("selection"),
                "candidate_count": len(resolved.get("candidates") or []),
            }
        )
        resolved_pages.append(
            {
                **page,
                "resolved_media": resolved,
                "stream_selection": page_streams.get("selection"),
            }
        )

    if login_required and not selected_streams:
        status = "requires_browser_login"
        message = "请在 SJTUFlow 打开的浏览器窗口登录 Canvas，然后重试同一个媒体任务。"
    elif selected_streams:
        status = "ready"
        message = f"Selected {len(selected_streams)} media stream(s) for transcription."
    elif no_stream_pages:
        status = "no_stream_found"
        message = "SJTUFlow loaded the selected Canvas page(s), but no supported video/audio stream was observed."
    else:
        status = "no_candidates"
        message = "No Canvas media page or SJTU VOD recording was selected from the request."

    payload = {
        "status": status,
        "request": value,
        "course": getattr(course, "__dict__", None) if course is not None else None,
        "course_selection": course_selection,
        "page_search": page_candidates_result,
        "page_selection": page_selection,
        "selected_pages": resolved_pages,
        "selected_streams": selected_streams,
        "stream_selections": stream_selections,
        "login_check": login_check,
        "message": message,
        "video_saved_locally": False,
        "transcript_saved_by_default": True,
    }
    return _safe_canvas_request_plan(payload) if _safe else payload


def transcribe_canvas_request_media(
    app: AppContext,
    request: str,
    title: str | None = None,
    provider: str = "local-whisper",
    language: str | None = None,
    description: str = "",
    *,
    overwrite: bool = False,
    wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
    login_wait_seconds: int | None = None,
    max_candidates: int = 20,
    check_login: bool = True,
    page_headless: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Plan and transcribe a natural-language Canvas media request."""

    value = request.strip()
    if not value:
        raise ValueError("request is required")

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    report(0.02, "Planning Canvas media transcription")
    plan = plan_canvas_media_transcription(
        app,
        value,
        wait_seconds=wait_seconds,
        login_wait_seconds=login_wait_seconds,
        max_candidates=max_candidates,
        check_login=check_login,
        page_headless=page_headless,
        _safe=False,
        progress=lambda fraction, message: report(0.02 + fraction * 0.18, message),
    )
    if plan.get("status") != "ready":
        raise MediaError(str(plan.get("message") or "Canvas media request is not ready for transcription."))

    selected_streams = list(plan.get("selected_streams") or [])
    if not selected_streams:
        raise MediaError("No selected media streams to transcribe.")

    internal_streams = list(plan.get("selected_streams") or [])
    if not internal_streams:
        raise MediaError("No selected media streams to transcribe.")

    course = plan.get("course") or {}
    course_name = str(course.get("name") or "") if isinstance(course, dict) else ""
    base_title = title or course_name or "Canvas Lecture"
    results: list[dict[str, Any]] = []
    total = len(internal_streams)
    for index, stream in enumerate(internal_streams):
        stream_title = base_title if total == 1 else f"{base_title} {index + 1}"
        page_title = str(stream.get("page_title") or "")
        desc_parts = [description.strip(), f"Request: {value}"]
        if page_title:
            desc_parts.append(f"Canvas page: {page_title}")
        stream_url = str(stream.get("stream_url") or "")
        if not stream_url:
            continue
        start = 0.08 + (index / total) * 0.9
        span = 0.9 / total
        result = transcribe_stream_to_transcript(
            app,
            stream_url,
            stream_title,
            provider=provider,
            language=language,
            description="\n".join(part for part in desc_parts if part),
            overwrite=overwrite,
            request_headers=stream.get("request_headers") if isinstance(stream.get("request_headers"), dict) else None,
            progress=lambda fraction, message, start=start, span=span: report(start + fraction * span, message),
        )
        result["selected_stream"] = safe_resolution_payload({"stream_url": stream_url, "candidates": [stream]})[
            "candidates"
        ][0]
        results.append(result)

    report(1.0, "Done")
    return {
        "status": "succeeded",
        "request": value,
        "title": base_title,
        "transcripts": [_metadata_without_transcript(item) for item in results],
        "count": len(results),
        "plan": _safe_canvas_request_plan(plan),
        "video_saved_locally": False,
        "transcript_saved_by_default": True,
    }


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
        name="media.ensure_canvas_login",
        description=(
            "Open SJTUFlow's managed Canvas browser profile and wait until the user finishes Canvas login. "
            "This only prepares/reuses browser login state; it does not search pages or transcribe media."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional Canvas URL to open; defaults to Canvas home."},
                "wait_seconds": {"type": "integer", "default": DEFAULT_LOGIN_WAIT_SECONDS},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def ensure_canvas_login(ctx: ToolContext, url: str | None = None, wait_seconds: int = DEFAULT_LOGIN_WAIT_SECONDS):
        return ensure_canvas_browser_login(ctx.app, url=url, wait_seconds=wait_seconds)

    @registry.tool(
        name="media.check_canvas_login",
        description=(
            "Check whether SJTUFlow's managed Canvas browser profile is already logged in. "
            "This is non-interactive and never opens a visible login window."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional Canvas URL to check; defaults to Canvas home."},
                "wait_seconds": {"type": "integer", "default": 6},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def check_canvas_login(ctx: ToolContext, url: str | None = None, wait_seconds: int = 6):
        return check_canvas_browser_login(ctx.app, url=url, wait_seconds=wait_seconds)

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
            "stream candidates. Use media.ensure_canvas_login first if the profile is not logged in."
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
                    "description": "Maximum time to wait while the final media page loads or starts the player.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def resolve_canvas_page(ctx: ToolContext, url: str, wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS):
        return safe_resolution_payload(resolve_canvas_page_media(ctx.app, url, wait_seconds=wait_seconds, headless=True))

    @registry.tool(
        name="media.find_canvas_pages",
        description=(
            "Debug fallback: search Canvas course home/modules pages with SJTUFlow's managed browser profile and return "
            "external_tools candidate pages. Prefer Canvas API module tools and media.plan_canvas_request for normal "
            "natural-language media transcription."
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
        name="media.plan_canvas_request",
        description=(
            "Plan a Canvas media transcription from natural language or explicit external_tools URLs. "
            "Natural-language requests use Canvas courses plus the SJTU lecture-video LTI/VOD API; explicit URLs use "
            "the managed browser compatibility path. "
            "Returns only sanitized metadata; no signed stream URLs or cookies are exposed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "Natural-language course/date/topic request or Canvas external_tools URL.",
                },
                "wait_seconds": {"type": "integer", "default": DEFAULT_BROWSER_WAIT_SECONDS},
                "max_candidates": {"type": "integer", "default": 20},
            },
            "required": ["request"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def plan_canvas_request(
        ctx: ToolContext,
        request: str,
        wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
        max_candidates: int = 20,
    ):
        return plan_canvas_media_transcription(
            ctx.app,
            request,
            wait_seconds=wait_seconds,
            max_candidates=max_candidates,
            check_login=True,
            page_headless=True,
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
                    "description": "Maximum time to wait while the final media page loads or starts the player.",
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
        name="media.transcribe_canvas_request",
        description=(
            "Transcribe Canvas lecture media from a natural-language course/date/topic request or explicit external_tools "
            "URL. The tool uses Canvas token for course discovery and SJTUFlow's managed browser for the media page, "
            "then saves transcript(s) by default without saving source video. Run media.ensure_canvas_login first if needed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "Natural-language course/date/topic request or Canvas external_tools URL.",
                },
                "title": {"type": "string", "description": "Optional base transcript title."},
                "provider": {"type": "string", "default": "local-whisper"},
                "language": {"type": "string", "description": "Optional language hint, e.g. 'zh' or 'en'."},
                "description": {"type": "string", "default": ""},
                "overwrite": {"type": "boolean", "default": False},
                "wait_seconds": {"type": "integer", "default": DEFAULT_BROWSER_WAIT_SECONDS},
                "max_candidates": {"type": "integer", "default": 20},
            },
            "required": ["request"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def transcribe_canvas_request(
        ctx: ToolContext,
        request: str,
        title: str | None = None,
        provider: str = "local-whisper",
        language: str | None = None,
        description: str = "",
        overwrite: bool = False,
        wait_seconds: int = DEFAULT_BROWSER_WAIT_SECONDS,
        max_candidates: int = 20,
    ):
        result = transcribe_canvas_request_media(
            ctx.app,
            request,
            title=title,
            provider=provider,
            language=language,
            description=description,
            overwrite=overwrite,
            wait_seconds=wait_seconds,
            max_candidates=max_candidates,
            check_login=True,
            page_headless=True,
        )
        return result

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
