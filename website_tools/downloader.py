"""Descarga controlada de páginas HTML y recursos enlazados."""

from dataclasses import dataclass, field, asdict
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.parse import urljoin
from html.parser import HTMLParser
from urllib.request import Request, urlopen
import ssl


@dataclass
class DownloadConfig:
    start_url: str
    output_dir: Path
    max_pages: int = 25
    max_bytes: int = 50_000_000
    timeout: float = 15.0
    verify_tls: bool = True
    user_agent: str = "IA-Browser-WebsiteTools/1.0"


@dataclass
class DownloadEntry:
    url: str
    local_path: str = ""
    status: int | None = None
    bytes: int = 0
    sha256: str = ""
    content_type: str = ""
    error: str = ""


@dataclass
class DownloadResult:
    entries: list[DownloadEntry] = field(default_factory=list)
    total_bytes: int = 0


class _ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() == "img" and values.get("src"):
            self.urls.append(values["src"])
        elif tag.lower() == "script" and values.get("src"):
            self.urls.append(values["src"])
        elif tag.lower() == "link" and values.get("href"):
            relation = values.get("rel", "").lower()
            if any(item in relation for item in ("stylesheet", "icon", "manifest")):
                self.urls.append(values["href"])


def _safe_path(url: str, output_dir: Path) -> Path:
    parts = urlsplit(url)
    relative = (parts.path or "/").lstrip("/")
    if not relative or relative.endswith("/"):
        relative += "index.html"
    if parts.query:
        relative += "_" + sha256(parts.query.encode()).hexdigest()[:10]
    candidate = (output_dir / relative).resolve()
    if output_dir.resolve() not in candidate.parents:
        raise ValueError("La URL produjo una ruta fuera de la carpeta destino")
    return candidate


def download_site(
    config: DownloadConfig,
    urls: list[str],
    *,
    include_resources: bool = True,
    on_entry=None,
) -> DownloadResult:
    result = DownloadResult()
    context = ssl.create_default_context() if config.verify_tls else ssl._create_unverified_context()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    queue = list(dict.fromkeys(urls[:config.max_pages]))
    seen = set()
    while queue and len(seen) < config.max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        entry = DownloadEntry(url=url)
        try:
            request = Request(url, headers={"User-Agent": config.user_agent})
            with urlopen(request, timeout=config.timeout, context=context) as response:
                remaining = config.max_bytes - result.total_bytes
                if remaining <= 0:
                    entry.error = "max-bytes-exceeded"
                    result.entries.append(entry)
                    break
                raw = response.read(min(2_000_000, remaining))
                entry.status = response.status
                entry.content_type = response.headers.get_content_type()
            target = _safe_path(url, config.output_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            entry.local_path = str(target.relative_to(config.output_dir))
            entry.bytes = len(raw)
            entry.sha256 = sha256(raw).hexdigest()
            result.total_bytes += len(raw)
            if include_resources and entry.content_type == "text/html":
                parser = _ResourceParser()
                parser.feed(raw.decode("utf-8", errors="replace"))
                base = url
                for resource in parser.urls:
                    child = urljoin(base, resource)
                    if urlsplit(child).scheme in {"http", "https"} and child not in seen:
                        queue.append(child)
        except (OSError, ValueError) as exc:
            entry.error = str(exc)
        result.entries.append(entry)
        if on_entry:
            on_entry(entry)
    manifest = config.output_dir / "website-manifest.json"
    manifest.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result
