"""
Instagram Graph API service.
All requests go through _call() which enforces rate limiting and logs every call.
"""
import time
import re
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from config import get_settings
from db.models import ApiCallLog, Post, Lead, Interaction, CompetitorAccount, CompetitorPost
from services.metrics import (
    api_requests_total, api_errors_total,
    leads_collected_total, posts_processed_total,
)

settings = get_settings()

BASE_URL = f"https://graph.instagram.com/{settings.instagram_api_version}"
_call_timestamps: list[float] = []   # in-process rate limit window


def _enforce_rate_limit():
    now = time.time()
    window = [t for t in _call_timestamps if now - t < 3600]
    _call_timestamps.clear()
    _call_timestamps.extend(window)
    if len(_call_timestamps) >= settings.instagram_api_rate_limit:
        sleep_for = 3600 - (now - _call_timestamps[0]) + 1
        time.sleep(sleep_for)
    _call_timestamps.append(time.time())


def _call(db: Session, endpoint: str, params: dict | None = None) -> dict:
    """
    Single point of exit for all Graph API calls.
    Logs every call, enforces rate limit, tracks metrics.
    """
    _enforce_rate_limit()
    url = f"{BASE_URL}/{endpoint}"
    base_params = {"access_token": settings.instagram_access_token}
    if params:
        base_params.update(params)

    start = time.time()
    log = ApiCallLog(endpoint=endpoint, method="GET")
    error_msg = None
    status = None

    try:
        resp = httpx.get(url, params=base_params, timeout=15)
        status = resp.status_code
        elapsed = (time.time() - start) * 1000
        log.status_code = status
        log.response_time_ms = elapsed

        api_requests_total.labels(endpoint=endpoint, status=str(status)).inc()

        if status != 200:
            error_msg = resp.text[:500]
            log.error = error_msg
            api_errors_total.labels(endpoint=endpoint, error=str(status)).inc()
            db.add(log)
            db.commit()
            return {}

        db.add(log)
        db.commit()
        return resp.json()

    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        log.response_time_ms = elapsed
        log.error = str(exc)
        api_errors_total.labels(endpoint=endpoint, error=type(exc).__name__).inc()
        db.add(log)
        db.commit()
        return {}


def _extract_hashtags(caption: str) -> list[str]:
    return re.findall(r"#(\w+)", caption or "")


# ---------------------------------------------------------------------------
# Own account — posts
# ---------------------------------------------------------------------------

def sync_own_posts(db: Session) -> int:
    """Fetch our latest posts from the Graph API and upsert into DB."""
    account_id = settings.instagram_business_account_id
    data = _call(db, f"{account_id}/media", {
        "fields": "id,caption,media_type,like_count,comments_count,timestamp,permalink",
        "limit": 50,
    })

    posts = data.get("data", [])
    saved = 0

    for p in posts:
        hashtags = _extract_hashtags(p.get("caption", ""))
        posted_at = None
        if p.get("timestamp"):
            try:
                posted_at = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            except ValueError:
                pass

        existing = db.query(Post).filter_by(instagram_id=p["id"]).first()
        if existing:
            existing.likes_count    = p.get("like_count", 0)
            existing.comments_count = p.get("comments_count", 0)
            existing.hashtags       = hashtags
            existing.caption        = p.get("caption", "")
            existing.synced_at      = datetime.utcnow()
            existing.engagement_rate = existing.calculate_engagement_rate()
        else:
            post = Post(
                instagram_id    = p["id"],
                caption         = p.get("caption", ""),
                hashtags        = hashtags,
                media_type      = p.get("media_type", "IMAGE"),
                likes_count     = p.get("like_count", 0),
                comments_count  = p.get("comments_count", 0),
                permalink       = p.get("permalink", ""),
                posted_at       = posted_at,
            )
            db.add(post)
            saved += 1

    db.commit()
    posts_processed_total.inc(saved)
    return len(posts)


# ---------------------------------------------------------------------------
# Own account — engagement (likes + comments → leads)
# ---------------------------------------------------------------------------

