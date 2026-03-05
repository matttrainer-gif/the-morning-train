#!/usr/bin/env python3
"""
Kit Lazer Letterboxd Scraper — One-time backfill + incremental sync

Scrapes Kit Lazer's (@moviesaretherapy) full Letterboxd diary,
merges with RSS data for richer fields, assigns mood tags,
and pushes the catalog to the Cloudflare Worker KV.

Usage:
  # Full backfill (scrapes all diary pages)
  python scrape_letterboxd.py

  # Push existing local catalog to KV
  python scrape_letterboxd.py --push-only

  # Scrape and save locally without pushing
  python scrape_letterboxd.py --no-push
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

LETTERBOXD_USER = "moviesrtherapy"
RSS_URL = f"https://letterboxd.com/{LETTERBOXD_USER}/rss/"
DIARY_URL = f"https://letterboxd.com/{LETTERBOXD_USER}/films/diary/page/{{page}}/"
CATALOG_PATH = Path(__file__).parent / "data" / "kit-lazer-catalog.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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


def assign_moods(genre: str, rating: float = 0) -> list[str]:
    """Assign mood tags based on genre."""
    moods = set()
    for g in [genre]:
        for mood in GENRE_MOODS.get(g, []):
            moods.add(mood)
    # High-rated films get "comfort_watch" as a bonus
    if rating >= 4.5 and "comfort_watch" not in moods:
        moods.add("feel_good")
    return sorted(moods) if moods else ["chill"]


def parse_rss() -> list[dict]:
    """Parse Letterboxd RSS feed for the 50 most recent entries with rich data."""
    print("Parsing RSS feed...")
    feed = feedparser.parse(RSS_URL)
    movies = []

    for entry in feed.entries:
        # Skip non-film entries (lists, etc.)
        film_title = getattr(entry, "letterboxd_filmtitle", None)
        if not film_title:
            # Try parsing from title
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
        # Remove "Watched on ..." default text
        if review_text.startswith("Watched on"):
            review_text = ""

        movie = {
            "tmdb_id": int(tmdb_str) if tmdb_str else 0,
            "title": film_title.strip(),
            "year": int(year_str) if year_str else 0,
            "genre": "",  # RSS doesn't have genre, will be enriched later
            "moods": [],
            "kit_rating": float(rating_str) if rating_str else 0,
            "kit_review": review_text[:500],
            "kit_liked": getattr(entry, "letterboxd_memberlike", "No") == "Yes",
            "kit_rewatch": getattr(entry, "letterboxd_rewatch", "No") == "Yes",
            "kit_watched_date": getattr(entry, "letterboxd_watcheddate", ""),
            "availability": "",
            "poster_url": poster_url,
            "letterboxd_url": entry.get("link", ""),
            "sources": [{"type": "letterboxd", "url": entry.get("link", "")}],
        }
        movies.append(movie)

    print(f"  Found {len(movies)} movies in RSS")
    return movies


def scrape_diary_page(page_num: int) -> list[dict]:
    """Scrape one page of the Letterboxd diary."""
    url = DIARY_URL.format(page=page_num)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            print(f"  [WARN] Page {page_num}: HTTP {resp.status_code}")
            return []
    except requests.RequestException as e:
        print(f"  [WARN] Page {page_num}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []

    # Letterboxd diary uses table rows with class "diary-entry-row"
    rows = soup.select("tr.diary-entry-row")
    if not rows:
        # Try alternate selectors
        rows = soup.select(".diary-entry-row")

    for row in rows:
        try:
            # Film title
            title_el = row.select_one("td.td-film-details h3 a, .headline-3 a, h3.headline-3 a")
            if not title_el:
                title_el = row.select_one("a[href*='/film/']")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            film_slug = title_el.get("href", "")

            # Year
            year_el = row.select_one("td.td-released, .td-released")
            year = 0
            if year_el:
                year_text = year_el.get_text(strip=True)
                if year_text.isdigit():
                    year = int(year_text)

            # Rating (count star elements)
            rating = 0.0
            rating_el = row.select_one("td.td-rating .rating, .td-rating .rating")
            if rating_el:
                # Stars are often encoded as a class like "rated-8" (meaning 4 stars, each star = 2)
                rating_class = " ".join(rating_el.get("class", []))
                rated_match = re.search(r"rated-(\d+)", rating_class)
                if rated_match:
                    rating = int(rated_match.group(1)) / 2.0
                else:
                    # Count star spans
                    stars = rating_el.select(".star")
                    if stars:
                        rating = len(stars)

            # Watched date
            date_el = row.select_one("td.td-calendar a, .td-calendar a")
            watched_date = ""
            if date_el:
                href = date_el.get("href", "")
                # Extract date from URL like /moviesrtherapy/films/diary/for/2026/03/04/
                date_match = re.search(r"/for/(\d{4})/(\d{2})/(\d{2})/", href)
                if date_match:
                    watched_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"

            # Rewatch indicator
            rewatch = bool(row.select_one(".icon-rewatch, .td-rewatch .icon-status-off") is None and row.select_one(".td-rewatch .icon-status-on"))

            # Liked
            liked = bool(row.select_one(".icon-liked, .like-link-target.icon-liked"))

            # Build letterboxd URL
            letterboxd_url = ""
            if film_slug:
                letterboxd_url = f"https://letterboxd.com{film_slug}" if film_slug.startswith("/") else film_slug

            entries.append({
                "tmdb_id": 0,  # Not available from diary scrape
                "title": title,
                "year": year,
                "genre": "",
                "moods": [],
                "kit_rating": rating,
                "kit_review": "",
                "kit_liked": liked,
                "kit_rewatch": rewatch,
                "kit_watched_date": watched_date,
                "availability": "",
                "poster_url": "",
                "letterboxd_url": letterboxd_url,
                "sources": [{"type": "letterboxd", "url": letterboxd_url}],
            })
        except Exception as e:
            print(f"  [WARN] Failed to parse row: {e}")
            continue

    return entries


def scrape_full_diary() -> list[dict]:
    """Scrape all pages of the Letterboxd diary."""
    print("Scraping Letterboxd diary pages...")
    all_entries = []
    page = 1

    while True:
        entries = scrape_diary_page(page)
        if not entries:
            print(f"  Stopped at page {page} (no entries)")
            break
        all_entries.extend(entries)
        print(f"  Page {page}: {len(entries)} entries (total: {len(all_entries)})")
        page += 1
        time.sleep(1.5)  # Be polite

    return all_entries


def merge_catalogs(rss_movies: list[dict], diary_movies: list[dict]) -> list[dict]:
    """Merge RSS data (rich) with diary data (comprehensive). RSS wins for overlapping entries."""
    # Index RSS by title+year for matching
    rss_index = {}
    for m in rss_movies:
        key = f"{m['title'].lower().strip()}|{m['year']}"
        rss_index[key] = m

    merged = list(rss_movies)  # Start with RSS entries
    added_keys = set(rss_index.keys())

    for dm in diary_movies:
        key = f"{dm['title'].lower().strip()}|{dm['year']}"
        if key in added_keys:
            # RSS already has this entry — merge any missing data
            existing = rss_index[key]
            if not existing.get("kit_watched_date") and dm.get("kit_watched_date"):
                existing["kit_watched_date"] = dm["kit_watched_date"]
            if not existing.get("kit_rating") and dm.get("kit_rating"):
                existing["kit_rating"] = dm["kit_rating"]
        else:
            merged.append(dm)
            added_keys.add(key)

    return merged


def deduplicate(movies: list[dict]) -> list[dict]:
    """Remove duplicate entries, keeping the one with the most data."""
    seen = {}
    for m in movies:
        key = f"{m['title'].lower().strip()}|{m['year']}"
        if key in seen:
            existing = seen[key]
            # Keep the entry with more data (higher tmdb_id, longer review, poster)
            if (m.get("tmdb_id", 0) > existing.get("tmdb_id", 0) or
                len(m.get("kit_review", "")) > len(existing.get("kit_review", "")) or
                (m.get("poster_url") and not existing.get("poster_url"))):
                # Merge fields from existing into m
                if not m.get("poster_url") and existing.get("poster_url"):
                    m["poster_url"] = existing["poster_url"]
                if not m.get("tmdb_id") and existing.get("tmdb_id"):
                    m["tmdb_id"] = existing["tmdb_id"]
                if not m.get("kit_review") and existing.get("kit_review"):
                    m["kit_review"] = existing["kit_review"]
                seen[key] = m
        else:
            seen[key] = m

    result = list(seen.values())
    # Assign IDs and mood tags
    for i, m in enumerate(result):
        m["id"] = i
        if not m.get("moods"):
            m["moods"] = assign_moods(m.get("genre", ""), m.get("kit_rating", 0))
        m["added_date"] = m.get("added_date", time.strftime("%Y-%m-%d"))
        m["last_updated"] = time.strftime("%Y-%m-%d")

    # Sort by watched date (newest first), then by rating
    result.sort(key=lambda m: (m.get("kit_watched_date", ""), m.get("kit_rating", 0)), reverse=True)

    return result


def push_to_worker(catalog: list[dict]):
    """Push catalog to Cloudflare Worker KV via sync endpoint."""
    worker_url = os.environ.get("WORKER_URL", "https://morning-train.matttrainer.workers.dev")
    sync_key = os.environ.get("KIT_LAZER_SYNC_KEY", "")

    if not sync_key:
        print("[ERROR] KIT_LAZER_SYNC_KEY not set. Set it as an environment variable.")
        print("  export KIT_LAZER_SYNC_KEY=your-secret-key")
        sys.exit(1)

    print(f"Pushing {len(catalog)} movies to Worker at {worker_url}...")

    # Push in batches of 100 to avoid huge payloads
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


def main():
    parser = argparse.ArgumentParser(description="Kit Lazer Letterboxd Scraper")
    parser.add_argument("--push-only", action="store_true", help="Push existing local catalog without scraping")
    parser.add_argument("--no-push", action="store_true", help="Scrape and save locally without pushing to Worker")
    args = parser.parse_args()

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if args.push_only:
        if not CATALOG_PATH.exists():
            print(f"[ERROR] No local catalog at {CATALOG_PATH}")
            sys.exit(1)
        with open(CATALOG_PATH) as f:
            catalog = json.load(f)
        print(f"Loaded {len(catalog)} movies from local catalog")
        push_to_worker(catalog)
        return

    # Step 1: Parse RSS (rich data for recent 50)
    rss_movies = parse_rss()

    # Step 2: Scrape full diary (all history)
    diary_movies = scrape_full_diary()

    # Step 3: Merge and deduplicate
    merged = merge_catalogs(rss_movies, diary_movies)
    catalog = deduplicate(merged)

    print(f"\nFinal catalog: {len(catalog)} unique movies")
    print(f"  With ratings: {sum(1 for m in catalog if m.get('kit_rating', 0) > 0)}")
    print(f"  With TMDB IDs: {sum(1 for m in catalog if m.get('tmdb_id', 0) > 0)}")
    print(f"  With posters: {sum(1 for m in catalog if m.get('poster_url'))}")

    # Step 4: Save locally
    with open(CATALOG_PATH, "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"\nSaved to {CATALOG_PATH}")

    # Step 5: Push to Worker
    if not args.no_push:
        push_to_worker(catalog)
    else:
        print("[INFO] Skipping push (--no-push)")


if __name__ == "__main__":
    main()
