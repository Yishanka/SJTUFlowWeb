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
from urllib.parse import urljoin, urlparse, urlunparse
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

CANVAS_MEDIA_LOGIN_MESSAGE = (
    "SJTU Canvas external_tools media pages usually cannot be fetched with a Canvas API token alone. "
    "The user must keep a browser session logged in and let the frontend provide an authorized media "
    "stream URL or same-session request headers. SJTUFlow does not bypass authentication, CAPTCHA, DRM, "
    "or course permissions. The video stream is not saved locally; only the generated transcript is saved."
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
            "Resolve a direct media URL, local HTML snippet/file, or authorized media page into stream URL candidates. "
            "Canvas external_tools pages still require browser session headers or pasted logged-in HTML."
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
            "Resolve a direct stream URL or logged-in HTML snippet/file, then stream-transcribe and save the transcript. "
            "Use this when the user has provided a pasted Canvas video element or an authorized media URL."
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
