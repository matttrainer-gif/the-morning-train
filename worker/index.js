/**
 * Cloudflare Worker — The Morning Train
 *
 * Endpoints:
 *   GET  /              — Wire Room: fetches live RSS headlines
 *   POST /query         — Inquiry Desk: deep-dives into a story via Claude API
 *   GET  /prefs/:token  — Get user preferences (personalization)
 *   POST /prefs/:token  — Save user preferences (personalization)
 *   POST /track/:token  — Track reading engagement (personalization)
 *
 * Kit Lazer Picks:
 *   POST /kit-lazer/sync              — Admin: push movies to catalog (auth required)
 *   GET  /kit-lazer/catalog           — Public: full movie catalog
 *   GET  /kit-lazer/moods             — Public: mood index
 *   POST /kit-lazer/recommend         — Public: AI movie recommendations (Claude)
 *   GET  /kit-lazer/user/:token       — Public: user watch profile
 *   POST /kit-lazer/user/:token/watched — Public: mark movie watched + rate
 *
 * Deploy: `npx wrangler deploy`
 * Secrets needed: ANTHROPIC_API_KEY, KIT_LAZER_SYNC_KEY
 * KV namespace: PREFS
 */

// ---------------------------------------------------------------------------
// Feed Configuration
// ---------------------------------------------------------------------------

const LIVE_FEEDS = {
  breaking: [
    { name: "AP News – Top", url: "https://rsshub.app/apnews/topics/apf-topnews" },
    { name: "Reuters – Top", url: "https://www.reutersagency.com/feed/?best-topics=top-news&post_type=best" },
    { name: "BBC – Breaking", url: "https://feeds.bbci.co.uk/news/rss.xml" },
    { name: "NPR – News Now", url: "https://feeds.npr.org/1001/rss.xml" },
    { name: "Fox News – Latest", url: "https://moxie.foxnews.com/google-publisher/latest.xml" },
    { name: "WSJ – US News", url: "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml" },
  ],
  politics: [
    { name: "AP – Politics", url: "https://rsshub.app/apnews/topics/politics" },
    { name: "The Hill", url: "https://thehill.com/feed/" },
    { name: "RealClearPolitics", url: "https://www.realclearpolitics.com/index.xml" },
    { name: "Politico", url: "https://www.politico.com/rss/politicopicks.xml" },
    { name: "BBC – US/Canada", url: "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml" },
    { name: "National Review", url: "https://www.nationalreview.com/feed/" },
    { name: "The Dispatch", url: "https://thedispatch.com/feed/" },
    { name: "Fox News – Politics", url: "https://moxie.foxnews.com/google-publisher/politics.xml" },
    { name: "Reason", url: "https://reason.com/feed/" },
    { name: "The Free Press", url: "https://www.thefp.com/feed" },
  ],
  world: [
    { name: "AP – World", url: "https://rsshub.app/apnews/topics/world-news" },
    { name: "Reuters – World", url: "https://www.reutersagency.com/feed/?best-topics=world&post_type=best" },
    { name: "BBC – World", url: "https://feeds.bbci.co.uk/news/world/rss.xml" },
    { name: "Al Jazeera", url: "https://www.aljazeera.com/xml/rss/all.xml" },
    { name: "WSJ – World", url: "https://feeds.a.dj.com/rss/RSSWorldNews.xml" },
  ],
  finance: [
    { name: "CNBC – Markets", url: "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258" },
    { name: "MarketWatch", url: "https://feeds.marketwatch.com/marketwatch/topstories/" },
    { name: "Yahoo Finance", url: "https://finance.yahoo.com/news/rssindex" },
    { name: "WSJ – Markets", url: "https://feeds.a.dj.com/rss/RSSMarketsMain.xml" },
  ],
  tech: [
    { name: "TechCrunch", url: "https://techcrunch.com/feed/" },
    { name: "The Verge", url: "https://www.theverge.com/rss/index.xml" },
    { name: "Ars Technica", url: "https://feeds.arstechnica.com/arstechnica/index" },
    { name: "Reason – Tech", url: "https://reason.com/tag/technology/feed/" },
  ],
  reddit: [
    { name: "r/news", url: "https://www.reddit.com/r/news/top/.rss?t=day&limit=10" },
    { name: "r/worldnews", url: "https://www.reddit.com/r/worldnews/top/.rss?t=day&limit=10" },
    { name: "r/technology", url: "https://www.reddit.com/r/technology/top/.rss?t=day&limit=10" },
    { name: "r/politics", url: "https://www.reddit.com/r/politics/top/.rss?t=day&limit=8" },
    { name: "r/conservative", url: "https://www.reddit.com/r/conservative/top/.rss?t=day&limit=8" },
    { name: "r/libertarian", url: "https://www.reddit.com/r/libertarian/top/.rss?t=day&limit=8" },
  ],
};

