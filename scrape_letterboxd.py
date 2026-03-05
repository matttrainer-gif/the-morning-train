#!/usr/bin/env python3
"""
Kit Lazer Letterboxd Scraper — Full backfill + incremental sync

Scrapes Kit Lazer's (@moviesaretherapy) complete Letterboxd history:
  1. Ratings pages (all ~3800 rated films with star ratings)
  2. Diary pages (watch dates, rewatch flags, liked status)
  3. Film detail pages (TMDB IDs, genres, posters) — optional enrichment
  4. RSS feed (reviews, precise ratings for recent 50)

Usage:
  # Full backfill (ratings + diary + enrichment)
  python scrape_letterboxd.py

  # Scrape without enrichment (faster, no TMDB/genre/poster)
  python scrape_letterboxd.py --skip-enrich

  # Push existing local catalog to KV
  python scrape_letterboxd.py --push-only

  # Scrape and save locally without pushing
  python scrape_letterboxd.py --no-push
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import cloudscraper
import feedparser
import requests
from bs4 import BeautifulSoup

LETTERBOXD_USER = "moviesrtherapy"
RSS_URL = f"https://letterboxd.com/{LETTERBOXD_USER}/rss/"
CATALOG_PATH = Path(__file__).parent / "data" / "kit-lazer-catalog.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Genre → mood mapping
GENRE_MOODS = {
    "Action": ["intense", "epic"],
    "Adventure": ["epic", "feel_good"],
    "Animation": ["comfort_watch", "feel_good"],
    "Comedy": ["funny", "comfort_watch"],
    "Crime": ["intense", "dark"],
    "Documentary": ["thought_provoking"],
    "Drama": ["thought_provoking"],
    "Family": ["comfort_watch", "feel_good"],
    "Fantasy": ["epic", "mind_bending"],
    "History": ["thought_provoking", "epic"],
    "Horror": ["scary", "intense", "dark"],
    "Music": ["feel_good", "comfort_watch"],
    "Mystery": ["mind_bending", "intense"],
    "Romance": ["romantic", "feel_good"],
    "Science Fiction": ["mind_bending", "epic"],
    "Sci-Fi": ["mind_bending", "epic"],
    "Thriller": ["intense", "dark"],
    "War": ["intense", "epic", "dark"],
    "Western": ["epic"],
    "Noir": ["dark", "intense"],
}


def assign_moods(genres: list[str], rating: float = 0) -> list[str]:
    """Assign mood tags based on genres."""
    moods = set()
    for g in genres:
        for mood in GENRE_MOODS.get(g, []):
            moods.add(mood)
    if rating >= 4.5 and "comfort_watch" not in moods:
        moods.add("feel_good")
    return sorted(moods) if moods else ["chill"]


def create_session() -> cloudscraper.CloudScraper:
    """Create a cloudscraper session that bypasses Cloudflare."""
    session = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "mobile": False}
    )
    return session


# ---------------------------------------------------------------------------
# Step 1: Scrape ratings pages (all rated films)
# ---------------------------------------------------------------------------

def scrape_ratings_page(session, page_num: int) -> list[dict]:
    """Scrape one page of the Letterboxd ratings grid (72 films per page)."""
    url = f"https://letterboxd.com/{LETTERBOXD_USER}/films/ratings/page/{page_num}/"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return []
    except requests.RequestException as e:
        print(f"  [WARN] Ratings page {page_num}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []

    # Each film is a react-component div inside a li.griditem
    films = soup.select("div.react-component[data-item-name]")
    if not films:
        return []

    for film_el in films:
        name_with_year = film_el.get("data-item-name", "")
        slug = film_el.get("data-item-slug", "")
        film_id = film_el.get("data-film-id", "0")

        # Parse "Title (Year)" format
        m = re.match(r"^(.+?)\s*\((\d{4})\)$", name_with_year)
        if m:
            title = m.group(1).strip()
            year = int(m.group(2))
        else:
            title = name_with_year.strip()
            year = 0

        # Find rating from nearest rated-N class
        rating = 0.0
        parent = film_el.parent
        if parent:
            rated_el = parent.select_one("[class*='rated-']")
            if rated_el:
                rc = " ".join(rated_el.get("class", []))
                rm = re.search(r"rated-(\d+)", rc)
                if rm:
                    rating = int(rm.group(1)) / 2.0

        entries.append({
            "slug": slug,
            "film_id": int(film_id) if film_id else 0,
            "title": title,
            "year": year,
            "kit_rating": rating,
        })

    return entries


def scrape_all_ratings(session) -> list[dict]:
    """Scrape all pages of rated films."""
    print("Scraping ratings pages...")
    all_films = []
    page = 1

    while True:
        films = scrape_ratings_page(session, page)
        if not films:
            print(f"  Stopped at page {page} (no entries)")
            break
        all_films.extend(films)
        print(f"  Page {page}: {len(films)} films (total: {len(all_films)})")
        page += 1
        time.sleep(1.0)

    return all_films


# ---------------------------------------------------------------------------
# Step 2: Scrape diary pages (watch dates, rewatches, likes)
# ---------------------------------------------------------------------------

def scrape_diary_page(session, page_num: int) -> list[dict]:
    """Scrape one page of the Letterboxd diary."""
    url = f"https://letterboxd.com/{LETTERBOXD_USER}/films/diary/page/{page_num}/"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return []
    except requests.RequestException as e:
        print(f"  [WARN] Diary page {page_num}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []

    rows = soup.select("tr.diary-entry-row")
    for row in rows:
        try:
            poster = row.select_one("div.react-component[data-item-name]")
            if not poster:
                continue

            name_with_year = poster.get("data-item-name", "")
            slug = poster.get("data-item-slug", "")

            m = re.match(r"^(.+?)\s*\((\d{4})\)$", name_with_year)
            if m:
                title = m.group(1).strip()
                year = int(m.group(2))
            else:
                title = name_with_year.strip()
                year = 0

            # Watch date from calendar links
            watched_date = ""
            month_el = row.select_one("a.month")
            year_el = row.select_one("a.year")
            day_el = row.select_one("a.daydate, td.col-daydate a")
            if month_el and year_el and day_el:
                month_href = month_el.get("href", "")
                dm = re.search(r"/for/(\d{4})/(\d{2})/", month_href)
                day_text = day_el.get_text(strip=True).zfill(2)
                if dm:
                    watched_date = f"{dm.group(1)}-{dm.group(2)}-{day_text}"

            # Rewatch
            rewatch = bool(row.select_one(".icon-rewatch"))

            # Liked
            liked = bool(row.select_one(".icon-liked"))

            # Rating on diary row
            rating = 0.0
            rated_el = row.select_one("[class*='rated-']")
            if rated_el:
                rc = " ".join(rated_el.get("class", []))
                rm = re.search(r"rated-(\d+)", rc)
                if rm:
                    rating = int(rm.group(1)) / 2.0

            entries.append({
                "slug": slug,
                "title": title,
                "year": year,
                "kit_watched_date": watched_date,
                "kit_rewatch": rewatch,
                "kit_liked": liked,
                "kit_rating": rating,
            })
        except Exception as e:
            print(f"  [WARN] Diary row parse error: {e}")
            continue

    return entries


def scrape_all_diary(session) -> list[dict]:
    """Scrape all pages of the diary."""
    print("Scraping diary pages...")
    all_entries = []
    page = 1

    while True:
        entries = scrape_diary_page(session, page)
        if not entries:
            print(f"  Stopped at page {page} (no entries)")
            break
        all_entries.extend(entries)
        print(f"  Page {page}: {len(entries)} entries (total: {len(all_entries)})")
        page += 1
        time.sleep(1.0)

    return all_entries


# ---------------------------------------------------------------------------
# Step 3: Enrich with film detail pages (TMDB ID, genres)
# ---------------------------------------------------------------------------

def enrich_film(session, slug: str, poster_only: bool = False) -> dict:
    """Fetch a film's detail page for TMDB ID, genres, and poster."""
    url = f"https://letterboxd.com/film/{slug}/"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return {}
    except requests.RequestException:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {}

    if not poster_only:
        # TMDB ID from body tag
        body = soup.select_one("body")
        if body:
            tmdb_id = body.get("data-tmdb-id", "")
            if tmdb_id:
                result["tmdb_id"] = int(tmdb_id)

        # Genres (first few, skip the verbose nano-genre descriptions)
        genre_links = soup.select("#tab-genres a.text-slug")
        genres = []
        for g in genre_links:
            text = g.get_text(strip=True)
            if len(text) < 20 and text[0].isupper():
                genres.append(text)
        if genres:
            result["genres"] = genres[:5]

    # Poster from og:image meta tag (most reliable)
    og_image = soup.select_one('meta[property="og:image"]')
    if og_image:
        poster_url = og_image.get("content", "")
        if poster_url and "empty-poster" not in poster_url:
            # Convert crop image to proper poster aspect ratio
            # og:image is square crop, LD+JSON has better poster ratio
            result["poster_url"] = poster_url

    # Try LD+JSON for better poster (portrait aspect ratio)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            text = script.string or ""
            if text.startswith("/*"):
                text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL).strip()
            ld = json.loads(text)
            if ld.get("image") and "empty-poster" not in ld["image"]:
                result["poster_url"] = ld["image"]
                break
        except (json.JSONDecodeError, Exception):
            continue

    return result


