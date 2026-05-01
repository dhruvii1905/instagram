"""
Celery background tasks — all scheduled, no manual triggering needed.
"""
import time
from celery import Celery
from celery.schedules import crontab

from config import get_settings
from db.connection import SessionLocal
from services import instagram_service as ig
from services import analytics_service as analytics
from services.metrics import job_duration_seconds, active_leads_gauge
from db.models import Post, CompetitorAccount

settings = get_settings()

celery_app = Celery(
    "instagram_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

# ---------------------------------------------------------------------------
# Beat schedule — runs these automatically
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    "sync-own-posts": {
        "task":     "workers.tasks.task_sync_own_posts",
        "schedule": settings.sync_posts_interval,
    },
    "sync-all-post-engagement": {
        "task":     "workers.tasks.task_sync_all_engagement",
        "schedule": settings.sync_engagement_interval,
    },
    "recalculate-lead-scores": {
        "task":     "workers.tasks.task_recalculate_scores",
        "schedule": settings.score_leads_interval,
    },
    "analyze-hashtag-trends": {
        "task":     "workers.tasks.task_analyze_hashtags",
        "schedule": settings.hashtag_analysis_interval,
    },
    "sync-all-competitors": {
        "task":     "workers.tasks.task_sync_all_competitors",
        "schedule": 600,   # every 10 min
    },
    "update-active-leads-gauge": {
        "task":     "workers.tasks.task_update_gauges",
        "schedule": 60,
    },
}


# ---------------------------------------------------------------------------
# Task implementations
# ---------------------------------------------------------------------------

@celery_app.task(name="workers.tasks.task_sync_own_posts", bind=True, max_retries=3)
def task_sync_own_posts(self):
    job = "sync_own_posts"
    start = time.time()
    db = SessionLocal()
    try:
        count = ig.sync_own_posts(db)
        job_duration_seconds.labels(job_name=job).observe(time.time() - start)
        return {"synced": count}
    except Exception as exc:
        job_duration_seconds.labels(job_name=job).observe(time.time() - start)
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@celery_app.task(name="workers.tasks.task_sync_all_engagement", bind=True, max_retries=3)
def task_sync_all_engagement(self):
    job = "sync_engagement"
    start = time.time()
    db = SessionLocal()
    try:
        posts = db.query(Post).order_by(Post.synced_at.asc()).limit(20).all()
        total = 0
        for post in posts:
            total += ig.sync_post_engagement(db, post.instagram_id)
        job_duration_seconds.labels(job_name=job).observe(time.time() - start)
        return {"interactions_added": total}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@celery_app.task(name="workers.tasks.task_recalculate_scores")
def task_recalculate_scores():
    job = "recalculate_scores"
    start = time.time()
    db = SessionLocal()
    try:
        n = analytics.recalculate_lead_scores(db)
        job_duration_seconds.labels(job_name=job).observe(time.time() - start)
        return {"leads_scored": n}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.task_analyze_hashtags")
def task_analyze_hashtags():
    job = "analyze_hashtags"
    start = time.time()
    db = SessionLocal()
    try:
        n = analytics.analyze_hashtag_trends(db)
        job_duration_seconds.labels(job_name=job).observe(time.time() - start)
        return {"hashtags_analyzed": n}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.task_sync_all_competitors", bind=True, max_retries=2)
def task_sync_all_competitors(self):
    job = "sync_competitors"
    start = time.time()
    db = SessionLocal()
    try:
        competitors = db.query(CompetitorAccount).all()
        total = 0
        for comp in competitors:
            total += ig.sync_competitor(db, comp)
        job_duration_seconds.labels(job_name=job).observe(time.time() - start)
        return {"posts_added": total}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(name="workers.tasks.task_update_gauges")
def task_update_gauges():
    from sqlalchemy import func
    from db.models import Lead
    db = SessionLocal()
    try:
        count = db.query(func.count(Lead.id)).scalar() or 0
        active_leads_gauge.set(count)
    finally:
        db.close()