// ---------------------------------------------------------------------------
// RSS Parser
// ---------------------------------------------------------------------------

function parseRSSItems(xmlText, sourceName) {
  const items = [];
  const itemRegex = /<(?:item|entry)[\s>]([\s\S]*?)<\/(?:item|entry)>/gi;
  let match;

  while ((match = itemRegex.exec(xmlText)) !== null) {
    const block = match[1];
    const title = extractTag(block, "title");
    const link = extractLink(block);
    const pubDate = extractTag(block, "pubDate") || extractTag(block, "published") || extractTag(block, "updated") || "";
    const description = stripHTML(extractTag(block, "description") || extractTag(block, "summary") || extractTag(block, "content") || "");

    if (title) {
      items.push({
        title: stripHTML(title).trim(),
        link: (link || "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">"),
        source: sourceName,
        published: pubDate,
        timestamp: pubDate ? new Date(pubDate).getTime() : 0,
        summary: description.slice(0, 300).trim(),
      });
    }
  }
  return items;
}

function extractTag(block, tagName) {
  const cdataRegex = new RegExp(`<${tagName}[^>]*>\\s*<!\\[CDATA\\[([\\s\\S]*?)\\]\\]>\\s*</${tagName}>`, "i");
  const cdataMatch = block.match(cdataRegex);
  if (cdataMatch) return cdataMatch[1];
  const regex = new RegExp(`<${tagName}[^>]*>([\\s\\S]*?)</${tagName}>`, "i");
  const match = block.match(regex);
  return match ? match[1] : null;
}

function extractLink(block) {
  const atomLink = block.match(/<link[^>]+href=["']([^"']+)["'][^>]*\/?>/i);
  if (atomLink) return atomLink[1];
  return extractTag(block, "link") || "";
}

function stripHTML(str) {
  return (str || "")
    .replace(/<!\[CDATA\[|\]\]>/g, "")       // Strip CDATA wrappers
    .replace(/<br\s*\/?>/gi, " ")             // <br> → space
    .replace(/<\/p>\s*<p[^>]*>/gi, " ")       // </p><p> → space
    .replace(/<[^>]+>/g, "")                  // Strip all HTML tags
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&mdash;/g, "\u2014")
    .replace(/&ndash;/g, "\u2013")
    .replace(/&ldquo;|&rdquo;/g, '"')
    .replace(/&lsquo;|&rsquo;/g, "'")
    .replace(/&hellip;/g, "\u2026")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(parseInt(n, 10)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/\s+/g, " ")
    .trim();
}

// ---------------------------------------------------------------------------
// Feed Fetching
// ---------------------------------------------------------------------------

async function fetchFeed(feed, signal) {
  try {
    const resp = await fetch(feed.url, {
      headers: { "User-Agent": "TheMorningTrain-Wire/1.0" },
      signal,
    });
    if (!resp.ok) return [];
    const text = await resp.text();
    return parseRSSItems(text, feed.name);
  } catch (e) {
    console.error(`Failed: ${feed.name} — ${e.message}`);
    return [];
  }
}

async function fetchCategory(feeds) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const results = await Promise.allSettled(
      feeds.map((f) => fetchFeed(f, controller.signal))
    );
    const items = results
      .filter((r) => r.status === "fulfilled")
      .flatMap((r) => r.value);
    items.sort((a, b) => b.timestamp - a.timestamp);
    return dedup(items);
  } finally {
    clearTimeout(timeout);
  }
}