def enrich_films(session, films: list[dict], poster_only: bool = False):
    """Enrich films with TMDB IDs, genres, and posters from detail pages."""
    if poster_only:
        to_enrich = [f for f in films if f.get("slug") and not f.get("poster_url")]
    else:
        to_enrich = [f for f in films if f.get("slug") and not f.get("tmdb_id")]
    total = len(to_enrich)
    label = "posters" if poster_only else "TMDB IDs, genres, and posters"
    print(f"Enriching {total} films with {label}...")

    enriched = 0
    errors = 0
    for i, film in enumerate(to_enrich):
        slug = film["slug"]
        detail = enrich_film(session, slug, poster_only=poster_only)
        if detail:
            film.update(detail)
            enriched += 1
        else:
            errors += 1

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{total} (enriched={enriched}, errors={errors})")
            time.sleep(2.0)
        else:
            time.sleep(0.8)

    print(f"  Enrichment complete: {enriched} enriched, {errors} errors")


# ---------------------------------------------------------------------------
# Step 4: Parse RSS feed (reviews + precise ratings for recent 50)
# ---------------------------------------------------------------------------

def parse_rss() -> list[dict]:
    """Parse Letterboxd RSS feed for the 50 most recent entries with rich data."""
    print("Parsing RSS feed...")
    feed = feedparser.parse(RSS_URL)
    movies = []

    for entry in feed.entries:
        film_title = getattr(entry, "letterboxd_filmtitle", None)
        if not film_title:
            title_match = re.match(r"^(.+?),\s*(\d{4})", entry.get("title", ""))
            if not title_match:
                continue
            film_title = title_match.group(1)

        year_str = getattr(entry, "letterboxd_filmyear", "0")
        rating_str = getattr(entry, "letterboxd_memberrating", "0")
        tmdb_str = getattr(entry, "tmdb_movieid", "0")

        # Extract poster from description CDATA
        poster_url = ""
        desc = entry.get("summary", "") or entry.get("description", "")
        img_match = re.search(r'<img\s+src="([^"]+)"', desc)
        if img_match:
            poster_url = img_match.group(1)

        # Extract review text
        review_text = re.sub(r"<[^>]+>", "", desc).strip()
        if review_text.startswith("Watched on"):
            review_text = ""

        movies.append({
            "tmdb_id": int(tmdb_str) if tmdb_str else 0,
            "title": film_title.strip(),
            "year": int(year_str) if year_str else 0,
            "kit_rating": float(rating_str) if rating_str else 0,
            "kit_review": review_text[:500],
            "kit_liked": getattr(entry, "letterboxd_memberlike", "No") == "Yes",
            "kit_rewatch": getattr(entry, "letterboxd_rewatch", "No") == "Yes",
            "kit_watched_date": getattr(entry, "letterboxd_watcheddate", ""),
            "poster_url": poster_url,
            "letterboxd_url": entry.get("link", ""),
        })

    print(f"  Found {len(movies)} movies in RSS")
    return movies


