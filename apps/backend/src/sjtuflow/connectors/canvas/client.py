from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sjtuflow.storage.config import CanvasConfig
from sjtuflow.utils.text import sanitize_filename


class CanvasError(RuntimeError):
    pass


class CanvasAuthError(CanvasError):
    pass


@dataclass
class CanvasCourse:
    id: str
    name: str
    code: str = ""
    term: str = ""
    workflow_state: str = ""


@dataclass
class CanvasAssignment:
    id: str
    course_id: str
    name: str
    due_at: str = ""
    html_url: str = ""
    description_text: str = ""
    submission_types: list[str] | None = None


@dataclass
class CanvasFile:
    id: str
    course_id: str = ""
    folder_id: str = ""
    display_name: str = ""
    size: int = 0
    updated_at: str = ""
    url: str = ""
    local_path: str = ""


class CanvasClient:
    def __init__(self, config: CanvasConfig) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        token = self.config.resolved_token()
        if not token:
            raise CanvasAuthError(
                f"No Canvas token configured. Set {self.config.access_token_env} or canvas.access_token in config."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SJTUFlow/0.1",
        }

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            prefix = "" if path.startswith("/") else "/"
            url = f"{self.base_url}{prefix}{path}"
        if params:
            query = urllib.parse.urlencode(params, doseq=True)
            separator = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{separator}{query}"
        return url

    def _request(self, path: str, params: dict[str, Any] | None = None, *, raw: bool = False) -> tuple[Any, dict[str, str]]:
        url = self._url(path, params)
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = response.read()
                headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise CanvasAuthError(f"Canvas auth failed with HTTP {exc.code}: {detail}") from exc
            raise CanvasError(f"Canvas request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CanvasError(f"Canvas request failed: {exc.reason}") from exc

        if raw:
            return data, headers
        if not data:
            return None, headers
        try:
            return json.loads(data.decode("utf-8")), headers
        except json.JSONDecodeError as exc:
            raise CanvasError("Canvas returned non-JSON data") from exc

    def _paginate(self, path: str, params: dict[str, Any] | None = None, *, limit: int = 100) -> list[Any]:
        items: list[Any] = []
        next_url: str | None = self._url(path, {"per_page": min(limit, 100), **(params or {})})
        while next_url and len(items) < limit:
            payload, headers = self._request(next_url)
            if isinstance(payload, list):
                items.extend(payload)
            elif payload is not None:
                items.append(payload)
            next_url = self._next_link(headers.get("Link", ""))
        return items[:limit]

    @staticmethod
    def _next_link(link_header: str) -> str | None:
        for part in link_header.split(","):
            section = part.strip()
            if 'rel="next"' not in section:
                continue
            start = section.find("<")
            end = section.find(">")
            if start >= 0 and end > start:
                return section[start + 1 : end]
        return None

    def list_courses(self, *, enrollment_state: str = "active", limit: int = 50) -> list[CanvasCourse]:
        raw_courses = self._paginate(
            "/api/v1/courses",
            {
                "enrollment_state": enrollment_state,
                "include[]": ["term"],
            },
            limit=limit,
        )
        courses: list[CanvasCourse] = []
        for item in raw_courses:
            if not isinstance(item, dict):
                continue
            term = item.get("term") if isinstance(item.get("term"), dict) else {}
            courses.append(
                CanvasCourse(
                    id=str(item.get("id", "")),
                    name=str(item.get("name") or item.get("course_code") or item.get("id")),
                    code=str(item.get("course_code") or ""),
                    term=str(term.get("name") or item.get("term") or ""),
                    workflow_state=str(item.get("workflow_state") or ""),
                )
            )
        return courses

    def list_assignments(self, course_id: str, *, include_past: bool = False, limit: int = 100) -> list[CanvasAssignment]:
        raw_assignments = self._paginate(
            f"/api/v1/courses/{urllib.parse.quote(str(course_id))}/assignments",
            {"include[]": ["submission"]},
            limit=limit,
        )
        assignments: list[CanvasAssignment] = []
        now = datetime.now(timezone.utc)
        for item in raw_assignments:
            if not isinstance(item, dict):
                continue
            due_at = str(item.get("due_at") or "")
            if due_at and not include_past:
                parsed = parse_canvas_time(due_at)
                if parsed and parsed < now:
                    continue
            assignments.append(
                CanvasAssignment(
                    id=str(item.get("id", "")),
                    course_id=str(course_id),
                    name=str(item.get("name") or item.get("id")),
                    due_at=due_at,
                    html_url=str(item.get("html_url") or ""),
                    description_text=strip_html(str(item.get("description") or ""))[:4000],
                    submission_types=list(item.get("submission_types") or []),
                )
            )
        return assignments

    def list_upcoming_assignments(self, *, window_days: int = 14, limit_courses: int = 50) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) + timedelta(days=window_days)
        results: list[dict[str, Any]] = []
        for course in self.list_courses(limit=limit_courses):
            try:
                assignments = self.list_assignments(course.id, include_past=False, limit=50)
            except CanvasError as exc:
                results.append({"course": course.name, "warning": str(exc)})
                continue
            for assignment in assignments:
                due_at = parse_canvas_time(assignment.due_at)
                if due_at and due_at <= cutoff:
                    results.append(
                        {
                            "course_id": course.id,
                            "course": course.name,
                            "assignment_id": assignment.id,
                            "name": assignment.name,
                            "due_at": assignment.due_at,
                            "html_url": assignment.html_url,
                        }
                    )
        results.sort(key=lambda item: item.get("due_at") or "9999")
        return results

    def list_files(self, course_id: str, *, folder_id: str | None = None, limit: int = 100) -> list[CanvasFile]:
        if folder_id:
            path = f"/api/v1/folders/{urllib.parse.quote(str(folder_id))}/files"
        else:
            path = f"/api/v1/courses/{urllib.parse.quote(str(course_id))}/files"
        raw_files = self._paginate(path, {}, limit=limit)
        files: list[CanvasFile] = []
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            files.append(
                CanvasFile(
                    id=str(item.get("id", "")),
                    course_id=str(course_id),
                    folder_id=str(item.get("folder_id") or ""),
                    display_name=str(item.get("display_name") or item.get("filename") or item.get("id")),
                    size=int(item.get("size") or 0),
                    updated_at=str(item.get("updated_at") or item.get("modified_at") or ""),
                    url=str(item.get("url") or ""),
                )
            )
        return files

    def get_file(self, file_id: str) -> CanvasFile:
        item, _ = self._request(f"/api/v1/files/{urllib.parse.quote(str(file_id))}")
        if not isinstance(item, dict):
            raise CanvasError("Canvas file lookup returned unexpected data")
        return CanvasFile(
            id=str(item.get("id", "")),
            folder_id=str(item.get("folder_id") or ""),
            display_name=str(item.get("display_name") or item.get("filename") or item.get("id")),
            size=int(item.get("size") or 0),
            updated_at=str(item.get("updated_at") or item.get("modified_at") or ""),
            url=str(item.get("url") or ""),
        )

    def download_file(self, file_id: str, out_dir: Path) -> CanvasFile:
        file = self.get_file(file_id)
        if not file.url:
            raise CanvasError(f"Canvas file {file_id} has no downloadable url")
        out_dir.mkdir(parents=True, exist_ok=True)
        file_name = sanitize_filename(file.display_name or f"canvas-file-{file_id}")
        target = out_dir / file_name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            target = target.with_name(f"{stem}-{file.id}{suffix}")
        data, _ = self._request(file.url, raw=True)
        if not isinstance(data, bytes):
            raise CanvasError("Canvas download returned unexpected data")
        target.write_bytes(data)
        file.local_path = str(target)
        file.size = len(data)
        return file

    def list_recent_announcements(self, *, since_days: int = 3, limit: int = 20) -> list[dict[str, Any]]:
        courses = self.list_courses(limit=50)
        context_codes = [f"course_{course.id}" for course in courses if course.id]
        if not context_codes:
            return []
        start = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
        raw = self._paginate(
            "/api/v1/announcements",
            {
                "context_codes[]": context_codes,
                "start_date": start,
            },
            limit=limit,
        )
        course_lookup = {course.id: course.name for course in courses}
        announcements: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            context_code = str(item.get("context_code") or "")
            course_id = context_code.replace("course_", "")
            announcements.append(
                {
                    "id": str(item.get("id") or ""),
                    "course_id": course_id,
                    "course": course_lookup.get(course_id, course_id),
                    "title": str(item.get("title") or ""),
                    "posted_at": str(item.get("posted_at") or item.get("created_at") or ""),
                    "message": strip_html(str(item.get("message") or ""))[:1200],
                    "html_url": str(item.get("html_url") or ""),
                }
            )
        return announcements


def parse_canvas_time(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def strip_html(value: str) -> str:
    import html
    import re

    value = re.sub(r"<(script|style).*?</\1>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()