function dedup(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = item.title.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 40);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// ---------------------------------------------------------------------------
// Cron Failure Alert — email via Resend if digest dispatch fails
// ---------------------------------------------------------------------------

async function cronAlert(env, message) {
  const apiKey = env.RESEND_API_KEY;
  const to = env.DIGEST_EMAIL;
  const from = env.RESEND_FROM_EMAIL || "digest@resend.dev";
  if (!apiKey || !to) {
    console.error(`cronAlert (no email config): ${message}`);
    return;
  }
  try {
    await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from,
        to: [to],
        subject: "Morning Train — Cron Alert",
        text: `The Morning Train daily cron trigger failed.\n\n${message}\n\nCheck: https://github.com/matttrainer-gif/the-morning-train/actions`,
      }),
    });
  } catch (e) {
    console.error(`cronAlert email failed: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// Inquiry Desk — Claude-powered story deep-dive
// ---------------------------------------------------------------------------

const INQUIRY_SYSTEM_PROMPT = `You are an investigative research assistant for The Morning Train, a centrist daily news digest. The reader is looking at a story in their digest and wants to dig deeper.

YOUR ROLE:
- Help the reader understand the underlying truth of the story
- Separate confirmed facts from disputed claims, spin, and opinion
- Provide historical context and background the summary may have omitted
- Identify what's NOT being said — blind spots, missing perspectives, omitted data
- When relevant, note how the left and right interpret the same facts differently
- If the reader asks a specific question, answer it directly and thoroughly
- Cite your reasoning — explain HOW you know something, not just what you think

STYLE:
- Be direct, substantive, and analytical
- Write in clear prose paragraphs, not bullet points
- If you're uncertain about something, say so explicitly
- Don't hedge excessively — take positions when the evidence warrants it
- Treat the reader as intelligent and well-informed

BALANCE:
- Apply the same centrist, fact-first stance as the digest itself
- Don't default to left-leaning framing as neutral
- Present strong arguments from both sides when they exist
- When one side has the facts, say so clearly`;

async function handleQuery(request, env) {
  const apiKey = env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return { error: "ANTHROPIC_API_KEY not configured. Add it as a Worker secret." };
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return { error: "Invalid JSON body" };
  }

  const { headline, analysis, key_facts, sources, question, conversation } = body;

  if (!headline || !question) {
    return { error: "Missing required fields: headline, question" };
  }

  // Build the story context
  let storyContext = `STORY HEADLINE: ${headline}\n`;
  if (analysis) storyContext += `\nDIGEST ANALYSIS:\n${analysis}\n`;
  if (key_facts && key_facts.length) storyContext += `\nKEY FACTS:\n${key_facts.map(f => `- ${f}`).join("\n")}\n`;
  if (sources && sources.length) storyContext += `\nSOURCES: ${sources.map(s => s.name || s).join(", ")}\n`;

  // Build messages — support multi-turn conversation
  const messages = [];

  // Add conversation history if present
  if (conversation && Array.isArray(conversation)) {
    for (const turn of conversation) {
      messages.push({ role: turn.role, content: turn.content });
    }
  }

  // Add the new question
  const userMessage = messages.length === 0
    ? `Here is a story from today's Morning Train digest:\n\n${storyContext}\n\nMy question: ${question}`
    : question;

  messages.push({ role: "user", content: userMessage });

  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514",
        max_tokens: 1500,
        system: INQUIRY_SYSTEM_PROMPT,
        messages,
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      console.error(`Claude API error: ${resp.status} ${errText}`);
      return { error: `Claude API returned ${resp.status}` };
    }

    const data = await resp.json();
    const reply = data.content?.[0]?.text || "No response received.";

    return {
      reply,
      usage: {
        input_tokens: data.usage?.input_tokens || 0,
        output_tokens: data.usage?.output_tokens || 0,
      },
    };
  } catch (e) {
    console.error(`Query error: ${e.message}`);
    return { error: `Failed to reach Claude API: ${e.message}` };
  }
}

// ---------------------------------------------------------------------------
// Personalization — Cloudflare KV based reading preferences
// ---------------------------------------------------------------------------