# ---------------------------------------------------------------------------
# Merge and deduplicate
# ---------------------------------------------------------------------------

def merge_all(ratings: list[dict], diary: list[dict], rss: list[dict]) -> list[dict]:
    """Merge ratings (all films + ratings), diary (dates/likes), and RSS (reviews/posters)."""
    # Start with ratings as the base (most comprehensive list)
    by_slug = {}
    for r in ratings:
        slug = r.get("slug", "")
        if slug:
            by_slug[slug] = {
                "slug": slug,
                "film_id": r.get("film_id", 0),
                "title": r["title"],
                "year": r["year"],
                "kit_rating": r.get("kit_rating", 0),
                "tmdb_id": r.get("tmdb_id", 0),
                "genres": r.get("genres", []),
                "genre": "",
                "moods": [],
                "kit_review": "",
                "kit_liked": False,
                "kit_rewatch": False,
                "kit_watched_date": "",
                "availability": "",
                "poster_url": r.get("poster_url", ""),
                "letterboxd_url": f"https://letterboxd.com/film/{slug}/",
                "sources": [{"type": "letterboxd", "url": f"https://letterboxd.com/film/{slug}/"}],
            }

    # Merge diary data (watch dates, likes, rewatches)
    for d in diary:
        slug = d.get("slug", "")
        if slug and slug in by_slug:
            entry = by_slug[slug]
            # Take the most recent watch date
            if d.get("kit_watched_date") and (not entry["kit_watched_date"] or d["kit_watched_date"] > entry["kit_watched_date"]):
                entry["kit_watched_date"] = d["kit_watched_date"]
            if d.get("kit_liked"):
                entry["kit_liked"] = True
            if d.get("kit_rewatch"):
                entry["kit_rewatch"] = True
            # If diary has a rating but ratings page didn't
            if d.get("kit_rating") and not entry.get("kit_rating"):
                entry["kit_rating"] = d["kit_rating"]
        elif slug:
            # Film in diary but not rated — add it
            by_slug[slug] = {
                "slug": slug,
                "film_id": 0,
                "title": d["title"],
                "year": d["year"],
                "kit_rating": d.get("kit_rating", 0),
                "tmdb_id": 0,
                "genres": [],
                "genre": "",
                "moods": [],
                "kit_review": "",
                "kit_liked": d.get("kit_liked", False),
                "kit_rewatch": d.get("kit_rewatch", False),
                "kit_watched_date": d.get("kit_watched_date", ""),
                "availability": "",
                "poster_url": "",
                "letterboxd_url": f"https://letterboxd.com/film/{slug}/",
                "sources": [{"type": "letterboxd", "url": f"https://letterboxd.com/film/{slug}/"}],
            }

    # Merge RSS data (reviews, posters, TMDB IDs)
    for r in rss:
        title_key = f"{r['title'].lower().strip()}|{r['year']}"
        # Find matching slug
        matched_slug = None
        for slug, entry in by_slug.items():
            if f"{entry['title'].lower().strip()}|{entry['year']}" == title_key:
                matched_slug = slug
                break
        if matched_slug:
            entry = by_slug[matched_slug]
            if r.get("kit_review") and not entry.get("kit_review"):
                entry["kit_review"] = r["kit_review"]
            if r.get("poster_url") and not entry.get("poster_url"):
                entry["poster_url"] = r["poster_url"]
            if r.get("tmdb_id") and not entry.get("tmdb_id"):
                entry["tmdb_id"] = r["tmdb_id"]
            if r.get("kit_watched_date") and not entry.get("kit_watched_date"):
                entry["kit_watched_date"] = r["kit_watched_date"]

    # Build final list
    result = list(by_slug.values())

    # Assign genres, moods, IDs
    for i, m in enumerate(result):
        m["id"] = i
        genres = m.get("genres", [])
        if genres:
            m["genre"] = genres[0]
        m["moods"] = assign_moods(genres, m.get("kit_rating", 0))
        m["added_date"] = time.strftime("%Y-%m-%d")
        m["last_updated"] = time.strftime("%Y-%m-%d")
        # Clean up internal fields
        m.pop("slug", None)
        m.pop("film_id", None)
        m.pop("genres", None)

    # Sort by watched date (newest first), then by rating
    result.sort(key=lambda m: (m.get("kit_watched_date", ""), m.get("kit_rating", 0)), reverse=True)

    return result


