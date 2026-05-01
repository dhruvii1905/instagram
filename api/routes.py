from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from db.connection import get_db
from db.models import Post, Lead, CompetitorAccount, HashtagStat
from services import analytics_service as analytics
from services import instagram_service as ig
from workers.tasks import (
    task_sync_own_posts, task_sync_all_engagement,
    task_recalculate_scores, task_analyze_hashtags,
    task_sync_all_competitors,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

@router.get("/posts")
def list_posts(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from sqlalchemy import desc
    posts = db.query(Post).order_by(desc(Post.posted_at)).limit(limit).all()
    return [
        {
            "instagram_id":    p.instagram_id,
            "caption":         (p.caption or "")[:300],
            "hashtags":        p.hashtags,
            "likes_count":     p.likes_count,
            "comments_count":  p.comments_count,
            "engagement_rate": p.engagement_rate,
            "posted_at":       p.posted_at.isoformat() if p.posted_at else None,
            "permalink":       p.permalink,
        }
        for p in posts
    ]


@router.get("/posts/top")
def top_posts(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return analytics.get_top_performing_posts(db, limit=limit)


@router.post("/posts/sync")
def trigger_sync_posts():
    task = task_sync_own_posts.delay()
    return {"task_id": task.id, "status": "queued"}


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

@router.get("/leads")
def list_leads(
    limit: int = Query(50, ge=1, le=500),
    min_score: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    from sqlalchemy import desc
    leads = (
        db.query(Lead)
        .filter(Lead.lead_score >= min_score)
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
            "last_seen_at":      l.last_seen_at.isoformat() if l.last_seen_at else None,
        }
        for l in leads
    ]


@router.get("/leads/top")
def top_leads(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return analytics.get_top_leads(db, limit=limit)


@router.get("/leads/{instagram_user_id}")
def get_lead(instagram_user_id: str, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter_by(instagram_user_id=instagram_user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {
        "instagram_user_id": lead.instagram_user_id,
        "username":          lead.username,
        "full_name":         lead.full_name,
        "lead_score":        lead.lead_score,
        "interaction_count": lead.interaction_count,
        "first_seen_at":     lead.first_seen_at.isoformat() if lead.first_seen_at else None,
        "last_seen_at":      lead.last_seen_at.isoformat()  if lead.last_seen_at  else None,
        "interactions": [
            {
                "post_id":          ix.post_id,
                "interaction_type": ix.interaction_type,
                "comment_text":     ix.comment_text,
                "occurred_at":      ix.occurred_at.isoformat() if ix.occurred_at else None,
            }
            for ix in lead.interactions
        ],
    }


# ---------------------------------------------------------------------------
# Hashtags / Trending
# ---------------------------------------------------------------------------

@router.get("/hashtags/trending")
def trending_hashtags(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return analytics.get_trending_hashtags(db, limit=limit)


# ---------------------------------------------------------------------------
# Analytics & Recommendations
# ---------------------------------------------------------------------------

@router.get("/analytics/summary")
def account_summary(db: Session = Depends(get_db)):
    return analytics.get_account_summary(db)


@router.get("/analytics/recommendations")
def content_recommendations(
    hashtag_count: int = Query(15, ge=5, le=30),
    db: Session = Depends(get_db),
):
    return {
        "suggested_hashtags":   analytics.recommend_hashtags(db, top_n=hashtag_count),
        "best_posting_hours":   analytics.recommend_posting_times(db)[:5],
    }


# ---------------------------------------------------------------------------
# Competitors
# ---------------------------------------------------------------------------

class CompetitorIn(BaseModel):
    username: str
    notes: Optional[str] = ""


@router.post("/competitors", status_code=201)
def add_competitor(body: CompetitorIn, db: Session = Depends(get_db)):
    uname = body.username.lstrip("@").lower()
    existing = db.query(CompetitorAccount).filter_by(username=uname).first()
    if existing:
        raise HTTPException(status_code=409, detail="Competitor already exists")
    comp = CompetitorAccount(username=uname, notes=body.notes or "")
    db.add(comp)
    db.commit()
    db.refresh(comp)
    return {"id": comp.id, "username": comp.username}


@router.get("/competitors")
def list_competitors(db: Session = Depends(get_db)):
    comps = db.query(CompetitorAccount).all()
    return [
        {
            "id":               c.id,
            "username":         c.username,
            "post_count":       c.post_count,
            "avg_likes":        c.avg_likes,
            "avg_comments":     c.avg_comments,
            "avg_engagement":   c.avg_engagement,
            "last_updated_at":  c.last_updated_at.isoformat() if c.last_updated_at else None,
        }
        for c in comps
    ]


@router.delete("/competitors/{competitor_id}", status_code=204)
def delete_competitor(competitor_id: int, db: Session = Depends(get_db)):
    comp = db.query(CompetitorAccount).filter_by(id=competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    db.delete(comp)
    db.commit()


@router.post("/competitors/sync")
def trigger_sync_competitors():
    task = task_sync_all_competitors.delay()
    return {"task_id": task.id, "status": "queued"}


# ---------------------------------------------------------------------------
# Manual CSV import for leads/competitors
# ---------------------------------------------------------------------------

@router.post("/import/competitors")
def import_competitors_csv(
    usernames: list[str] = Body(..., example=["account1", "account2"]),
    db: Session = Depends(get_db),
):
    added = []
    for uname in usernames:
        uname = uname.lstrip("@").lower().strip()
        if not uname:
            continue
        existing = db.query(CompetitorAccount).filter_by(username=uname).first()
        if not existing:
            db.add(CompetitorAccount(username=uname))
            added.append(uname)
    db.commit()
    return {"added": added, "count": len(added)}


# ---------------------------------------------------------------------------
# Job triggers
# ---------------------------------------------------------------------------

@router.post("/jobs/sync-engagement")
def trigger_sync_engagement():
    task = task_sync_all_engagement.delay()
    return {"task_id": task.id, "status": "queued"}


@router.post("/jobs/score-leads")
def trigger_score_leads():
    task = task_recalculate_scores.delay()
    return {"task_id": task.id, "status": "queued"}


@router.post("/jobs/analyze-hashtags")
def trigger_analyze_hashtags():
    task = task_analyze_hashtags.delay()
    return {"task_id": task.id, "status": "queued"}