/**
 * Preferences stored per user token:
 * {
 *   category_weights: { "AI & Technology": 1.2, "US Politics": 1.5, ... },
 *   read_history: [ { category: "...", timestamp: ..., story_headline: "..." }, ... ],
 *   created_at: "ISO date",
 *   updated_at: "ISO date"
 * }
 */

const DEFAULT_PREFS = {
  category_weights: {
    "AI & Technology": 1.0,
    "US Politics": 1.0,
    "World Politics": 1.0,
    "Financial Analysis": 1.0,
    "Movies & Shows": 1.0,
    "PC & PS5 Gaming": 1.0,
    "Podcast Commentary": 1.0,
  },
  read_history: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

async function handleGetPrefs(token, env) {
  if (!env.PREFS) {
    return { error: "KV namespace PREFS not bound. Add it to wrangler.toml." };
  }
  const prefs = await env.PREFS.get(`user:${token}`, { type: "json" });
  return prefs || { ...DEFAULT_PREFS, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
}

async function handleSavePrefs(token, request, env) {
  if (!env.PREFS) {
    return { error: "KV namespace PREFS not bound. Add it to wrangler.toml." };
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return { error: "Invalid JSON body" };
  }

  // Merge with existing prefs
  const existing = await env.PREFS.get(`user:${token}`, { type: "json" }) || { ...DEFAULT_PREFS };

  if (body.category_weights) {
    existing.category_weights = { ...existing.category_weights, ...body.category_weights };
  }

  existing.updated_at = new Date().toISOString();

  await env.PREFS.put(`user:${token}`, JSON.stringify(existing));
  return existing;
}

async function handleTrackRead(token, request, env) {
  if (!env.PREFS) {
    return { error: "KV namespace PREFS not bound." };
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return { error: "Invalid JSON body" };
  }

  const { category, story_headline } = body;
  if (!category) {
    return { error: "Missing required field: category" };
  }

  const existing = await env.PREFS.get(`user:${token}`, { type: "json" }) || { ...DEFAULT_PREFS };

  // Add to read history (keep last 200 entries)
  existing.read_history = existing.read_history || [];
  existing.read_history.unshift({
    category,
    story_headline: story_headline || "",
    timestamp: new Date().toISOString(),
  });
  existing.read_history = existing.read_history.slice(0, 200);

  // Recalculate category weights based on reading frequency
  const catCounts = {};
  const totalReads = existing.read_history.length;
  for (const entry of existing.read_history) {
    catCounts[entry.category] = (catCounts[entry.category] || 0) + 1;
  }

  // Weight = base 1.0 + (0.5 * frequency ratio above average)
  const avgFreq = totalReads / 7; // 7 categories
  for (const [cat, count] of Object.entries(catCounts)) {
    const ratio = count / Math.max(avgFreq, 1);
    existing.category_weights[cat] = Math.round((0.5 + ratio * 0.5) * 100) / 100;
  }

  existing.updated_at = new Date().toISOString();
  await env.PREFS.put(`user:${token}`, JSON.stringify(existing));

  return { ok: true, weights: existing.category_weights };
}

// ---------------------------------------------------------------------------
// Kit Lazer Picks — Movie recommendation database
// ---------------------------------------------------------------------------

const KIT_LAZER_RECOMMEND_PROMPT = `You are a movie recommendation assistant powered by Kit Lazer's (@moviesaretherapy) complete review catalog.

You have access to Kit Lazer's FULL database of rated and reviewed films. Use this to:
- Recommend movies based on the user's mood, preferences, or situation
- Compare the user's taste profile with Kit Lazer's ratings
- Explain WHY Kit Lazer loved or disliked specific films
- Suggest deep cuts and hidden gems from his catalog

When recommending, always include:
- The film title and year
- Kit Lazer's rating (X/5 stars)
- A brief explanation of why it fits what they're looking for
- Where to watch it (if known)

Be conversational and enthusiastic about film. Channel Kit Lazer's passion for cinema.
If the user has a taste profile, factor in their agreement/disagreement patterns with Kit Lazer.
Keep responses concise — 2-4 movie recommendations per response unless asked for more.`;

async function handleKitLazerSync(request, env) {
  // Verify admin secret
  const authHeader = request.headers.get("Authorization");
  if (!env.KIT_LAZER_SYNC_KEY || authHeader !== `Bearer ${env.KIT_LAZER_SYNC_KEY}`) {
    return { error: "Unauthorized" };
  }
  if (!env.PREFS) return { error: "KV not bound" };

  let body;
  try { body = await request.json(); } catch (e) { return { error: "Invalid JSON" }; }

  const newMovies = body.movies || [];
  if (!newMovies.length) return { error: "No movies provided" };

  // Load existing catalog
  const existing = await env.PREFS.get("catalog:movies", { type: "json" }) || [];

  // Build indexes for dedup
  const tmdbIndex = {};
  const titleIndex = {};
  existing.forEach((m, i) => {
    if (m.tmdb_id) tmdbIndex[m.tmdb_id] = i;
    titleIndex[`${(m.title || "").toLowerCase().trim()}|${m.year}`] = i;
  });

  let added = 0, updated = 0;

  for (const movie of newMovies) {
    // Check TMDB ID first, then title+year
    let existingIdx = (movie.tmdb_id && movie.tmdb_id > 0) ? tmdbIndex[movie.tmdb_id] : undefined;
    if (existingIdx === undefined) {
      const key = `${(movie.title || "").toLowerCase().trim()}|${movie.year}`;
      existingIdx = titleIndex[key];
    }

    if (existingIdx !== undefined) {
      // Merge: update if newer data
      const ex = existing[existingIdx];
      if (movie.kit_rating && (!ex.kit_rating || movie.kit_watched_date > (ex.kit_watched_date || ""))) {
        ex.kit_rating = movie.kit_rating;
        if (movie.kit_review) ex.kit_review = movie.kit_review;
        ex.kit_rewatch = true;
      }
      if (movie.poster_url && !ex.poster_url) ex.poster_url = movie.poster_url;
      if (movie.tmdb_id && !ex.tmdb_id) ex.tmdb_id = movie.tmdb_id;
      if (movie.genre && !ex.genre) ex.genre = movie.genre;
      if (movie.moods && movie.moods.length > 0) {
        // Update moods if incoming has better data (more specific than "chill")
        const incomingHasReal = movie.moods.some(m => m !== "chill");
        const existingHasReal = ex.moods && ex.moods.some(m => m !== "chill");
        if (incomingHasReal || !existingHasReal) ex.moods = movie.moods;
      }
      if (movie.availability && !ex.availability) ex.availability = movie.availability;
      // Merge sources
      const existingUrls = new Set((ex.sources || []).map(s => s.url));
      for (const src of (movie.sources || [])) {
        if (src.url && !existingUrls.has(src.url)) {
          ex.sources = ex.sources || [];
          ex.sources.push(src);
        }
      }
      ex.last_updated = new Date().toISOString().slice(0, 10);
      updated++;
    } else {
      // New entry
      movie.id = existing.length;
      movie.added_date = movie.added_date || new Date().toISOString().slice(0, 10);
      movie.last_updated = new Date().toISOString().slice(0, 10);
      existing.push(movie);
      // Update indexes
      if (movie.tmdb_id) tmdbIndex[movie.tmdb_id] = movie.id;
      titleIndex[`${(movie.title || "").toLowerCase().trim()}|${movie.year}`] = movie.id;
      added++;
    }
  }

  // Reassign IDs
  existing.forEach((m, i) => { m.id = i; });

  // Save catalog
  await env.PREFS.put("catalog:movies", JSON.stringify(existing));

  // Rebuild mood index
  const moodIndex = {};
  existing.forEach((m, i) => {
    for (const mood of (m.moods || [])) {
      if (!moodIndex[mood]) moodIndex[mood] = [];
      moodIndex[mood].push(i);
    }
  });
  await env.PREFS.put("index:moods", JSON.stringify(moodIndex));

  // Update metadata
  await env.PREFS.put("catalog:meta", JSON.stringify({
    last_updated: new Date().toISOString(),
    total_count: existing.length,
    last_sync: { added, updated },
  }));

  return { ok: true, added, updated, total: existing.length };
}

async function handleKitLazerCatalog(env) {
  if (!env.PREFS) return { error: "KV not bound" };
  const catalog = await env.PREFS.get("catalog:movies", { type: "json" });
  return catalog || [];
}

async function handleKitLazerMoods(env) {
  if (!env.PREFS) return { error: "KV not bound" };
  const moods = await env.PREFS.get("index:moods", { type: "json" });
  return moods || {};
}

async function handleKitLazerGetUser(token, env) {
  if (!env.PREFS) return { error: "KV not bound" };
  const profile = await env.PREFS.get(`kl-user:${token}`, { type: "json" });
  return profile || {
    watched: [],
    taste_profile: { genre_affinity: {}, mood_affinity: {}, avg_rating_delta: 0, overlap_score: 0 },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

async function handleKitLazerMarkWatched(token, request, env) {
  if (!env.PREFS) return { error: "KV not bound" };

  let body;
  try { body = await request.json(); } catch (e) { return { error: "Invalid JSON" }; }

  const { movie_id, personal_rating } = body;
  if (movie_id === undefined) return { error: "Missing movie_id" };

  const profile = await env.PREFS.get(`kl-user:${token}`, { type: "json" }) || {
    watched: [],
    taste_profile: { genre_affinity: {}, mood_affinity: {}, avg_rating_delta: 0, overlap_score: 0 },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  // Check if already watched — update rating if so
  const existingIdx = profile.watched.findIndex(w => w.movie_id === movie_id);
  if (existingIdx >= 0) {
    if (personal_rating !== undefined) profile.watched[existingIdx].personal_rating = personal_rating;
    profile.watched[existingIdx].watched_date = new Date().toISOString().slice(0, 10);
  } else {
    profile.watched.push({
      movie_id,
      personal_rating: personal_rating || 0,
      watched_date: new Date().toISOString().slice(0, 10),
    });
  }

  // Recalculate taste profile
  const catalog = await env.PREFS.get("catalog:movies", { type: "json" }) || [];
  profile.taste_profile = recalculateTaste(profile.watched, catalog);
  profile.updated_at = new Date().toISOString();

  await env.PREFS.put(`kl-user:${token}`, JSON.stringify(profile));
  return { ok: true, taste_profile: profile.taste_profile, watched_count: profile.watched.length };
}

function recalculateTaste(watched, catalog) {
  let totalDelta = 0;
  let ratedCount = 0;
  const genreCounts = {};
  const moodCounts = {};

  for (const w of watched) {
    const movie = catalog[w.movie_id];
    if (!movie) continue;

    if (w.personal_rating && movie.kit_rating) {
      totalDelta += (w.personal_rating - movie.kit_rating);
      ratedCount++;
    }

    if (movie.genre) {
      genreCounts[movie.genre] = (genreCounts[movie.genre] || 0) + (w.personal_rating || 3);
    }
    for (const mood of (movie.moods || [])) {
      moodCounts[mood] = (moodCounts[mood] || 0) + (w.personal_rating || 3);
    }
  }

  const avgDelta = ratedCount > 0 ? Math.round((totalDelta / ratedCount) * 100) / 100 : 0;

  // Normalize genre affinity
  const avgGenre = Object.values(genreCounts).length > 0
    ? Object.values(genreCounts).reduce((a, b) => a + b, 0) / Object.keys(genreCounts).length
    : 1;
  const genreAffinity = {};
  for (const [genre, score] of Object.entries(genreCounts)) {
    genreAffinity[genre] = Math.round((score / avgGenre) * 100) / 100;
  }

  // Overlap score: 1.0 = perfect agreement, 0.0 = total disagreement
  let avgAbsDelta = 0;
  if (ratedCount > 0) {
    avgAbsDelta = watched.reduce((sum, w) => {
      const m = catalog[w.movie_id];
      return sum + (m && w.personal_rating && m.kit_rating ? Math.abs(w.personal_rating - m.kit_rating) : 0);
    }, 0) / ratedCount;
  }
  const overlapScore = Math.max(0, Math.round((1 - avgAbsDelta / 5) * 100) / 100);

  return {
    genre_affinity: genreAffinity,
    mood_affinity: moodCounts,
    avg_rating_delta: avgDelta,
    overlap_score: overlapScore,
  };
}

async function handleKitLazerRecommend(request, env) {
  const apiKey = env.ANTHROPIC_API_KEY;
  if (!apiKey) return { error: "ANTHROPIC_API_KEY not configured" };
  if (!env.PREFS) return { error: "KV not bound" };

  let body;
  try { body = await request.json(); } catch (e) { return { error: "Invalid JSON" }; }

  const { question, conversation, token } = body;
  if (!question) return { error: "Missing question" };

  // Load catalog
  const catalog = await env.PREFS.get("catalog:movies", { type: "json" }) || [];

  // Load user profile
  let userContext = "";
  if (token) {
    const profile = await env.PREFS.get(`kl-user:${token}`, { type: "json" });
    if (profile && profile.taste_profile) {
      const tp = profile.taste_profile;
      const watchedTitles = profile.watched.slice(0, 15).map(w => {
        const m = catalog[w.movie_id];
        return m ? `${m.title} (you: ${w.personal_rating}/5, Kit: ${m.kit_rating}/5)` : null;
      }).filter(Boolean).join(", ");

      userContext = `\n\nUSER TASTE PROFILE:
Overlap with Kit Lazer: ${Math.round(tp.overlap_score * 100)}%
Avg rating difference: ${tp.avg_rating_delta > 0 ? "+" : ""}${tp.avg_rating_delta} (positive = user rates higher)
Top genres: ${Object.entries(tp.genre_affinity).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([g, s]) => `${g}(${s})`).join(", ")}
Recently watched: ${watchedTitles || "none yet"}`;
    }
  }

  // Build catalog summary (limit to keep context manageable)
  const sortedCatalog = [...catalog].sort((a, b) => (b.kit_rating || 0) - (a.kit_rating || 0));
  const catalogSummary = sortedCatalog.slice(0, 300).map(m =>
    `[${m.id}] ${m.title} (${m.year}) - ${m.kit_rating || "?"}/5 - ${m.genre || "?"} - ${(m.moods || []).join(",")} - ${m.availability || "?"}`
  ).join("\n");

  // Build messages
  const messages = [];
  if (conversation && Array.isArray(conversation)) {
    for (const turn of conversation) {
      messages.push({ role: turn.role, content: turn.content });
    }
  }

  const userMsg = messages.length === 0
    ? `KIT LAZER'S CATALOG (${catalog.length} films, top 300 shown):\n${catalogSummary}${userContext}\n\nUser's question: ${question}`
    : question;
  messages.push({ role: "user", content: userMsg });

  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514",
        max_tokens: 1500,
        system: KIT_LAZER_RECOMMEND_PROMPT,
        messages,
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      return { error: `Claude API returned ${resp.status}` };
    }

    const data = await resp.json();
    return { reply: data.content?.[0]?.text || "No response received." };
  } catch (e) {
    return { error: `Failed to reach Claude API: ${e.message}` };
  }
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

export default {
  async fetch(request, env, ctx) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    // ---- POST /query — Inquiry Desk ----
    if (request.method === "POST" && (path === "/query" || path === "/query/")) {
      const result = await handleQuery(request, env);
      const status = result.error ? (result.error.includes("Missing") ? 400 : 500) : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- GET /prefs/:token — Get preferences ----
    const prefsGetMatch = path.match(/^\/prefs\/([a-zA-Z0-9_-]+)\/?$/);
    if (request.method === "GET" && prefsGetMatch) {
      const result = await handleGetPrefs(prefsGetMatch[1], env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- POST /prefs/:token — Save preferences ----
    const prefsSaveMatch = path.match(/^\/prefs\/([a-zA-Z0-9_-]+)\/?$/);
    if (request.method === "POST" && prefsSaveMatch) {
      const result = await handleSavePrefs(prefsSaveMatch[1], request, env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- POST /track/:token — Track reading engagement ----
    const trackMatch = path.match(/^\/track\/([a-zA-Z0-9_-]+)\/?$/);
    if (request.method === "POST" && trackMatch) {
      const result = await handleTrackRead(trackMatch[1], request, env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- Kit Lazer Picks API ----

    // POST /kit-lazer/sync — Admin: push movies
    if (request.method === "POST" && (path === "/kit-lazer/sync" || path === "/kit-lazer/sync/")) {
      const result = await handleKitLazerSync(request, env);
      const status = result.error ? (result.error === "Unauthorized" ? 401 : 500) : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // GET /kit-lazer/catalog — Full catalog
    if (request.method === "GET" && (path === "/kit-lazer/catalog" || path === "/kit-lazer/catalog/")) {
      const result = await handleKitLazerCatalog(env);
      return new Response(JSON.stringify(result), {
        headers: { ...corsHeaders, "Content-Type": "application/json", "Cache-Control": "public, max-age=300" },
      });
    }

    // GET /kit-lazer/moods — Mood index
    if (request.method === "GET" && (path === "/kit-lazer/moods" || path === "/kit-lazer/moods/")) {
      const result = await handleKitLazerMoods(env);
      return new Response(JSON.stringify(result), {
        headers: { ...corsHeaders, "Content-Type": "application/json", "Cache-Control": "public, max-age=300" },
      });
    }

    // POST /kit-lazer/recommend — AI recommendations
    if (request.method === "POST" && (path === "/kit-lazer/recommend" || path === "/kit-lazer/recommend/")) {
      const result = await handleKitLazerRecommend(request, env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // GET /kit-lazer/user/:token — User profile
    const klUserGetMatch = path.match(/^\/kit-lazer\/user\/([a-zA-Z0-9_-]+)\/?$/);
    if (request.method === "GET" && klUserGetMatch) {
      const result = await handleKitLazerGetUser(klUserGetMatch[1], env);
      return new Response(JSON.stringify(result), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // POST /kit-lazer/user/:token/watched — Mark watched + rate
    const klWatchedMatch = path.match(/^\/kit-lazer\/user\/([a-zA-Z0-9_-]+)\/watched\/?$/);
    if (request.method === "POST" && klWatchedMatch) {
      const result = await handleKitLazerMarkWatched(klWatchedMatch[1], request, env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- GET / — Wire Room (live feeds) ----
    if (request.method === "GET" && (path === "/" || path === "")) {
      const category = url.searchParams.get("category") || "all";
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "25"), 50);

      let feedsToFetch = [];
      if (category === "all") {
        feedsToFetch = Object.values(LIVE_FEEDS).flat();
      } else if (LIVE_FEEDS[category]) {
        feedsToFetch = LIVE_FEEDS[category];
      } else {
        return new Response(
          JSON.stringify({ error: `Unknown category: ${category}. Available: ${Object.keys(LIVE_FEEDS).join(", ")}, all` }),
          { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      const items = await fetchCategory(feedsToFetch);
      const response = {
        generated_at: new Date().toISOString(),
        category,
        count: Math.min(items.length, limit),
        items: items.slice(0, limit),
      };

      return new Response(JSON.stringify(response, null, 2), {
        headers: {
          ...corsHeaders,
          "Content-Type": "application/json",
          "Cache-Control": "public, max-age=120",
        },
      });
    }

    return new Response("Not found", { status: 404, headers: corsHeaders });
  },

  // Cron trigger — dispatches GitHub Actions daily digest workflow
  async scheduled(event, env, ctx) {
    const token = env.GITHUB_TOKEN;
    if (!token) {
      await cronAlert(env, "GITHUB_TOKEN not set — cannot dispatch workflow");
      return;
    }

    const resp = await fetch(
      "https://api.github.com/repos/matttrainer-gif/the-morning-train/actions/workflows/daily-digest.yml/dispatches",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "MorningTrain-CronWorker/1.0",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );

    if (resp.ok || resp.status === 204) {
      console.log("Digest workflow dispatched successfully");
    } else {
      const err = await resp.text();
      const msg = `GitHub dispatch failed: HTTP ${resp.status} — ${err}`;
      console.error(msg);
      await cronAlert(env, msg);
    }
  },
};