# ---------------------------------------------------------------------------
# Push to Worker
# ---------------------------------------------------------------------------

def push_to_worker(catalog: list[dict]):
    """Push catalog to Cloudflare Worker KV via sync endpoint."""
    worker_url = os.environ.get("WORKER_URL", "https://morning-train.matttrainer.workers.dev")
    sync_key = os.environ.get("KIT_LAZER_SYNC_KEY", "")

    if not sync_key:
        print("[ERROR] KIT_LAZER_SYNC_KEY not set. Set it as an environment variable.")
        print("  export KIT_LAZER_SYNC_KEY=your-secret-key")
        sys.exit(1)

    print(f"Pushing {len(catalog)} movies to Worker at {worker_url}...")

    batch_size = 100
    total_added = 0
    total_updated = 0

    for i in range(0, len(catalog), batch_size):
        batch = catalog[i:i + batch_size]
        try:
            resp = requests.post(
                f"{worker_url}/kit-lazer/sync",
                json={"movies": batch},
                headers={
                    "Authorization": f"Bearer {sync_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                total_added += data.get("added", 0)
                total_updated += data.get("updated", 0)
                print(f"  Batch {i // batch_size + 1}: added={data.get('added', 0)}, updated={data.get('updated', 0)}")
            else:
                print(f"  [ERROR] Batch {i // batch_size + 1}: HTTP {resp.status_code} — {resp.text[:200]}")
        except Exception as e:
            print(f"  [ERROR] Batch {i // batch_size + 1}: {e}")

    print(f"\nSync complete: {total_added} added, {total_updated} updated")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kit Lazer Letterboxd Scraper")
    parser.add_argument("--push-only", action="store_true", help="Push existing local catalog without scraping")
    parser.add_argument("--no-push", action="store_true", help="Scrape and save locally without pushing to Worker")
    parser.add_argument("--skip-enrich", action="store_true", help="Skip enrichment (TMDB IDs, genres, posters)")
    parser.add_argument("--posters-only", action="store_true", help="Only fetch missing poster images for existing catalog")
    args = parser.parse_args()

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if args.posters_only:
        if not CATALOG_PATH.exists():
            print(f"[ERROR] No local catalog at {CATALOG_PATH}")
            sys.exit(1)
        with open(CATALOG_PATH) as f:
            catalog = json.load(f)
        print(f"Loaded {len(catalog)} movies from local catalog")

        # Re-add slugs from letterboxd URLs for enrichment
        for m in catalog:
            if not m.get("slug") and m.get("letterboxd_url"):
                slug_match = re.search(r"/film/([^/]+)/?", m["letterboxd_url"])
                if slug_match:
                    m["slug"] = slug_match.group(1)

        session = create_session()
        enrich_films(session, catalog, poster_only=True)

        # Clean up slugs before saving
        for m in catalog:
            m.pop("slug", None)

        with open(CATALOG_PATH, "w") as f:
            json.dump(catalog, f, indent=2)
        print(f"\nSaved to {CATALOG_PATH}")
        print(f"With posters: {sum(1 for m in catalog if m.get('poster_url'))}")

        if not args.no_push:
            push_to_worker(catalog)
        return

    if args.push_only:
        if not CATALOG_PATH.exists():
            print(f"[ERROR] No local catalog at {CATALOG_PATH}")
            sys.exit(1)
        with open(CATALOG_PATH) as f:
            catalog = json.load(f)
        print(f"Loaded {len(catalog)} movies from local catalog")
        push_to_worker(catalog)
        return

    session = create_session()
    print("Session initialized\n")

    # Step 1: Scrape all rated films
    ratings = scrape_all_ratings(session)

    # Step 2: Scrape diary (watch dates, likes, rewatches)
    diary = scrape_all_diary(session)

    # Step 3: Parse RSS (reviews, posters, TMDB IDs for recent 50)
    rss = parse_rss()

    # Step 4: Enrich with TMDB IDs and genres (optional, slow)
    if not args.skip_enrich:
        enrich_films(session, ratings)

    # Step 5: Merge everything
    catalog = merge_all(ratings, diary, rss)

    print(f"\nFinal catalog: {len(catalog)} unique movies")
    print(f"  With ratings: {sum(1 for m in catalog if m.get('kit_rating', 0) > 0)}")
    print(f"  With TMDB IDs: {sum(1 for m in catalog if m.get('tmdb_id', 0) > 0)}")
    print(f"  With genres: {sum(1 for m in catalog if m.get('genre'))}")
    print(f"  With posters: {sum(1 for m in catalog if m.get('poster_url'))}")
    print(f"  With watch dates: {sum(1 for m in catalog if m.get('kit_watched_date'))}")

    # Step 6: Save locally
    with open(CATALOG_PATH, "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"\nSaved to {CATALOG_PATH}")

    # Step 7: Push to Worker
    if not args.no_push:
        push_to_worker(catalog)
    else:
        print("[INFO] Skipping push (--no-push)")


if __name__ == "__main__":
    main()