def sync_post_engagement(db: Session, instagram_post_id: str) -> int:
    """
    Fetch likers and commenters for one of our posts.
    Create/update Lead records and Interaction records.
    Returns number of new interactions stored.
    """
    post = db.query(Post).filter_by(instagram_id=instagram_post_id).first()
    if not post:
        return 0

    new_interactions = 0

    # --- Likes ---
    likes_data = _call(db, f"{instagram_post_id}/likes", {
        "fields": "id,username,name,profile_picture_url",
    })
    for user in likes_data.get("data", []):
        lead = _upsert_lead(db, user)
        if _upsert_interaction(db, lead, post, "like"):
            new_interactions += 1

    # --- Comments ---
    comments_data = _call(db, f"{instagram_post_id}/comments", {
        "fields": "id,username,text,from,timestamp",
    })
    for comment in comments_data.get("data", []):
        from_user = comment.get("from", {})
        if not from_user.get("id"):
            continue
        lead = _upsert_lead(db, {
            "id":       from_user.get("id"),
            "username": from_user.get("username", ""),
            "name":     from_user.get("name", ""),
        })
        if _upsert_interaction(db, lead, post, "comment",
                               comment_text=comment.get("text", "")):
            new_interactions += 1

    db.commit()
    return new_interactions


def _upsert_lead(db: Session, user: dict) -> Lead:
    uid = user.get("id", "")
    lead = db.query(Lead).filter_by(instagram_user_id=uid).first()
    if not lead:
        lead = Lead(
            instagram_user_id = uid,
            username          = user.get("username", ""),
            full_name         = user.get("name", ""),
            profile_pic_url   = user.get("profile_picture_url", ""),
        )
        db.add(lead)
        db.flush()
        leads_collected_total.inc()
    else:
        lead.last_seen_at = datetime.utcnow()
        lead.username     = user.get("username", lead.username)
    return lead


def _upsert_interaction(db: Session, lead: Lead, post: Post,
                        itype: str, comment_text: str = "") -> bool:
    """Returns True if a new interaction was created."""
    existing = db.query(Interaction).filter_by(
        lead_id=lead.id, post_id=post.id, interaction_type=itype
    ).first()
    if existing:
        return False

    interaction = Interaction(
        lead_id          = lead.id,
        post_id          = post.id,
        interaction_type = itype,
        comment_text     = comment_text,
    )
    db.add(interaction)
    lead.interaction_count += 1
    return True


# ---------------------------------------------------------------------------
# Competitor accounts (Graph API — public business pages only)
# ---------------------------------------------------------------------------

def sync_competitor(db: Session, competitor: CompetitorAccount) -> int:
    """
    Fetch public media from a competitor business page via Graph API.
    Requires a Business Discovery API token for the page.
    """
    data = _call(db, f"{settings.instagram_business_account_id}", {
        "fields": f"business_discovery.fields(id,media_count,media{{id,caption,like_count,comments_count,timestamp}})",
        "username": competitor.username,
    })

    discovery = data.get("business_discovery", {})
    media_list = discovery.get("media", {}).get("data", [])
    if not media_list:
        return 0

    total_likes    = 0
    total_comments = 0
    posts_saved    = 0

    for m in media_list:
        hashtags = _extract_hashtags(m.get("caption", ""))
        posted_at = None
        if m.get("timestamp"):
            try:
                posted_at = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
            except ValueError:
                pass

        existing = db.query(CompetitorPost).filter_by(instagram_id=m["id"]).first()
        if not existing:
            cp = CompetitorPost(
                competitor_id  = competitor.id,
                instagram_id   = m["id"],
                caption        = m.get("caption", ""),
                hashtags       = hashtags,
                likes_count    = m.get("like_count", 0),
                comments_count = m.get("comments_count", 0),
                posted_at      = posted_at,
            )
            db.add(cp)
            posts_saved += 1
        total_likes    += m.get("like_count", 0)
        total_comments += m.get("comments_count", 0)

    n = len(media_list)
    competitor.post_count      = discovery.get("media_count", n)
    competitor.avg_likes       = round(total_likes / n, 2)
    competitor.avg_comments    = round(total_comments / n, 2)
    competitor.avg_engagement  = round((total_likes + total_comments) / n, 2)
    competitor.last_updated_at = datetime.utcnow()

    db.commit()
    return posts_saved
