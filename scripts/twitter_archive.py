#!/usr/bin/env python3
"""Normaliza responses de X/Twitter y genera un archivo local para leerlos."""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


HTML_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hoja de contactos</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#171310;
    --panel:#211b16;
    --panel-alt:#2a231c;
    --rule:#3a3128;
    --ink:#f1e9dc;
    --ink-dim:#a89985;
    --accent:#c1442e;
    --accent-dim:#7a3226;
    --focus:#e8b34f;
  }
  *{box-sizing:border-box;}
  html{color-scheme:dark;}
  body{
    margin:0;
    background:
      repeating-linear-gradient(180deg, rgba(0,0,0,0) 0 3px, rgba(0,0,0,.12) 3px 4px),
      var(--bg);
    color:var(--ink);
    font-family:"IBM Plex Sans","Hiragino Kaku Gothic ProN","Noto Sans CJK JP",sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  a{color:var(--focus);text-decoration:none;}
  a:hover{text-decoration:underline;}
  a:focus-visible, input:focus-visible, select:focus-visible, button:focus-visible{
    outline:2px solid var(--focus); outline-offset:2px;
  }

  /* ---------- header / light table ---------- */
  header{
    position:sticky; top:0; z-index:3;
    background:linear-gradient(var(--panel), var(--panel) 85%, transparent);
    border-bottom:1px solid var(--rule);
    padding:22px 20px 18px;
  }
  .header-inner{max-width:720px; margin:0 auto;}
  .title-row{display:flex; align-items:baseline; gap:12px; flex-wrap:wrap;}
  h1{
    font-family:"Big Shoulders Display", sans-serif;
    font-weight:700;
    font-size:clamp(1.9rem, 5vw, 2.6rem);
    letter-spacing:.01em;
    margin:0;
    line-height:1;
  }
  .count{
    font-family:"IBM Plex Mono", monospace;
    font-size:.85rem;
    color:var(--ink-dim);
    border:1px solid var(--rule);
    border-radius:2px;
    padding:3px 8px;
  }
  .subhead{
    margin:6px 0 18px;
    color:var(--ink-dim);
    font-size:.92rem;
    max-width:52ch;
  }
  .toolbar{display:flex; gap:18px; flex-wrap:wrap;}
  .field{display:flex; flex-direction:column; gap:4px; flex:1; min-width:150px;}
  .field label{font-size:.72rem; color:var(--ink-dim);}
  .field input, .field select{
    background:transparent;
    border:none;
    border-bottom:1px solid var(--rule);
    color:var(--ink);
    font-family:inherit;
    font-size:.95rem;
    padding:6px 2px;
  }
  .field select{ appearance:none; cursor:pointer;}
  .field input::placeholder{color:var(--ink-dim);}
  .field input:focus, .field select:focus{border-bottom-color:var(--accent);}

  /* ---------- roll ---------- */
  main{max-width:720px; margin:0 auto; padding:8px 20px 60px;}
  .frame{
    display:flex;
    border-bottom:1px solid var(--rule);
    padding:22px 0;
  }
  .frame:first-child{padding-top:26px;}
  .sprocket-rail{
    flex:0 0 34px;
    display:flex;
    flex-direction:column;
    align-items:center;
    gap:9px;
    padding-top:4px;
  }
  .sprocket-rail span{
    width:7px; height:7px; border-radius:50%;
    background:var(--rule);
    flex:0 0 auto;
  }
  .frame-no{
    font-family:"IBM Plex Mono", monospace;
    font-size:.68rem;
    color:var(--ink-dim);
    writing-mode:vertical-rl;
    transform:rotate(180deg);
    letter-spacing:.05em;
  }
  .card{flex:1; min-width:0;}

  .byline{display:flex; align-items:center; gap:10px;}
  .avatar{
    width:38px; height:38px; border-radius:3px;
    background:var(--panel-alt);
    border:1px solid var(--rule);
    object-fit:cover; flex:0 0 auto;
  }
  .who .name{font-weight:600; font-size:.95rem;}
  .who .meta{font-size:.78rem; color:var(--ink-dim);}
  .who .meta a{color:inherit;}
  .who .meta a:hover{color:var(--ink);}

  .text{
    margin:12px 0 0;
    line-height:1.55;
    font-size:.98rem;
    white-space:pre-wrap;
    overflow-wrap:anywhere;
  }
  .lang-tag{
    display:inline-block;
    font-family:"IBM Plex Mono", monospace;
    font-size:.62rem;
    color:var(--ink-dim);
    border:1px solid var(--rule);
    padding:1px 5px;
    border-radius:2px;
    margin-left:8px;
    vertical-align:2px;
  }

  .contact-grid{
    display:grid;
    grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
    gap:6px;
    margin-top:12px;
  }
  .contact-grid a{display:block; line-height:0;}
  .contact-grid img{
    width:100%; height:170px; object-fit:cover;
    background:var(--panel-alt);
    border:1px solid var(--rule);
  }

  .nested{
    margin-top:14px;
    padding:14px 14px 14px 16px;
    background:var(--panel);
    border-left:2px solid var(--accent-dim);
  }
  .nested .nested-label{
    font-size:.72rem;
    color:var(--accent);
    margin-bottom:8px;
  }
  .nested .byline{gap:8px;}
  .nested .avatar{width:26px; height:26px;}
  .nested .text{font-size:.9rem; margin-top:8px;}
  .nested .missing{font-size:.85rem; color:var(--ink-dim); font-style:italic;}
  .nested .contact-grid img{height:130px;}
  .nested .open{display:inline-block; margin-top:8px; font-size:.82rem;}

  .stamp{
    display:flex; align-items:center; gap:18px;
    margin-top:14px;
    font-family:"IBM Plex Mono", monospace;
    font-size:.78rem;
    color:var(--ink-dim);
  }
  .stamp .stat{display:flex; align-items:center; gap:5px;}
  .stamp svg{width:14px; height:14px; stroke:var(--ink-dim); fill:none; stroke-width:1.6;}
  .stamp .open-link{margin-left:auto;}
  .stamp .rt-badge{color:var(--accent);}

  .empty{
    text-align:center;
    padding:60px 20px;
    color:var(--ink-dim);
  }
  .empty .big{
    font-family:"Big Shoulders Display", sans-serif;
    font-size:2.2rem;
    color:var(--ink);
    display:block;
    margin-bottom:6px;
  }

  @media (max-width:520px){
    .sprocket-rail{flex-basis:22px; gap:6px;}
    .sprocket-rail span{width:5px; height:5px;}
    .frame-no{font-size:.6rem;}
    .contact-grid{grid-template-columns:repeat(auto-fit, minmax(105px,1fr));}
    .contact-grid img{height:120px;}
  }

  @media (prefers-reduced-motion: reduce){
    *{scroll-behavior:auto !important;}
  }
</style>
</head>
<body>
  <header>
    <div class="header-inner">
      <div class="title-row">
        <h1>Hoja de contactos</h1>
        <span class="count" id="count"></span>
      </div>
      <p class="subhead">Cada publicación guardada, revelada como un fotograma. Buscá por texto, autor o idioma.</p>
      <div class="toolbar">
        <div class="field">
          <label for="search">Buscar</label>
          <input id="search" type="search" placeholder="texto, @usuario, idioma…">
        </div>
        <div class="field">
          <label for="author">Autor</label>
          <select id="author"><option value="">Todos</option></select>
        </div>
        <div class="field">
          <label for="sort">Orden</label>
          <select id="sort">
            <option value="newest">Más recientes primero</option>
            <option value="oldest">Más antiguos primero</option>
          </select>
        </div>
      </div>
    </div>
  </header>

  <main id="tweets"></main>

  <script>
    const tweets = __TWEETS__;

    const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[c]));

    const fmtDate = value => {
      if (!value) return 'fecha desconocida';
      const d = new Date(value);
      return d.toLocaleString('es-AR', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' });
    };

    const ICONS = {
      like:  '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.35-9.5-9C.7 7.5 3 4 6.5 4c2 0 3.3 1 5.5 3.2C14.2 5 15.5 4 17.5 4 21 4 23.3 7.5 21.5 11 19 15.65 12 20 12 20z"/></svg>',
      reply: '<svg viewBox="0 0 24 24"><path d="M4 5h16v10H8l-4 4V5z"/></svg>',
      rt:    '<svg viewBox="0 0 24 24"><path d="M6 5h9a3 3 0 0 1 3 3v3M18 19H9a3 3 0 0 1-3-3v-3M3 8l3-3 3 3M21 16l-3 3-3-3"/></svg>',
      view:  '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="2.6"/></svg>'
    };

    const avatarTag = (author, cls) =>
      `<img class="avatar ${cls || ''}" src="${esc(author.avatar_url)}" alt=""
        onerror="this.style.visibility='hidden'">`;

    const mediaGrid = items => (items && items.length)
      ? `<div class="contact-grid">${items.map(item =>
          `<a href="${esc(item.expanded_url || item.url)}" target="_blank" rel="noreferrer">
            <img loading="lazy" src="${esc(item.media_url)}" alt="">
          </a>`).join('')}</div>`
      : '';

    const nestedBlock = (tweet, label) => {
      if (!tweet) return '';
      const author = tweet.author || {};
      const body = tweet.text
        ? `<div class="text">${esc(tweet.text)}</div>`
        : '<div class="missing">El texto no venía incluido en la respuesta original.</div>';
      return `<div class="nested">
        <div class="nested-label">${label}</div>
        <div class="byline">
          ${avatarTag(author)}
          <div class="who">
            <div class="name">${esc(author.name)}</div>
            <div class="meta">@${esc(author.screen_name || 'unknown')}</div>
          </div>
        </div>
        ${body}
        ${mediaGrid(tweet.media)}
        <a class="open" href="${esc(tweet.url)}" target="_blank" rel="noreferrer">Ver publicación original ↗</a>
      </div>`;
    };

    const frame = (tweet, index) => {
      const a = tweet.author;
      const num = String(index + 1).padStart(3, '0');
      return `<article class="frame">
        <div class="sprocket-rail">
          <span></span><span></span><span></span>
          <div class="frame-no">No. ${num}</div>
          <span></span><span></span><span></span>
        </div>
        <div class="card">
          <div class="byline">
            ${avatarTag(a)}
            <div class="who">
              <div class="name">${esc(a.name)}</div>
              <div class="meta">
                <a href="https://x.com/${esc(a.screen_name)}" target="_blank" rel="noreferrer">@${esc(a.screen_name)}</a>
                · ${esc(fmtDate(tweet.created_at))}
                ${tweet.lang && tweet.lang !== 'und' && tweet.lang !== 'zxx' ? `<span class="lang-tag">${esc(tweet.lang)}</span>` : ''}
              </div>
            </div>
          </div>
          <div class="text">${esc(tweet.text)}</div>
          ${!tweet.retweeted ? mediaGrid(tweet.media) : ''}
          ${nestedBlock(tweet.retweeted, tweet.retweeted ? `Repost de @${esc(tweet.retweeted.author.screen_name)}` : '')}
          ${nestedBlock(tweet.quoted, tweet.quoted ? `Cita a @${esc(tweet.quoted.author.screen_name)}` : '')}
          <div class="stamp">
            <span class="stat">${ICONS.like} ${tweet.metrics.likes}</span>
            <span class="stat">${ICONS.reply} ${tweet.metrics.replies}</span>
            <span class="stat ${tweet.metrics.retweets ? 'rt-badge' : ''}">${ICONS.rt} ${tweet.metrics.retweets}</span>
            <span class="stat">${ICONS.view} ${tweet.metrics.views}</span>
            <a class="open-link" href="${esc(tweet.url)}" target="_blank" rel="noreferrer">Abrir en X ↗</a>
          </div>
        </div>
      </article>`;
    };

    const authors = [...new Map(tweets.map(t => [t.author.screen_name, t.author])).values()]
      .sort((x, y) => x.screen_name.localeCompare(y.screen_name));
    const authorSelect = document.getElementById('author');
    authorSelect.innerHTML += authors.map(a =>
      `<option value="${esc(a.screen_name)}">@${esc(a.screen_name)}</option>`).join('');

    function render() {
      const query = document.getElementById('search').value.toLowerCase().trim();
      const selected = authorSelect.value;
      const result = tweets
        .filter(t => (!selected || t.author.screen_name === selected) &&
          (!query || JSON.stringify(t).toLowerCase().includes(query)))
        .sort((x, y) => document.getElementById('sort').value === 'oldest'
          ? x.timestamp - y.timestamp
          : y.timestamp - x.timestamp);

      document.getElementById('count').textContent = `${result.length} / ${tweets.length}`;
      document.getElementById('tweets').innerHTML = result.length
        ? result.map(frame).join('')
        : '<div class="empty"><span class="big">Rollo vacío</span>No hay fotogramas que coincidan con la búsqueda.</div>';
    }

    ['search', 'author', 'sort'].forEach(id =>
      document.getElementById(id).addEventListener('input', render));
    render();
  </script>
</body>
</html>
"""


def _first(mapping: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _user(tweet: dict[str, Any]) -> dict[str, Any]:
    result = tweet.get("core", {}).get("user_results", {}).get("result", {})
    core = result.get("core", {})
    return {
        "name": core.get("name", "Usuario desconocido"),
        "screen_name": core.get("screen_name", "unknown"),
        "id": result.get("rest_id", ""),
        "avatar_url": result.get("avatar", {}).get("image_url", ""),
    }


def _media(legacy: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    entities = legacy.get("extended_entities", legacy.get("entities", {}))
    if not isinstance(entities, dict):
        return result
    items = entities.get("media", [])
    if not isinstance(items, list):
        return result
    for item in items:
        result.append({
            "type": item.get("type", "photo"),
            "media_url": item.get("media_url_https", ""),
            "url": item.get("url", ""),
            "expanded_url": item.get("expanded_url", ""),
            "width": str(item.get("original_info", {}).get("width", "")),
            "height": str(item.get("original_info", {}).get("height", "")),
        })
    return result


def _normalize(tweet: dict[str, Any]) -> dict[str, Any] | None:
    legacy = tweet.get("legacy", {})
    tweet_id = _first(legacy, "id_str", default=tweet.get("rest_id", ""))
    if not tweet_id or "full_text" not in legacy:
        return None
    created_at = legacy.get("created_at", "")
    try:
        timestamp = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").timestamp()
    except ValueError:
        timestamp = 0
    return {
        "id": tweet_id,
        "url": f"https://x.com/{_user(tweet)['screen_name']}/status/{tweet_id}",
        "text": legacy.get("full_text", ""),
        "created_at": created_at,
        "timestamp": timestamp,
        "lang": legacy.get("lang", ""),
        "author": _user(tweet),
        "media": _media(legacy),
        "metrics": {
            "likes": legacy.get("favorite_count", 0),
            "replies": legacy.get("reply_count", 0),
            "retweets": legacy.get("retweet_count", 0),
            "views": tweet.get("views", {}).get("count", "0"),
        },
    }


def _unwrap_related(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = value.get("result", value)
    if isinstance(result, dict) and isinstance(result.get("tweet"), dict):
        return result["tweet"]
    return result if isinstance(result, dict) else {}


def _related_tweet(tweet: dict[str, Any], name: str) -> dict[str, Any]:
    """Busca relaciones en las variantes de payload usadas por X."""
    for container in (tweet, tweet.get("legacy", {})):
        related = _unwrap_related(container.get(name))
        if related:
            return related
    return {}


def _quoted_reference(tweet: dict[str, Any]) -> dict[str, Any] | None:
    legacy = tweet.get("legacy", {})
    quote_id = legacy.get("quoted_status_id_str")
    if not quote_id:
        return None
    permalink = legacy.get("quoted_status_permalink", {})
    url = permalink.get("expanded") or permalink.get("url") or f"https://x.com/i/web/status/{quote_id}"
    match = re.search(r"(?:twitter|x)\.com/([^/]+)/status/", url)
    screen_name = match.group(1) if match else "unknown"
    return {
        "id": quote_id,
        "url": url,
        "text": "",
        "author": {"name": screen_name, "screen_name": screen_name, "id": "", "avatar_url": ""},
        "media": [],
    }


def extract_tweets(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae las entradas visibles, evitando duplicar el tweet original de un RT."""
    instructions = data.get("data", {}).get("list", {}).get("tweets_timeline", {}).get(
        "timeline", {}).get("instructions", [])
    tweets = []
    seen: set[str] = set()
    for instruction in instructions:
        for entry in instruction.get("entries", []):
            result = entry.get("content", {}).get("itemContent", {}).get(
                "tweet_results", {}).get("result", {})
            tweet = result.get("tweet", result)
            normalized = _normalize(tweet)
            if not normalized or normalized["id"] in seen:
                continue
            seen.add(normalized["id"])
            original = _related_tweet(tweet, "retweeted_status_result")
            normalized_original = _normalize(original)
            if normalized_original:
                normalized["retweeted"] = normalized_original
                # X puede repetir la media del tweet original en el RT visible.
                normalized["media"] = []
            quoted = _related_tweet(tweet, "quoted_status_result")
            if not quoted and original:
                quoted = _related_tweet(original, "quoted_status_result")
            normalized_quoted = _normalize(quoted) if quoted else None
            normalized["quoted"] = normalized_quoted or _quoted_reference(original or tweet)
            tweets.append(normalized)
    return tweets


def main() -> int:
    parser = argparse.ArgumentParser(description="Crear un archivo local de tweets desde una response de X.")
    parser.add_argument("response", type=Path, help="response-*.json o carpeta que contenga responses")
    parser.add_argument("--output", type=Path, default=Path("twitter_archive"),
                        help="Carpeta de salida (por defecto: twitter_archive)")
    args = parser.parse_args()
    files = sorted(args.response.glob("response-*.json")) if args.response.is_dir() else [args.response]
    if not files:
        parser.error("No se encontraron archivos response-*.json")
    all_tweets: dict[str, dict[str, Any]] = {}
    for path in files:
        with path.open(encoding="utf-8") as stream:
            for tweet in extract_tweets(json.load(stream)):
                all_tweets[tweet["id"]] = tweet
    tweets = sorted(all_tweets.values(), key=lambda item: item["timestamp"], reverse=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "tweets.json").write_text(json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8")
    html = HTML_TEMPLATE.replace("__TWEETS__", json.dumps(tweets, ensure_ascii=False).replace("</", "<\\/"))
    (args.output / "index.html").write_text(html, encoding="utf-8")
    print(f"Tweets guardados: {len(tweets)}")
    print(f"Página: {(args.output / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
