from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime,
    ForeignKey, JSON, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from db.connection import Base


class Post(Base):
    __tablename__ = "posts"

    id              = Column(Integer, primary_key=True)
    instagram_id    = Column(String(64), unique=True, nullable=False, index=True)
    caption         = Column(Text, default="")
    hashtags        = Column(JSON, default=list)       # list of strings
    media_type      = Column(String(32), default="IMAGE")
    likes_count     = Column(Integer, default=0)
    comments_count  = Column(Integer, default=0)
    reach           = Column(Integer, default=0)
    impressions     = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)
    permalink       = Column(String(512), default="")
    posted_at       = Column(DateTime, nullable=True)
    synced_at       = Column(DateTime, default=datetime.utcnow)

    interactions = relationship("Interaction", back_populates="post", cascade="all, delete-orphan")

    def calculate_engagement_rate(self) -> float:
        total = self.likes_count + self.comments_count
        base  = self.reach or self.impressions or 1
        return round((total / base) * 100, 4)


class Lead(Base):
    __tablename__ = "leads"

    id                  = Column(Integer, primary_key=True)
    instagram_user_id   = Column(String(64), unique=True, nullable=False, index=True)
    username            = Column(String(128), default="")
    full_name           = Column(String(256), default="")
    profile_pic_url     = Column(String(512), default="")
    lead_score          = Column(Integer, default=0, index=True)
    interaction_count   = Column(Integer, default=0)
    first_seen_at       = Column(DateTime, default=datetime.utcnow)
    last_seen_at        = Column(DateTime, default=datetime.utcnow)

    interactions = relationship("Interaction", back_populates="lead", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_leads_score_desc", "lead_score"),
    )


class Interaction(Base):
    """One row per like or comment on one of our posts."""
    __tablename__ = "interactions"

    id               = Column(Integer, primary_key=True)
    lead_id          = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    post_id          = Column(Integer, ForeignKey("posts.id",  ondelete="CASCADE"), index=True)
    interaction_type = Column(String(16), nullable=False)   # "like" | "comment"
    comment_text     = Column(Text, default="")
    occurred_at      = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="interactions")
    post = relationship("Post", back_populates="interactions")

    __table_args__ = (
        UniqueConstraint("lead_id", "post_id", "interaction_type", name="uq_interaction"),
    )


class HashtagStat(Base):
    __tablename__ = "hashtag_stats"

    id             = Column(Integer, primary_key=True)
    hashtag        = Column(String(256), unique=True, nullable=False, index=True)
    frequency      = Column(Integer, default=1)
    total_likes    = Column(Integer, default=0)
    total_comments = Column(Integer, default=0)
    trending_score = Column(Float, default=0.0, index=True)
    last_seen_at   = Column(DateTime, default=datetime.utcnow)


class CompetitorAccount(Base):
    __tablename__ = "competitor_accounts"

    id              = Column(Integer, primary_key=True)
    username        = Column(String(128), unique=True, nullable=False)
    instagram_id    = Column(String(64), default="")
    notes           = Column(Text, default="")
    post_count      = Column(Integer, default=0)
    avg_likes       = Column(Float, default=0.0)
    avg_comments    = Column(Float, default=0.0)
    avg_engagement  = Column(Float, default=0.0)
    post_frequency  = Column(Float, default=0.0)   # posts per day
    added_at        = Column(DateTime, default=datetime.utcnow)
    last_updated_at = Column(DateTime, nullable=True)

    posts = relationship("CompetitorPost", back_populates="competitor", cascade="all, delete-orphan")


class CompetitorPost(Base):
    """Public post data fetched from Graph API for competitor pages."""
    __tablename__ = "competitor_posts"

    id             = Column(Integer, primary_key=True)
    competitor_id  = Column(Integer, ForeignKey("competitor_accounts.id", ondelete="CASCADE"), index=True)
    instagram_id   = Column(String(64), unique=True, nullable=False)
    caption        = Column(Text, default="")
    hashtags       = Column(JSON, default=list)
    likes_count    = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    posted_at      = Column(DateTime, nullable=True)
    fetched_at     = Column(DateTime, default=datetime.utcnow)

    competitor = relationship("CompetitorAccount", back_populates="posts")


class ApiCallLog(Base):
    __tablename__ = "api_call_logs"

    id               = Column(Integer, primary_key=True)
    endpoint         = Column(String(512), nullable=False)
    method           = Column(String(8), default="GET")
    status_code      = Column(Integer, nullable=True)
    response_time_ms = Column(Float, nullable=True)
    error            = Column(Text, nullable=True)
    called_at        = Column(DateTime, default=datetime.utcnow, index=True)
