"""
Analytics: lead scoring, hashtag trends, content recommendations, posting time.
"""
import math
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from config import get_settings
from db.models import Lead, Interaction, Post, HashtagStat, CompetitorPost

settings = get_settings()


# ---------------------------------------------------------------------------
# Lead scoring
# ---------------------------------------------------------------------------

def recalculate_lead_scores(db: Session) -> int:
    """Recompute every lead's score from their interaction history."""
    leads = db.query(Lead).all()

    for lead in leads:
        score = 0
        posts_interacted = set()

        for ix in lead.interactions:
            if ix.interaction_type == "comment":
                score += settings.score_comment
            elif ix.interaction_type == "like":
                score += settings.score_like
            posts_interacted.add(ix.post_id)

        # Bonus for repeated interaction (same lead on multiple posts)
        if len(posts_interacted) > 1:
            score += settings.score_repeat * (len(posts_interacted) - 1)

        lead.lead_score = score

    db.commit()
    return len(leads)


def get_top_leads(db: Session, limit: int = 50) -> list[dict]:
    leads = (
        db.query(Lead)
        .order_by(desc(Lead.lead_score))
        .limit(limit)
        .all()
    )
    return [
        {
            "instagram_user_id": l.instagram_user_id,
            "username":          l.username,
            "full_name":         l.full_name,
            "lead_score":        l.lead_score,
            "interaction_count": l.interaction_count,
            "first_seen_at":     l.first_seen_at.isoformat() if l.first_seen_at else None,
            "last_seen_at":      l.last_seen_at.isoformat()  if l.last_seen_at  else None,
        }
        for l in leads
    ]


# ---------------------------------------------------------------------------
# Hashtag trend analysis
# ---------------------------------------------------------------------------

def analyze_hashtag_trends(db: Session) -> int:
    """
    Count hashtag frequency across all own + competitor posts.
    Update trending_score using a time-decay formula.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(days=30)

    # Pull hashtags from our own posts
    own_posts = db.query(Post).filter(Post.posted_at >= cutoff).all()
    comp_posts = db.query(CompetitorPost).filter(CompetitorPost.posted_at >= cutoff).all()

    tag_likes: dict[str, int]    = {}
    tag_comments: dict[str, int] = {}
    tag_count: dict[str, int]    = {}

    for post in own_posts:
        for tag in (post.hashtags or []):
            tag = tag.lower().strip()
            tag_count[tag]    = tag_count.get(tag, 0) + 1
            tag_likes[tag]    = tag_likes.get(tag, 0) + post.likes_count
            tag_comments[tag] = tag_comments.get(tag, 0) + post.comments_count

    for post in comp_posts:
        for tag in (post.hashtags or []):
            tag = tag.lower().strip()
            tag_count[tag]    = tag_count.get(tag, 0) + 1
            tag_likes[tag]    = tag_likes.get(tag, 0) + post.likes_count
            tag_comments[tag] = tag_comments.get(tag, 0) + post.comments_count

    for tag, freq in tag_count.items():
        engagement = tag_likes.get(tag, 0) + tag_comments.get(tag, 0)
        # Score = log(freq+1) * log(engagement+1) — balances volume vs engagement
        score = math.log1p(freq) * math.log1p(engagement)

        stat = db.query(HashtagStat).filter_by(hashtag=tag).first()
        if stat:
            stat.frequency      = freq
            stat.total_likes    = tag_likes.get(tag, 0)
            stat.total_comments = tag_comments.get(tag, 0)
            stat.trending_score = score
            stat.last_seen_at   = now
        else:
            db.add(HashtagStat(
                hashtag        = tag,
                frequency      = freq,
                total_likes    = tag_likes.get(tag, 0),
                total_comments = tag_comments.get(tag, 0),
                trending_score = score,
                last_seen_at   = now,
            ))

    db.commit()
    return len(tag_count)


def get_trending_hashtags(db: Session, limit: int = 30) -> list[dict]:
    stats = (
        db.query(HashtagStat)
        .order_by(desc(HashtagStat.trending_score))
        .limit(limit)
        .all()
    )
    return [
        {
            "hashtag":        s.hashtag,
            "frequency":      s.frequency,
            "total_likes":    s.total_likes,
            "total_comments": s.total_comments,
            "trending_score": round(s.trending_score, 4),
            "last_seen_at":   s.last_seen_at.isoformat() if s.last_seen_at else None,
        }
        for s in stats
    ]


# ---------------------------------------------------------------------------
# Content intelligence
# ---------------------------------------------------------------------------

def get_top_performing_posts(db: Session, limit: int = 10) -> list[dict]:
    posts = (
        db.query(Post)
        .order_by(desc(Post.engagement_rate))
        .limit(limit)
        .all()
    )
    return [
        {
            "instagram_id":    p.instagram_id,
            "caption":         (p.caption or "")[:200],
            "hashtags":        p.hashtags,
            "likes_count":     p.likes_count,
            "comments_count":  p.comments_count,
            "engagement_rate": p.engagement_rate,
            "posted_at":       p.posted_at.isoformat() if p.posted_at else None,
            "permalink":       p.permalink,
        }
        for p in posts
    ]


# ---------------------------------------------------------------------------
# Content recommendation engine
# ---------------------------------------------------------------------------

def recommend_hashtags(db: Session, top_n: int = 15) -> list[str]:
    """
    Suggest hashtags based on:
    1. High trending_score from our own top posts
    2. Frequency in competitor posts
    """
    trending = (
        db.query(HashtagStat.hashtag)
        .order_by(desc(HashtagStat.trending_score))
        .limit(top_n)
        .all()
    )
    return [row[0] for row in trending]


def recommend_posting_times(db: Session) -> list[dict]:
    """
    Analyse which hour-of-day our posts get the most engagement.
    Returns sorted list of {hour, avg_engagement}.
    """
    posts = db.query(Post).filter(Post.posted_at.isnot(None)).all()
    if not posts:
        return []

    hourly: dict[int, list[float]] = {}
    for p in posts:
        hour = p.posted_at.hour
        eng  = p.likes_count + p.comments_count
        hourly.setdefault(hour, []).append(eng)

    result = [
        {"hour": h, "avg_engagement": round(sum(v) / len(v), 2)}
        for h, v in hourly.items()
    ]
    result.sort(key=lambda x: x["avg_engagement"], reverse=True)
    return result


def get_account_summary(db: Session) -> dict:
    total_posts    = db.query(func.count(Post.id)).scalar() or 0
    total_leads    = db.query(func.count(Lead.id)).scalar() or 0
    avg_likes      = db.query(func.avg(Post.likes_count)).scalar() or 0
    avg_comments   = db.query(func.avg(Post.comments_count)).scalar() or 0
    avg_eng        = db.query(func.avg(Post.engagement_rate)).scalar() or 0
    top_hashtags   = recommend_hashtags(db, top_n=5)
    best_times     = recommend_posting_times(db)[:3]

    return {
        "total_posts":       total_posts,
        "total_leads":       total_leads,
        "avg_likes":         round(float(avg_likes), 2),
        "avg_comments":      round(float(avg_comments), 2),
        "avg_engagement_pct": round(float(avg_eng), 4),
        "top_hashtags":      top_hashtags,
        "best_posting_hours": best_times,
    }
