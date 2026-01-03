import logging
import time
from dataclasses import dataclass

import requests

from src.utils.database import (
    get_reddit_state,
    set_reddit_state,
    insert_reddit_posts,
    get_recent_reddit_posts,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RedditConfig:
    enabled: bool
    fetch_interval_seconds: int
    analysis_interval_seconds: int
    user_agent: str
    subreddits: list[str]
    listing: str
    limit_per_subreddit: int
    max_posts_per_symbol: int
    override_enabled: bool
    override_sentiment_threshold: float
    override_min_mentions: int
    override_min_confidence: float
    score_weight: float


def load_reddit_config(config: dict) -> RedditConfig:
    r = (config.get("reddit") or {}) if isinstance(config, dict) else {}
    return RedditConfig(
        enabled=bool(r.get("enabled", False)),
        fetch_interval_seconds=int(r.get("fetch_interval_seconds", 3600)),
        analysis_interval_seconds=int(r.get("analysis_interval_seconds", 3600)),
        user_agent=str(r.get("user_agent", "AutoTrader/1.0")),
        subreddits=list(r.get("subreddits", [])),
        listing=str(r.get("listing", "new")),
        limit_per_subreddit=int(r.get("limit_per_subreddit", 50)),
        max_posts_per_symbol=int(r.get("max_posts_per_symbol", 8)),
        override_enabled=bool(r.get("override_enabled", False)),
        override_sentiment_threshold=float(r.get("override_sentiment_threshold", 0.65)),
        override_min_mentions=int(r.get("override_min_mentions", 5)),
        override_min_confidence=float(r.get("override_min_confidence", 0.6)),
        score_weight=float(r.get("score_weight", 0.35)),
    )


class RedditClient:
    """
    Lightweight Reddit reader using the public JSON endpoints.
    We keep requests minimal and cache locally to respect Reddit.
    """

    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def fetch_listing(self, subreddit: str, listing: str, limit: int) -> list[dict]:
        url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
        params = {"limit": int(limit)}
        resp = self.session.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        children = (data.get("data") or {}).get("children") or []
        posts = []
        for c in children:
            d = c.get("data") or {}
            posts.append(
                {
                    "reddit_id": d.get("name"),
                    "subreddit": d.get("subreddit"),
                    "created_utc": int(d.get("created_utc") or 0),
                    "title": d.get("title") or "",
                    "selftext": d.get("selftext") or "",
                    "permalink": d.get("permalink") or "",
                    "ups": int(d.get("ups") or 0),
                    "num_comments": int(d.get("num_comments") or 0),
                }
            )
        return posts


class RedditCache:
    def __init__(self, cfg: RedditConfig):
        self.cfg = cfg
        self.client = RedditClient(cfg.user_agent)

    def refresh_posts_if_due(self) -> bool:
        """
        Fetch subreddit posts at most once per configured interval.
        Returns True if a refresh happened.
        """
        if not self.cfg.enabled:
            return False

        state = get_reddit_state()
        now = int(time.time())
        last_fetch = int(state.get("last_fetch_utc") or 0)
        if now - last_fetch < self.cfg.fetch_interval_seconds:
            return False

        # Mark attempt time up-front to avoid repeated retries within the interval
        # if Reddit is temporarily unavailable.
        set_reddit_state(last_fetch_utc=now)

        try:
            posts_all: list[dict] = []
            listing = self.cfg.listing
            for sr in self.cfg.subreddits:
                posts = self.client.fetch_listing(sr, listing, self.cfg.limit_per_subreddit)
                posts_all.extend(posts)

            insert_reddit_posts(posts_all)
            return True
        except Exception:
            # Respect Reddit: do not retry until the next interval.
            raise

    def get_cached_posts(self, limit: int = 500) -> list[dict]:
        df = get_recent_reddit_posts(limit=limit)
        if df.empty:
            return []
        return df.to_dict(orient="records")


