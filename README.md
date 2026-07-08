# Fajr news digest

A personal news brief that rebuilds itself once a day, right at Fajr, and serves as a
static page (intended for `news.nabil.engineer`). It pulls RSS from a curated set of
sources, organises them into your sections, optionally synthesises a brief with an LLM,
and adds a daily topic-and-bullets summary of the newest Nothing but Facts episode from
its transcript. Free to host and, with the default settings, free to run.

## How the Fajr timing works

"At Fajr" is not a fixed clock time. It drifts daily and swings about four hours across
the year in London, so a plain fixed cron cannot follow it. Instead:

- The workflow runs every 15 minutes across the pre-dawn window (`.github/workflows/fajr-digest.yml`).
- Each run computes today's real Fajr for your coordinates offline with `adhanpy` (`fajr.py`).
- `build.py` is the gate: it does nothing until Fajr has passed, builds the digest on the
  first tick after, then records the date in `state/last_run.txt` so later ticks skip.

A high-latitude rule is set because at London's latitude the sun never reaches the Fajr
angle around midsummer, so the plain calculation would otherwise return nonsense.

## What's in it

- Sections, in order, from `config.yml`: Ummah, London, Tech, Medical, Financial, Sports,
  Podcasts. Each pulls its own feeds and is summarised against its scope.
- Ummah uses a curated allowlist (Al Jazeera, 5Pillars, Middle East Eye, Declassified UK)
  plus writer-follows for Peter Oborne and Sami Hamdi via Google News.
- Podcasts lists new YouTube episodes from Safina Society, The Thinking Muslim, and 5Pillars.
- A **Nothing but Facts** block: topics covered in the latest captioned episode, each with
  a couple of bullets, built from the episode's transcript (see below).

## Choosing the LLM (free by default)

Set `digest.provider` in `config.yml`:

- **`gemini`** (default, free): get a free key at https://aistudio.google.com/apikey and add
  it as a repository secret named `GEMINI_API_KEY`. The free tier is far more than one
  digest a day needs.
- **`anthropic`**: set `provider: anthropic` and add `ANTHROPIC_API_KEY`. Pay-as-you-go,
  roughly cents a month on Haiku.
- **No key at all**: the page still builds every day, just as a plain list of headlines and
  new episodes instead of a synthesised brief. The Nothing but Facts summary needs a key.

If the model call fails for any reason, the page falls back to the plain list rather than
breaking, so a bad key or a model outage never leaves you with an empty page.

## The Nothing but Facts summary

`build_nbf_summary` (in `build.py`, using `nbf.py`) finds the newest Nothing but Facts
episode on the Safina Society channel, pulls its YouTube auto-captions with `yt-dlp` (free,
no key), and asks the LLM for the topics covered with two or three bullets each.

Two honest limitations:

- **Caption lag.** YouTube generates auto-captions a few hours to a day after upload, so the
  newest episode often has none yet. The code walks back to the newest episode that *does*
  have captions, so some days the summary is of yesterday's episode until the latest is
  captioned. The block names which episode it summarised.
- **CI reliability.** YouTube sometimes blocks caption fetches from datacenter IPs like
  GitHub Actions runners. `yt-dlp` is fairly robust, but on a day it gets blocked the
  summary is simply skipped rather than failing the run.

## One-time setup

1. **Create the repo and push** (commands at the bottom). Make it **public** so Actions
   minutes are free and unlimited.
2. **Add your LLM key** as a repository secret: `GEMINI_API_KEY` (free, default) or
   `ANTHROPIC_API_KEY` (Settings, Secrets and variables, Actions, New repository secret).
3. **Enable Pages**: Settings, Pages, Source = Deploy from a branch, Branch = `main`,
   Folder = `/docs`.
4. **Point the subdomain**: add a `CNAME` DNS record for `news` to `<username>.github.io`.
   The `docs/CNAME` file already claims `news.nabil.engineer`; edit if you use another host.
5. **Test it now**: Actions tab, "Fajr news digest", Run workflow, tick **force**. That
   bypasses the Fajr gate and the once-a-day guard so it builds immediately.

## Configure it

`config.yml` holds everything: your `location`, the `fajr` method and high-latitude rule,
the `digest` block (provider, models, title, and the `nbf` toggle), and the `sections` list.
Each section has a `name`, a `brief` (its scope, which the summariser enforces), and its own
`feeds`. Add or remove feeds freely; RSS only, since scraping full pages can breach a site's
terms and a dead feed is ignored rather than fatal.

## Run it locally

```
pip install -r requirements.txt
python build.py
```

It reports whether it is before Fajr, already posted today, or building. To force a build
while testing, set `state/last_run.txt` to an old date and set `FORCE=1` in the environment.

## Cost

Hosting and Actions are free on a public repo. With `provider: gemini` the LLM is free too,
so the whole thing runs at no cost. Transcripts are free (YouTube captions via yt-dlp).
Only `provider: anthropic` costs money, and only cents a month at one run a day.

## A note on sources

The news sections **synthesise** across outlets in their own words and link back; they do
not republish article text. Feeds are RSS, and single-source claims are attributed rather
than stated as fact. The Nothing but Facts summary is drawn from the episode's own
auto-generated transcript and links to the episode.

## Go live

```
cd fajr-news
git init
git add .
git commit -m "Fajr news digest"
gh repo create fajr-news --public --source=. --push
```

Then do the Pages and secret steps above.
