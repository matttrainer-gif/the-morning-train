#!/usr/bin/env python3
"""
The Morning Train — Digest Generator
Fetches news from curated RSS feeds, analyzes with Claude API,
generates a styled HTML report and sends an email summary.

Features:
  - Balanced centrist analysis with sources from left, center, and right
  - Verification levels (Confirmed / Developing / Disputed / Analysis)
  - Bias spectrum showing how left vs. right sources frame each story
  - Date-based archive system (each day gets a permalink)
  - Podcast commentary section with cross-show comparison
"""

import os
import sys
import json
import re
import hashlib
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import feedparser
import anthropic
import requests
from jinja2 import Environment, FileSystemLoader
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CATEGORIES = {
    "AI & Technology": {
        "emoji": "&#129302;",
        "feeds": [
            ("MIT Technology Review – AI", "https://www.technologyreview.com/feed/"),
            ("Ars Technica – AI", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
            ("The Verge – AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
            ("TechCrunch – AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
            ("VentureBeat – AI", "https://venturebeat.com/category/ai/feed/"),
            ("OpenAI Blog", "https://openai.com/blog/rss.xml"),
        ],
        "podcasts": [
            ("Hard Fork (NYT)", "https://feeds.simplecast.com/l2i9YnTd"),
        ],
    },
    "US Politics": {
        "emoji": "&#127482;&#127480;",
        "feeds": [
            # --- Wire services (center) ---
            ("AP News – Politics", "https://rsshub.app/apnews/topics/politics"),
            ("Reuters – US Politics", "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best"),
            # --- Center-left ---
            ("NPR – Politics", "https://feeds.npr.org/1014/rss.xml"),
            ("BBC – US & Canada", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
            ("Politico", "https://www.politico.com/rss/politicopicks.xml"),
            # --- Center / aggregators ---
            ("The Hill", "https://thehill.com/feed/"),
            ("RealClearPolitics", "https://www.realclearpolitics.com/index.xml"),
            # --- Center-right / right ---
            ("WSJ – Opinion", "https://feeds.a.dj.com/rss/RSSOpinion.xml"),
            ("WSJ – US News", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
            ("National Review", "https://www.nationalreview.com/feed/"),
            ("The Dispatch", "https://thedispatch.com/feed/"),
            ("Fox News – Politics", "https://moxie.foxnews.com/google-publisher/politics.xml"),
            ("Reason", "https://reason.com/feed/"),
            ("The Free Press", "https://www.thefp.com/feed"),
        ],
        "podcasts": [
            # --- Center-left ---
            ("The Ezra Klein Show", "https://feeds.simplecast.com/82FI35Px"),
            ("NYT The Daily", "https://feeds.simplecast.com/54nAGcIl"),
            ("NPR Up First", "https://feeds.npr.org/510318/podcast.xml"),
            # --- Heterodox / center ---
            ("Real Time with Bill Maher", "https://feeds.megaphone.fm/real-time-with-bill-maher"),
            # --- Center-right / right ---
            ("Advisory Opinions (The Dispatch)", "https://feeds.megaphone.fm/advisory-opinions"),
            ("The Remnant – Jonah Goldberg", "https://feeds.megaphone.fm/the-remnant"),
            ("The Megyn Kelly Show", "https://feeds.megaphone.fm/megynkellyshow"),
            ("Honestly – Bari Weiss", "https://feeds.megaphone.fm/honestly"),
            ("The Ben Shapiro Show", "https://feeds.megaphone.fm/WWO3519750118"),
        ],
    },
    "World Politics": {
        "emoji": "&#127758;",
        "feeds": [
            # --- Wire services (center) ---
            ("AP News – World", "https://rsshub.app/apnews/topics/world-news"),
            ("Reuters – World", "https://www.reutersagency.com/feed/?best-topics=world&post_type=best"),
            # --- International (varied perspectives) ---
            ("BBC – World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
            ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
            ("France 24", "https://www.france24.com/en/rss"),
            ("DW News", "https://rss.dw.com/rdf/rss-en-world"),
            # --- Center-right / right ---
            ("WSJ – World News", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
            ("National Review – World", "https://www.nationalreview.com/category/world/feed/"),
        ],
        "podcasts": [
            ("The Ezra Klein Show", "https://feeds.simplecast.com/82FI35Px"),
            ("The Remnant – Jonah Goldberg", "https://feeds.megaphone.fm/the-remnant"),
        ],
    },
    "Financial Analysis": {
        "emoji": "&#128200;",
        "feeds": [
            ("CNBC – Finance", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
            ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
            ("Bloomberg via Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
            ("Financial Times", "https://www.ft.com/rss/home"),
            ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
            ("Barron's", "https://www.barrons.com/market-data/rss"),
        ],
        "podcasts": [
            ("All-In Podcast", "https://feeds.megaphone.fm/all-in-with-chamath-jason-sacks-and-friedberg"),
        ],
    },
    "Movies & Shows": {
        "emoji": "&#127916;",
        "feeds": [
            ("Rotten Tomatoes", "https://editorial.rottentomatoes.com/feed/"),
            ("Collider", "https://collider.com/feed/"),
            ("Screen Rant", "https://screenrant.com/feed/"),
            ("IndieWire", "https://www.indiewire.com/feed/"),
            ("Decider", "https://decider.com/feed/"),
            ("Variety Film", "https://variety.com/v/film/feed/"),
        ],
    },
    "PC & PS5 Gaming": {
        "emoji": "&#127918;",
        "feeds": [
            ("IGN", "https://feeds.feedburner.com/ign/all"),
            ("PC Gamer", "https://www.pcgamer.com/rss/"),
            ("Eurogamer", "https://www.eurogamer.net/feed"),
            ("GameSpot", "https://www.gamespot.com/feeds/mashup/"),
            ("Push Square (PS5)", "https://www.pushsquare.com/feeds/latest"),
            ("Rock Paper Shotgun", "https://www.rockpapershotgun.com/feed"),
        ],
    },
    "Podcast Commentary": {
        "emoji": "&#127897;",
        "feeds": [],
        "podcasts": [
            # --- Center-left ---
            ("The Ezra Klein Show", "https://feeds.simplecast.com/82FI35Px"),
            ("NYT The Daily", "https://feeds.simplecast.com/54nAGcIl"),
            ("NPR Up First", "https://feeds.npr.org/510318/podcast.xml"),
            ("Hard Fork (NYT)", "https://feeds.simplecast.com/l2i9YnTd"),
            # --- Heterodox / center ---
            ("Making Sense – Sam Harris", "https://wakingup.libsyn.com/rss"),
            ("All-In Podcast", "https://feeds.megaphone.fm/all-in-with-chamath-jason-sacks-and-friedberg"),
            ("Real Time with Bill Maher", "https://feeds.megaphone.fm/real-time-with-bill-maher"),
            ("Club Random with Bill Maher", "https://feeds.simplecast.com/jBp_kwbg"),
            ("Honestly – Bari Weiss", "https://feeds.megaphone.fm/honestly"),
            # --- Center-right / right ---
            ("Advisory Opinions (The Dispatch)", "https://feeds.megaphone.fm/advisory-opinions"),
            ("The Remnant – Jonah Goldberg", "https://feeds.megaphone.fm/the-remnant"),
            ("The Megyn Kelly Show", "https://feeds.megaphone.fm/megynkellyshow"),
            ("The Ben Shapiro Show", "https://feeds.megaphone.fm/WWO3519750118"),
        ],
        "is_podcast_section": True,
    },
}

MAX_ARTICLES_PER_CATEGORY = 15   # fetched from feeds
MAX_STORIES_PER_CATEGORY = 5     # after Claude selects the best
LOOKBACK_HOURS = 28              # how far back to consider articles


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Article:
    title: str
    link: str
    source: str
    published: str
    summary: str = ""
    category: str = ""
    is_podcast: bool = False
    episode_duration: str = ""
    image_url: str = ""


ENTERTAINMENT_CATEGORIES = {"Movies & Shows", "PC & PS5 Gaming"}


@dataclass
class AnalyzedStory:
    headline: str
    analysis: str
    sources: list = field(default_factory=list)
    key_facts: list = field(default_factory=list)
    sentiment: str = "neutral"
    verification: str = "confirmed"  # confirmed / developing / disputed / analysis
    bias_spectrum: list = field(default_factory=list)  # list of {source, lean, framing}


# ---------------------------------------------------------------------------
# RSS Fetching
# ---------------------------------------------------------------------------

def fetch_articles(category_name: str, feeds: list[tuple[str, str]]) -> list[Article]:
    """Fetch recent articles from RSS feeds for a category."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles = []

    for source_name, feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:
                # Parse published date
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    from calendar import timegm
                    published = datetime.fromtimestamp(timegm(entry.published_parsed), tz=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    from calendar import timegm
                    published = datetime.fromtimestamp(timegm(entry.updated_parsed), tz=timezone.utc)

                # If no date or too old, still include (some feeds lack dates)
                if published and published < cutoff:
                    continue

                summary = ""
                if hasattr(entry, "summary"):
                    summary = re.sub(r"<[^>]+>", "", entry.summary)[:500]

                # Extract image URL from RSS entry
                image_url = ""
                if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                    image_url = entry.media_thumbnail[0].get("url", "")
                if not image_url and hasattr(entry, "media_content") and entry.media_content:
                    for mc in entry.media_content:
                        if mc.get("medium") == "image" or mc.get("type", "").startswith("image/"):
                            image_url = mc.get("url", "")
                            break
                    if not image_url:
                        image_url = entry.media_content[0].get("url", "")
                if not image_url and hasattr(entry, "enclosures") and entry.enclosures:
                    for enc in entry.enclosures:
                        if enc.get("type", "").startswith("image/"):
                            image_url = enc.get("href", enc.get("url", ""))
                            break

                articles.append(Article(
                    title=entry.get("title", "Untitled"),
                    link=entry.get("link", ""),
                    source=source_name,
                    published=published.isoformat() if published else "Unknown",
                    summary=summary,
                    category=category_name,
                    image_url=image_url,
                ))
        except Exception as e:
            print(f"  [WARN] Failed to fetch {source_name}: {e}", file=sys.stderr)

    # Deduplicate by similar titles
    seen = set()
    unique = []
    for a in articles:
        key = hashlib.md5(a.title.lower().encode()).hexdigest()[:8]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # Sort by date (newest first), limit
    unique.sort(key=lambda a: a.published if a.published != "Unknown" else "", reverse=True)
    return unique[:MAX_ARTICLES_PER_CATEGORY]


def fetch_podcast_episodes(category_name: str, podcasts: list[tuple[str, str]]) -> list[Article]:
    """Fetch recent podcast episodes. Podcast RSS feeds often have richer descriptions."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS * 3)  # wider window for podcasts
    episodes = []

    for show_name, feed_url in podcasts:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # podcasts release less frequently
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    from calendar import timegm
                    published = datetime.fromtimestamp(timegm(entry.published_parsed), tz=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    from calendar import timegm
                    published = datetime.fromtimestamp(timegm(entry.updated_parsed), tz=timezone.utc)

                if published and published < cutoff:
                    continue

                # Podcast feeds often have much richer descriptions/show notes
                summary = ""
                # Try content:encoded first (often has full show notes)
                if hasattr(entry, "content") and entry.content:
                    for c in entry.content:
                        if hasattr(c, "value"):
                            summary = re.sub(r"<[^>]+>", "", c.value)[:1500]
                            break
                # Fall back to summary/description
                if not summary and hasattr(entry, "summary"):
                    summary = re.sub(r"<[^>]+>", "", entry.summary)[:1500]
                if not summary and hasattr(entry, "description"):
                    summary = re.sub(r"<[^>]+>", "", entry.description)[:1500]

                # Try to get duration
                duration = ""
                if hasattr(entry, "itunes_duration"):
                    duration = entry.itunes_duration

                episodes.append(Article(
                    title=entry.get("title", "Untitled"),
                    link=entry.get("link", ""),
                    source=f"{show_name} (Podcast)",
                    published=published.isoformat() if published else "Unknown",
                    summary=summary,
                    category=category_name,
                    is_podcast=True,
                    episode_duration=duration,
                ))
        except Exception as e:
            print(f"  [WARN] Failed to fetch podcast {show_name}: {e}", file=sys.stderr)

    episodes.sort(key=lambda a: a.published if a.published != "Unknown" else "", reverse=True)
    return episodes


def fetch_all_categories() -> dict[str, list[Article]]:
    """Fetch articles and podcast episodes for all categories."""
    result = {}
    for cat_name, cat_cfg in CATEGORIES.items():
        print(f"Fetching: {cat_name}...")

        # Fetch regular articles
        articles = fetch_articles(cat_name, cat_cfg.get("feeds", []))
        print(f"  Found {len(articles)} articles")

        # Fetch podcast episodes
        podcasts = cat_cfg.get("podcasts", [])
        if podcasts:
            episodes = fetch_podcast_episodes(cat_name, podcasts)
            print(f"  Found {len(episodes)} podcast episodes")
            articles.extend(episodes)

        result[cat_name] = articles
    return result


# ---------------------------------------------------------------------------
# Claude Analysis
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = """You are a senior news analyst producing a daily briefing for a well-informed centrist reader.

EDITORIAL STANCE — STRICT BALANCE REQUIREMENTS:
- Your reader wants genuine centrism, not left-of-center with a disclaimer. This is critical.
- For every political story, you MUST represent how BOTH the left and the right view the issue.
  Use the actual sources provided — you have feeds from across the spectrum (AP, Reuters, NPR,
  WSJ, National Review, The Dispatch, Fox News, Reason, The Free Press, and more).
- Lead with confirmed facts from wire services (AP, Reuters). Then present the left-leaning
  framing (NPR, Politico, BBC) and the right-leaning framing (WSJ, National Review, Fox,
  The Dispatch, Reason) side by side.
- Do NOT default to the left-leaning framing as the "neutral" baseline. Conservative positions
  are not inherently more in need of justification than liberal ones.
- Explicitly label which sources/perspectives you are drawing from, e.g. "NPR frames this as..."
  vs. "National Review argues..." or "WSJ's editorial board contends..."
- When a story has genuine factual dispute (not just spin), note what is confirmed vs. contested.
- Opinion is fine to include if it is insightful, but ALWAYS attribute it and label it as opinion.
- Avoid lazy "both sides" false equivalence — sometimes one side has the facts. Say so, with evidence.

VERIFICATION LEVELS — for each story you MUST assign one:
- "confirmed" — Core facts are verified by multiple credible sources (wire services, official records)
- "developing" — Story is still unfolding; some facts confirmed but key details pending
- "disputed" — Key claims are contested between sources; no clear consensus on central facts
- "analysis" — Story is primarily opinion/analysis/commentary rather than breaking news

BIAS SPECTRUM — for political and controversial stories, include a "bias_spectrum" array showing
how different sources frame the story. Each entry: {"source": "Source Name", "lean": "left|center-left|center|center-right|right", "framing": "One sentence describing how this source frames the story"}.
Include 3-5 entries from across the spectrum when available. For non-political stories (gaming,
entertainment, pure tech), you may omit this or provide an empty array.

OTHER GUIDELINES:
- Prioritize confirmed facts, data, and verified reporting over opinion
- For financial analysis, include relevant data points (market moves, earnings, economic indicators)
- For entertainment and gaming, focus on major releases, industry moves, and notable critical reception
- Be direct and analytical — no filler, no hype, no breathless language
- Each story should have: a clear headline, 1-2 paragraph analysis, key facts, source attribution
- Group related stories together rather than repeating similar items

OUTPUT FORMAT — respond with valid JSON only, no markdown fences:
{
  "section_summary": "1-2 sentence overview of the most important theme in this section",
  "stories": [
    {
      "headline": "Clear, factual headline",
      "analysis": "1-2 paragraph balanced analysis. Include both left and right perspectives for political stories.",
      "key_facts": ["Fact 1", "Fact 2", "Fact 3"],
      "sources": [{"name": "Source Name", "url": "https://..."}],
      "sentiment": "neutral|positive|negative|bullish|bearish|mixed",
      "verification": "confirmed|developing|disputed|analysis",
      "bias_spectrum": [
        {"source": "NPR", "lean": "center-left", "framing": "How NPR frames it"},
        {"source": "WSJ", "lean": "center-right", "framing": "How WSJ frames it"}
      ]
    }
  ]
}

Select the TOP 3-5 most significant stories. Merge overlapping coverage into single stories with MULTIPLE sources from BOTH sides of the spectrum."""


PODCAST_SYSTEM_PROMPT = """You are a senior media analyst producing a podcast digest for a well-informed centrist reader.

EDITORIAL STANCE — STRICT BALANCE REQUIREMENTS:
- Your podcast sources span left to right. You MUST give roughly equal weight to both sides.
- When summarizing political topics across shows, ALWAYS juxtapose left and right podcast takes.
  For example: "Ezra Klein argued X, while Jonah Goldberg on The Remnant countered with Y."
- Do NOT treat left-leaning takes as the default or neutral position.
- Each host has known leanings — label them:
  * CENTER-LEFT: Ezra Klein, The Daily, NPR Up First, Hard Fork
  * HETERODOX/CENTER: Sam Harris, Bill Maher, Bari Weiss, All-In
  * CENTER-RIGHT/RIGHT: Jonah Goldberg (The Remnant), Advisory Opinions (The Dispatch),
    Megyn Kelly, Ben Shapiro
- Highlight where hosts AGREE across the spectrum — that often signals real truth.
- Highlight where they sharply DISAGREE — and explain what factual basis each side claims.

VERIFICATION LEVELS — for each story you MUST assign one:
- "confirmed" — Discussion based on verified facts/data
- "developing" — Discussion of an evolving situation with uncertain facts
- "disputed" — Hosts disagree on the underlying facts (not just interpretation)
- "analysis" — Primarily opinion/commentary/philosophical discussion

BIAS SPECTRUM — for each topic that is political or controversial, include a "bias_spectrum"
array: {"source": "Show Name", "lean": "left|center-left|center|center-right|right", "framing": "How this host framed the topic"}.

For each episode or cross-show topic:
- Summarize the key topics discussed and any notable arguments or insights
- Distinguish between the host's opinions and any factual claims made
- Note when multiple shows are covering the same topic — compare their takes
- Flag especially insightful or contrarian perspectives
- For All-In and financial podcasts, highlight specific investment theses or market views
- For Sam Harris, highlight philosophical or ethical arguments
- For Bill Maher, note his takes but flag when he's being provocative vs. substantive
- For Shapiro/Kelly/Goldberg, give their conservative arguments fair treatment

OUTPUT FORMAT — respond with valid JSON only, no markdown fences:
{
  "section_summary": "Overview of the week's podcast conversation themes",
  "stories": [
    {
      "headline": "Episode title or topic headline",
      "analysis": "Summary of discussion, key arguments, notable quotes/positions.",
      "key_facts": ["Key point 1", "Key point 2"],
      "sources": [{"name": "Show Name", "url": "https://..."}],
      "sentiment": "neutral|positive|negative|mixed",
      "verification": "confirmed|developing|disputed|analysis",
      "bias_spectrum": [
        {"source": "Ezra Klein", "lean": "center-left", "framing": "Klein's take"},
        {"source": "Ben Shapiro", "lean": "right", "framing": "Shapiro's take"}
      ]
    }
  ]
}

Select the TOP 3-5 most interesting/important episodes or cross-show themes."""

ENTERTAINMENT_SYSTEM_PROMPT = """You are an entertainment analyst for a daily digest. Your reader wants to know WHAT TO WATCH and WHAT TO PLAY next.

EDITORIAL STANCE:
- You are a knowledgeable friend who has read all the reviews and knows what's worth their time.
- Lead with RECOMMENDATIONS, not industry business news. Skip box office numbers, studio executive changes, or production deals unless they directly affect what's available to watch/play.
- For each story, answer: "Should I watch/play this? Why or why not?"

PRIORITIZE (in order):
1. New releases with strong or notable critical reception
2. Upcoming releases generating significant buzz
3. Hidden gems / under-the-radar recommendations
4. Major franchise or sequel announcements worth knowing
5. Streaming availability changes (new on Netflix, Disney+, Game Pass, etc.)

INCLUDE FOR EACH STORY:
- Review scores when available (Rotten Tomatoes %, Metacritic, OpenCritic)
- Critical consensus — what do reviewers agree/disagree about?
- Platform availability — WHERE can the reader watch/play this?
- Genre and tone — help the reader know if it matches their taste
- Similar titles — "If you liked X, you'll enjoy this"
- Release date if upcoming, or "available now"

FOR GAMING SPECIFICALLY:
- Performance on PC vs PS5
- Game length / value proposition
- Early access vs full release status

FIELDS:
- "sentiment": "positive" = recommended, "negative" = skip, "mixed" = divisive, "neutral" = informational
- "verification": "confirmed" for reviewed/released, "developing" for upcoming, "analysis" for editorial
- "bias_spectrum": return empty array []
- "image_url": pick the best image URL from the article data provided (movie poster, game art, screenshot). Return the URL exactly as given in the IMAGE field. If none available, return empty string.

OUTPUT FORMAT — valid JSON only, no markdown fences:
{
  "section_summary": "1-2 sentence overview: what's worth watching/playing right now",
  "stories": [
    {
      "headline": "Title — key takeaway (e.g. 'Recommended' or 'Worth the Wait')",
      "analysis": "1-2 paragraph review/recommendation with scores, platforms, genre, comparisons.",
      "key_facts": ["Review score / consensus", "Platform availability", "Release date or status"],
      "sources": [{"name": "Source", "url": "https://..."}],
      "sentiment": "positive|negative|mixed|neutral",
      "verification": "confirmed|developing|analysis",
      "bias_spectrum": [],
      "image_url": "URL from article data or empty string"
    }
  ]
}

Select TOP 3-5 stories. Prioritize actionable recommendations. Merge overlapping coverage of the same title."""


def analyze_category(client: anthropic.Anthropic, category: str, articles: list[Article]) -> dict:
    """Use Claude to analyze articles for a category."""
    if not articles:
        return {"section_summary": "No recent articles found.", "stories": []}

    is_podcast_section = CATEGORIES.get(category, {}).get("is_podcast_section", False)
    is_entertainment = category in ENTERTAINMENT_CATEGORIES

    # Format article text — include image URLs for entertainment categories
    articles_text = "\n\n".join([
        (
            f"PODCAST EPISODE: {a.title}\nSHOW: {a.source}\nDATE: {a.published}\n"
            f"DURATION: {a.episode_duration}\nURL: {a.link}\nSHOW NOTES/DESCRIPTION:\n{a.summary}"
            if a.is_podcast else
            f"TITLE: {a.title}\nSOURCE: {a.source}\nDATE: {a.published}\nURL: {a.link}\n"
            f"{'IMAGE: ' + a.image_url + chr(10) if a.image_url else ''}"
            f"SUMMARY: {a.summary}"
        )
        for a in articles
    ])

    if is_entertainment:
        prompt = f"""Analyze the following {category} content from the last few days.
Select the top 3-5 titles most relevant to someone deciding what to watch or play.
Focus on reviews, recommendations, and new releases — skip pure industry/business news.

ARTICLES:
{articles_text}

Remember: JSON only, no markdown code fences. Recommendation-focused.
Include review scores and platform availability when possible.
For image_url, pick the best image from the IMAGE fields provided for each story."""
    elif is_podcast_section:
        prompt = f"""Analyze the following {category} content from the last few days.
Select the top 3-5 most important stories/episodes and provide balanced analysis.

EPISODES:
{articles_text}

Remember: JSON only, no markdown code fences, centrist perspective, facts first.
Include verification level and bias_spectrum for each story."""
    else:
        prompt = f"""Analyze the following {category} content from the last few days.
Select the top 3-5 most important stories/episodes and provide balanced analysis.

ARTICLES:
{articles_text}

Remember: JSON only, no markdown code fences, centrist perspective, facts first.
Include verification level and bias_spectrum for each story."""

    system = (
        PODCAST_SYSTEM_PROMPT if is_podcast_section
        else ENTERTAINMENT_SYSTEM_PROMPT if is_entertainment
        else ANALYSIS_SYSTEM_PROMPT
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        # Clean potential markdown fences
        text = re.sub(r"^```json?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        result = json.loads(text)

        # Ensure all stories have required fields with defaults
        for story in result.get("stories", []):
            if "verification" not in story:
                story["verification"] = "confirmed"
            if "bias_spectrum" not in story:
                story["bias_spectrum"] = []
            if "image_url" not in story:
                story["image_url"] = ""
            # Validate image URL
            img = story.get("image_url", "")
            if img and not img.startswith("http"):
                story["image_url"] = ""

        return result
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON parse error for {category}: {e}", file=sys.stderr)
        return {"section_summary": "Analysis temporarily unavailable.", "stories": []}
    except Exception as e:
        print(f"  [ERROR] Claude API error for {category}: {e}", file=sys.stderr)
        return {"section_summary": "Analysis temporarily unavailable.", "stories": []}


def analyze_all(articles_by_category: dict[str, list[Article]]) -> dict:
    """Analyze all categories with Claude."""
    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var
    results = {}

    for cat_name, articles in articles_by_category.items():
        print(f"Analyzing: {cat_name}...")
        results[cat_name] = analyze_category(client, cat_name, articles)
        print(f"  Generated {len(results[cat_name].get('stories', []))} stories")

    return results


# ---------------------------------------------------------------------------
# HTML Report Generation
# ---------------------------------------------------------------------------

def generate_html(analysis: dict, output_path: str):
    """Generate the HTML report from analysis results."""
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("report.html")

    now = datetime.now(timezone.utc)
    categories_data = []
    for cat_name, cat_cfg in CATEGORIES.items():
        cat_analysis = analysis.get(cat_name, {})
        categories_data.append({
            "name": cat_name,
            "emoji": cat_cfg["emoji"],
            "summary": cat_analysis.get("section_summary", ""),
            "stories": cat_analysis.get("stories", []),
        })

    html = template.render(
        generated_at=now.strftime("%A, %B %d, %Y at %I:%M %p UTC"),
        date_short=now.strftime("%B %d, %Y"),
        date_iso=now.strftime("%Y-%m-%d"),
        categories=categories_data,
        year=now.year,
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written to {output_path}")


# ---------------------------------------------------------------------------
# Archive System
# ---------------------------------------------------------------------------

def archive_digest(output_path: str, docs_dir: str):
    """Copy today's digest to a date-based archive folder and update the archive index."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    year_str = now.strftime("%Y")
    month_str = now.strftime("%m")

    # Create archive directory: docs/archive/YYYY/MM/DD.html
    archive_dir = Path(docs_dir) / "archive" / year_str / month_str
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{date_str}.html"

    shutil.copy2(output_path, archive_path)
    print(f"Archived to {archive_path}")

    # Update archive index
    _update_archive_index(docs_dir)


def _update_archive_index(docs_dir: str):
    """Generate/update the archive index page listing all past digests."""
    archive_root = Path(docs_dir) / "archive"
    if not archive_root.exists():
        return

    # Collect all archived digest files
    entries = []
    for html_file in sorted(archive_root.rglob("*.html"), reverse=True):
        if html_file.name == "index.html":
            continue
        date_str = html_file.stem  # e.g., "2026-03-04"
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            rel_path = html_file.relative_to(Path(docs_dir))
            entries.append({
                "date": date_str,
                "display": date_obj.strftime("%A, %B %d, %Y"),
                "path": str(rel_path),
            })
        except ValueError:
            continue

    # Generate archive index HTML
    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Morning Train — Archive</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800&family=Oswald:wght@300;400&family=Source+Serif+4:wght@400;600&display=swap');
  body {
    font-family: 'Source Serif 4', Georgia, serif;
    background: #1a1510;
    color: #e8dcc8;
    max-width: 700px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
  }
  h1 {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 2rem;
    text-align: center;
    margin-bottom: 0.25rem;
  }
  .tagline {
    font-family: 'Oswald', sans-serif;
    text-align: center;
    color: #c9a84c;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.25em;
    margin-bottom: 2rem;
  }
  .back-link {
    display: block;
    text-align: center;
    margin-bottom: 1.5rem;
  }
  .back-link a {
    font-family: 'Oswald', sans-serif;
    color: #c9a84c;
    text-decoration: none;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    border: 1px solid #6b5a28;
    padding: 0.3rem 0.8rem;
    border-radius: 2px;
  }
  .back-link a:hover { background: #6b5a28; color: #f0e6d0; }
  .archive-list { list-style: none; padding: 0; }
  .archive-list li {
    border-bottom: 1px solid #4a3f32;
    padding: 0.75rem 0;
  }
  .archive-list li:first-child { border-top: 1px solid #4a3f32; }
  .archive-list a {
    font-family: 'Playfair Display', Georgia, serif;
    color: #f0e6d0;
    text-decoration: none;
    font-size: 1rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .archive-list a:hover { color: #c9a84c; }
  .archive-list .date-code {
    font-family: 'Oswald', sans-serif;
    font-size: 0.65rem;
    color: #7a6d5c;
    letter-spacing: 0.08em;
  }
  .empty {
    text-align: center;
    color: #7a6d5c;
    font-style: italic;
    padding: 3rem 0;
  }
</style>
</head>
<body>
  <div class="tagline">Est. MMXXVI &middot; All Stations</div>
  <h1>The Morning Train</h1>
  <div class="tagline">Archive of Past Editions</div>
  <div class="back-link"><a href="../index.html">&larr; Today's Edition</a></div>
"""

    if entries:
        index_html += '  <ul class="archive-list">\n'
        for entry in entries:
            index_html += f'    <li><a href="../{entry["path"]}"><span>{entry["display"]}</span><span class="date-code">{entry["date"]}</span></a></li>\n'
        index_html += '  </ul>\n'
    else:
        index_html += '  <div class="empty">No archived editions yet.</div>\n'

    index_html += """</body>
</html>"""

    archive_index_path = archive_root / "index.html"
    with open(archive_index_path, "w", encoding="utf-8") as f:
        f.write(index_html)
    print(f"Archive index updated at {archive_index_path}")


# ---------------------------------------------------------------------------
# Email Summary
# ---------------------------------------------------------------------------

def generate_email_summary(analysis: dict) -> str:
    """Generate a concise plain-text email summary."""
    now = datetime.now(timezone.utc)
    lines = [
        f"THE MORNING TRAIN — {now.strftime('%A, %B %d, %Y')}",
        "=" * 55,
        "",
    ]

    for cat_name in CATEGORIES:
        cat_analysis = analysis.get(cat_name, {})
        emoji = CATEGORIES[cat_name]["emoji"]
        lines.append(f"{cat_name.upper()}")
        lines.append("-" * 40)

        summary = cat_analysis.get("section_summary", "")
        if summary:
            lines.append(summary)
            lines.append("")

        for story in cat_analysis.get("stories", [])[:3]:
            verification = story.get("verification", "confirmed").upper()
            lines.append(f"  [{verification}] {story.get('headline', 'Untitled')}")
            facts = story.get("key_facts", [])[:2]
            for fact in facts:
                lines.append(f"    - {fact}")
            sources = story.get("sources", [])
            if sources:
                lines.append(f"    Source: {sources[0].get('name', '')} — {sources[0].get('url', '')}")
            lines.append("")

        lines.append("")

    return "\n".join(lines)


def send_email(subject: str, body_text: str, body_html: str, to_email: str):
    """Send email via Resend API."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("[WARN] RESEND_API_KEY not set — skipping email", file=sys.stderr)
        return

    from_email = os.environ.get("RESEND_FROM_EMAIL", "digest@resend.dev")

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "text": body_text,
            "html": body_html,
        },
    )

    if resp.status_code == 200:
        print(f"Email sent to {to_email}")
    else:
        print(f"[WARN] Email send failed: {resp.status_code} {resp.text}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    output_path = os.environ.get("OUTPUT_PATH", "docs/index.html")
    to_email = os.environ.get("DIGEST_EMAIL", "")
    docs_dir = str(Path(output_path).parent)

    print("=" * 55)
    print("THE MORNING TRAIN — DIGEST GENERATOR")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 55)

    # 1. Fetch
    articles = fetch_all_categories()

    total = sum(len(v) for v in articles.values())
    if total == 0:
        print("[ERROR] No articles fetched. Check network/feeds.", file=sys.stderr)
        sys.exit(1)
    print(f"\nTotal articles fetched: {total}\n")

    # 2. Analyze
    analysis = analyze_all(articles)

    # 3. Generate HTML
    generate_html(analysis, output_path)

    # 4. Archive today's digest
    archive_digest(output_path, docs_dir)

    # 5. Send email
    if to_email:
        now = datetime.now(timezone.utc)
        subject = f"The Morning Train — {now.strftime('%b %d, %Y')}"
        text_summary = generate_email_summary(analysis)

        # Read the generated HTML for the email body
        with open(output_path, "r") as f:
            html_body = f.read()

        send_email(subject, text_summary, html_body, to_email)
    else:
        print("[INFO] No DIGEST_EMAIL set — skipping email send")

    print("\nDone!")


if __name__ == "__main__":
    main()
