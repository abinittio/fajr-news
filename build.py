"""Daily Fajr-gated news digest.

Runs on every cron tick in the pre-dawn window. Does nothing until today's Fajr
has passed, then builds one digest, writes docs/index.html, and records the date
so later ticks the same day skip. The workflow commits the result, which both
publishes the page and stops the once-a-day guard from firing twice.
"""

import html
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import yaml

from fajr import fajr_for

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "last_run.txt"
OUT = ROOT / "docs" / "index.html"


def load_config():
    with open(ROOT / "config.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def already_posted_today(today_str: str) -> bool:
    return STATE.exists() and STATE.read_text(encoding="utf-8").strip() == today_str


def fetch_sections(sections, max_items):
    """For each configured section, pull its feeds, newest first, capped per section.

    Returns a list of {name, brief, items}. A dead or empty feed simply contributes
    nothing; feedparser does not raise on a bad URL.
    """
    result = []
    for sec in sections:
        items = []
        exclude_shorts = sec.get("exclude_shorts", False)
        for url in sec.get("feeds", []):
            parsed = feedparser.parse(url)
            source = parsed.feed.get("title", url)
            for entry in parsed.entries:
                link = entry.get("link", "")
                if exclude_shorts and "/shorts/" in link:
                    continue  # drop YouTube Shorts from new-episode listings
                items.append(
                    {
                        "source": source,
                        "title": entry.get("title", "").strip(),
                        "link": link,
                        "published": entry.get("published_parsed"),
                    }
                )
        items.sort(key=lambda i: i["published"] or time.gmtime(0), reverse=True)
        result.append(
            {
                "name": sec["name"],
                "brief": (sec.get("brief") or "").strip(),
                "items": items[:max_items],
            }
        )
    return result


def render_body_fallback(sections):
    """No API key set: emit an honest raw list of headlines under each section."""
    parts = [
        '<p class="note">Set a free <code>GEMINI_API_KEY</code> secret to get an '
        "AI-synthesised brief (and the Nothing but Facts summary) instead of this raw "
        "headline list.</p>"
    ]
    for sec in sections:
        if not sec["items"]:
            continue
        parts.append(f'<section><h2>{html.escape(sec["name"])}</h2>')
        for it in sec["items"]:
            title = html.escape(it["title"])
            source = html.escape(it["source"])
            link = html.escape(it["link"], quote=True)
            parts.append(
                f'<article><h3><a href="{link}">{title}</a></h3>'
                f'<p class="source">{source}</p></article>'
            )
        parts.append("</section>")
    return "\n".join(parts)


def _sections_prompt(sections):
    chunks = []
    for sec in sections:
        if not sec["items"]:
            continue
        lines = []
        for it in sec["items"]:
            date = time.strftime("%Y-%m-%d", it["published"]) if it["published"] else "date unknown"
            lines.append(f'- [{it["source"]}] {it["title"]} ({it["link"]}) [published {date}]')
        chunks.append(
            f'## {sec["name"]}\nScope: {sec["brief"]}\nCandidate items:\n' + "\n".join(lines)
        )
    return "\n\n".join(chunks)


def render_body_ai(sections, cfg, today):
    """Synthesise a sectioned brief with the configured LLM. Falls back to the raw
    list if the model call fails for any reason."""
    interests = (cfg["digest"].get("interests") or "").strip()
    interests_block = (
        "My standing interests and priorities. Use these to rank AND filter every "
        "section, not just to pick topics:\n" + interests + "\n\n"
    ) if interests else ""
    prompt = (
        f"You are writing my personal morning news brief for {today}, organised into "
        "fixed sections. Below are the sections in the order I want them, each with its "
        "scope and today's candidate items (each tagged with its publish date).\n\n"
        f"{interests_block}"
        "For each section, in order:\n"
        "- Write it under an <h2> with exactly the section name.\n"
        "- Apply the scope strictly and DROP candidate items that fall outside it. In "
        "Sports that means football only for internationals and the Premier League, La "
        "Liga, and Bundesliga, and combat only for UFC/Bellator MMA, big boxing "
        "stories, Glory kickboxing, and ONE Championship or Hamza El Haimer Muay Thai.\n"
        "- Then rank the surviving items by relevance to my interests above, then "
        "importance, then recency; lead with the most relevant, and drop off-interest "
        "noise even when it technically fits the section.\n"
        "- Synthesise the kept items into one to three short paragraphs in your own "
        "words, with <a> links to the sources.\n"
        "- If a section has nothing in scope, omit that section entirely.\n"
        "- Exception for any podcast/video section (e.g. Podcasts): do NOT synthesise. "
        "List new episodes only, each on its own line as an <a> link to the episode with "
        "the text 'Channel name: episode title', including only full episodes published in "
        "roughly the last day or two.\n\n"
        "Rules:\n"
        "- Do not invent details, numbers, or quotes.\n"
        "- Attribute anything only one source claims; do not state it as fact.\n"
        "- Output a clean HTML fragment only: <section> blocks, each with an <h2> and "
        "paragraphs. No <html>, <head>, or <body> tags.\n\n"
        f"Sections:\n{_sections_prompt(sections)}"
    )
    return _llm(prompt, cfg, max_tokens=4000) or render_body_fallback(sections)


def has_llm(cfg):
    """True if the configured LLM provider has its key available in the environment."""
    provider = cfg["digest"].get("provider", "gemini").lower()
    if provider == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY"))
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return False


def _llm(prompt, cfg, max_tokens):
    """Dispatch to the configured LLM provider. Returns text, or None on any failure."""
    provider = cfg["digest"].get("provider", "gemini").lower()
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        model = cfg["digest"].get("gemini_model", "gemini-2.5-flash")
        return _gemini(prompt, model, key, max_tokens) if key else None
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        model = cfg["digest"].get("model", "claude-haiku-4-5-20251001")
        return _anthropic(prompt, model, max_tokens) if key else None
    return None


def _gemini(prompt, model, key, max_tokens):
    import requests

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        resp = requests.post(
            url,
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                # thinkingBudget 0 stops 2.5 "thinking" models from spending the output
                # budget on internal reasoning; we want the tokens as summary text.
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=180,
        )
        resp.raise_for_status()
        cand = (resp.json().get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts) or None
    except Exception as ex:
        print(f"Gemini call failed: {type(ex).__name__}: {str(ex)[:200]}")
        return None


def _anthropic(prompt, model, max_tokens):
    try:
        from anthropic import Anthropic

        client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text") or None
    except Exception as ex:
        print(f"Anthropic call failed: {type(ex).__name__}: {str(ex)[:200]}")
        return None


def build_transcript_summaries(cfg):
    """Summarise the newest captioned video from each configured channel. Returns the
    concatenated HTML fragments (one <section> each), or "" if none produced output."""
    from nbf import latest_with_transcript

    blocks = []
    for spec in cfg["digest"].get("transcript_summaries", []):
        ep = latest_with_transcript(spec["feed"], spec.get("match"), spec.get("min_words", 0))
        if not ep:
            print(f"No captioned video for {spec['name']} yet; skipping.")
            continue
        prompt = (
            f"Below is the auto-generated transcript of a video from '{spec['name']}'. "
            "Summarise it for my daily brief.\n\n"
            "Output a clean HTML fragment only: a <section> with an <h2> reading exactly "
            f"'{spec['name']}', then a short <p> naming the video with a link to it, then "
            f"the following. {(spec.get('style') or '').strip()}\n"
            "Do not invent anything not in the transcript. The transcript is auto-generated "
            "so proper names may be misspelt; fix obvious ones only if you are confident. "
            "No <html>, <head>, or <body> tags.\n\n"
            f"Video title: {ep['title']}\nVideo link: {ep['url']}\n\n"
            f"Transcript:\n{ep['transcript'][:200000]}"
        )
        out = _llm(prompt, cfg, max_tokens=2000)
        if out:
            blocks.append(out)
        else:
            print(f"{spec['name']} summary: LLM returned nothing; skipping.")
    return "\n".join(blocks)


# Interactive static page. Placeholder tokens (__TITLE__ etc.) are filled by render_page,
# so the embedded CSS/JS braces need no escaping. Client-side JS only, so it works on
# GitHub Pages: collapsible sections, a jump-to nav, live filter, and a theme toggle.
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{ --bg:#f7f5f2; --fg:#191919; --muted:#6f6f6f; --line:#e6e2db; --card:#fff;
  --accent:#0f6d5c; --chip:#ece8e1; --chipA:#0f6d5c; --chipAfg:#fff; }
@media (prefers-color-scheme: dark){ :root{ --bg:#141414; --fg:#ececec; --muted:#9a9a9a;
  --line:#2a2a2a; --card:#1c1c1c; --accent:#4cc3ab; --chip:#242424; --chipA:#4cc3ab; --chipAfg:#0b0b0b; } }
:root[data-theme="light"]{ --bg:#f7f5f2; --fg:#191919; --muted:#6f6f6f; --line:#e6e2db;
  --card:#fff; --accent:#0f6d5c; --chip:#ece8e1; --chipA:#0f6d5c; --chipAfg:#fff; }
:root[data-theme="dark"]{ --bg:#141414; --fg:#ececec; --muted:#9a9a9a; --line:#2a2a2a;
  --card:#1c1c1c; --accent:#4cc3ab; --chip:#242424; --chipA:#4cc3ab; --chipAfg:#0b0b0b; }
*{ box-sizing:border-box; }
body{ margin:0; background:var(--bg); color:var(--fg);
  font:1.02rem/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
a{ color:var(--accent); text-decoration:none; }
a:hover{ text-decoration:underline; }
.top{ position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line);
  padding:.75rem 1rem; display:flex; align-items:baseline; gap:.6rem; }
.top h1{ font-size:1.1rem; margin:0; }
.top .meta{ color:var(--muted); font-size:.8rem; }
.top .spacer{ flex:1; }
.top button{ background:var(--chip); color:var(--fg); border:0; border-radius:999px;
  padding:.35rem .65rem; font-size:.9rem; cursor:pointer; }
.chips{ position:sticky; top:3rem; z-index:4; background:var(--bg); border-bottom:1px solid var(--line);
  display:flex; gap:.4rem; overflow-x:auto; padding:.55rem 1rem; scrollbar-width:none; }
.chips::-webkit-scrollbar{ display:none; }
.chip{ white-space:nowrap; background:var(--chip); color:var(--fg); border:0; border-radius:999px;
  padding:.35rem .8rem; font-size:.85rem; cursor:pointer; }
.chip.active{ background:var(--chipA); color:var(--chipAfg); }
.toolbar{ max-width:760px; margin:0 auto; padding:.8rem 1rem 0; display:flex; gap:.5rem; }
.toolbar input{ flex:1; background:var(--card); color:var(--fg); border:1px solid var(--line);
  border-radius:.6rem; padding:.5rem .8rem; font-size:.95rem; }
.toolbar button{ background:var(--chip); color:var(--fg); border:0; border-radius:.6rem;
  padding:.5rem .8rem; font-size:.85rem; cursor:pointer; white-space:nowrap; }
main{ max-width:760px; margin:0 auto; padding:1rem; }
section{ background:var(--card); border:1px solid var(--line); border-radius:.8rem;
  margin:0 0 1rem; box-shadow:0 1px 2px rgba(0,0,0,.04); overflow:hidden; }
section h2{ font-size:1.1rem; margin:0; padding:.9rem 1rem; cursor:pointer;
  display:flex; align-items:center; gap:.5rem; }
section h2::before{ content:"\\25be"; color:var(--muted); font-size:.75rem; transition:transform .15s; }
section.collapsed h2::before{ transform:rotate(-90deg); }
section .body{ padding:0 1rem 1rem; }
section.collapsed .body{ display:none; }
section h3{ font-size:1rem; margin:1rem 0 .3rem; }
section p{ margin:.5rem 0; }
section ul{ margin:.4rem 0 .8rem; padding-left:1.2rem; }
section li{ margin:.25rem 0; }
section article{ margin:.7rem 0; }
section article h3{ margin:0 0 .2rem; }
.source{ color:var(--muted); font-size:.82rem; margin:.15rem 0 0; }
.note{ background:var(--chip); padding:.6rem .8rem; border-radius:.5rem; font-size:.9rem;
  margin:0 auto 1rem; max-width:760px; }
footer{ max-width:760px; margin:0 auto; padding:1.4rem 1rem 3rem; color:var(--muted); font-size:.8rem; }
.empty{ color:var(--muted); text-align:center; padding:2rem; }
</style>
</head>
<body>
<header class="top">
  <h1>__TITLE__</h1>
  <span class="meta">__DATE__ &middot; Fajr __FAJR__</span>
  <span class="spacer"></span>
  <button id="theme" title="Toggle light/dark">&#9680;</button>
</header>
<nav class="chips" id="nav"></nav>
<div class="toolbar">
  <input id="filter" type="search" placeholder="Filter the feed..." autocomplete="off">
  <button id="toggleAll">Collapse all</button>
</div>
<main id="feed">
__BODY__
</main>
<footer>Auto-generated daily at Fajr. Summaries are synthesised across the linked sources
and may contain errors; follow the links for the originals.</footer>
<script>
(function(){
  var feed=document.getElementById('feed');
  var sections=[].slice.call(feed.querySelectorAll(':scope > section'));
  sections.forEach(function(sec,i){
    var h2=sec.querySelector('h2'); if(!h2) return;
    if(!sec.id) sec.id='sec'+i;
    var body=document.createElement('div'); body.className='body';
    var n=h2.nextSibling;
    while(n){ var nx=n.nextSibling; body.appendChild(n); n=nx; }
    sec.appendChild(body);
    h2.addEventListener('click',function(){ sec.classList.toggle('collapsed'); });
  });
  var nav=document.getElementById('nav');
  var chips=sections.map(function(sec){
    var h2=sec.querySelector('h2'); if(!h2) return null;
    var b=document.createElement('button'); b.className='chip';
    b.textContent=h2.textContent.trim(); b.dataset.sec=sec.id;
    b.addEventListener('click',function(){
      sec.classList.remove('collapsed');
      sec.scrollIntoView({behavior:'smooth',block:'start'});
    });
    nav.appendChild(b); return b;
  }).filter(Boolean);
  if('IntersectionObserver' in window){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(e){ if(e.isIntersecting){
        chips.forEach(function(c){ c.classList.toggle('active', c.dataset.sec===e.target.id); });
      }});
    },{rootMargin:'-25% 0px -65% 0px'});
    sections.forEach(function(s){ io.observe(s); });
  }
  var filter=document.getElementById('filter');
  filter.addEventListener('input',function(){
    var q=filter.value.trim().toLowerCase(), any=false;
    sections.forEach(function(sec){
      var m=!q || sec.textContent.toLowerCase().indexOf(q)>=0;
      sec.style.display=m?'':'none'; if(m) any=true;
      if(q) sec.classList.remove('collapsed');
    });
    var e=document.getElementById('noresult');
    if(!any && q){ if(!e){ e=document.createElement('div'); e.id='noresult'; e.className='empty';
      e.textContent='No matches.'; feed.appendChild(e); } }
    else if(e){ e.remove(); }
  });
  var ta=document.getElementById('toggleAll'), collapsed=false;
  ta.addEventListener('click',function(){
    collapsed=!collapsed;
    sections.forEach(function(s){ s.classList.toggle('collapsed',collapsed); });
    ta.textContent=collapsed?'Expand all':'Collapse all';
  });
  var root=document.documentElement, tb=document.getElementById('theme'), saved=null;
  try{ saved=localStorage.getItem('theme'); }catch(e){}
  if(saved) root.setAttribute('data-theme',saved);
  tb.addEventListener('click',function(){
    var cur=root.getAttribute('data-theme');
    var sysDark=window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var next=(cur? cur==='dark' : sysDark) ? 'light':'dark';
    root.setAttribute('data-theme',next);
    try{ localStorage.setItem('theme',next); }catch(e){}
  });
})();
</script>
</body>
</html>
"""


def render_page(title, date, fajr, body):
    return (
        PAGE.replace("__TITLE__", title)
        .replace("__DATE__", date)
        .replace("__FAJR__", fajr)
        .replace("__BODY__", body)
    )


def main():
    cfg = load_config()
    tz = ZoneInfo(cfg["location"]["timezone"])
    now_local = datetime.now(tz)
    today_str = now_local.date().isoformat()

    # FORCE=1 (set by the manual "force" workflow input) bypasses both gates for testing.
    force = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")

    if not force and already_posted_today(today_str):
        print(f"Already posted for {today_str}; skipping.")
        return

    fajr = fajr_for(
        now_local,
        cfg["location"]["latitude"],
        cfg["location"]["longitude"],
        cfg["fajr"]["method"],
        cfg["fajr"]["high_latitude_rule"],
    )
    if not force and now_local < fajr:
        print(f"Before Fajr ({fajr:%H:%M}); skipping this tick.")
        return

    print(f"Fajr ({fajr:%H:%M}) passed and no digest yet for {today_str}. Building.")
    sections = fetch_sections(cfg["sections"], cfg["digest"]["max_items_per_section"])
    if not any(sec["items"] for sec in sections):
        print("No feed items fetched at all; leaving the existing page in place.")
        return

    llm = has_llm(cfg)
    if llm:
        body = render_body_ai(sections, cfg, today_str)
    else:
        body = render_body_fallback(sections)

    # Transcript summaries of configured channels (need both a transcript and an LLM).
    if llm and cfg["digest"].get("transcript_summaries"):
        ts_html = build_transcript_summaries(cfg)
        if ts_html:
            body = ts_html + "\n" + body

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        render_page(
            html.escape(cfg["digest"]["title"]),
            now_local.strftime("%A %d %B %Y"),
            fajr.strftime("%H:%M"),
            body,
        ),
        encoding="utf-8",
    )
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(today_str, encoding="utf-8")
    print(f"Digest written for {today_str}.")


if __name__ == "__main__":
    sys.exit(main())
