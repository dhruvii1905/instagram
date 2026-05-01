#!/usr/bin/env python3
"""
Instagram Scraper — Main CLI (Browser Mode)
All actions run visibly inside Chrome.
"""
import click
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

import database as db
from browser_scraper import BrowserScraper

console = Console()
_bs: BrowserScraper | None = None


def get_browser() -> BrowserScraper:
    global _bs
    if _bs is None or _bs.driver is None:
        _bs = BrowserScraper()
        _bs.start()
    return _bs


@click.group()
def cli():
    """Instagram Growth & Scraping Tool (Browser Mode)"""
    db.init_db()


# ---------- login / test ----------

@cli.command()
def login():
    """Open Chrome and verify the session is active."""
    bs = get_browser()
    bs._goto("https://www.instagram.com/", sleep=2)
    console.print("[green]Chrome is open and logged in.[/green]")
    input("Press Enter to close Chrome...")
    bs.stop()


# ---------- competitors ----------

@cli.group()
def competitor():
    """Manage competitor accounts."""


@competitor.command("add")
@click.argument("username")
def competitor_add(username):
    db.add_competitor(username.lstrip("@"))
    console.print(f"[green]Added @{username}[/green]")


@competitor.command("list")
def competitor_list():
    rows = db.get_competitors()
    t = Table("username", "last scraped")
    for r in rows:
        t.add_row(r["username"], r["last_scraped_at"] or "never")
    console.print(t)


@competitor.command("scrape")
@click.option("--max", "max_users", default=300, show_default=True)
def competitor_scrape(max_users):
    """Scrape followers of all tracked competitors (shown in Chrome)."""
    bs = get_browser()
    for comp in db.get_competitors():
        bs.scrape_competitor_followers(comp["username"], max_users=max_users)
    bs.stop()


# ---------- reel ----------

@cli.command()
@click.argument("url")
def scrape_reel(url):
    """Scrape engagers of a Reel URL (shown in Chrome)."""
    bs = get_browser()
    bs.scrape_reel(url)
    bs.stop()


# ---------- hashtag ----------

@cli.command()
@click.argument("hashtag")
@click.option("--max", "max_posts", default=30, show_default=True)
def scrape_hashtag(hashtag, max_posts):
    """Scrape recent posters of a hashtag (shown in Chrome)."""
    bs = get_browser()
    bs.scrape_hashtag(hashtag.lstrip("#"), max_posts=max_posts)
    bs.stop()


# ---------- follow ----------

@cli.command()
@click.option("--batch", default=20, show_default=True)
def follow(batch):
    """Follow users from the queue (shown in Chrome)."""
    bs = get_browser()
    bs.run_follow_queue(batch_size=batch)
    bs.stop()


# ---------- stories ----------

@cli.command()
@click.option("--limit", default=30, show_default=True)
def view_stories(limit):
    """View stories of followed leads (shown in Chrome)."""
    bs = get_browser()
    bs.view_stories_of_leads(limit=limit)
    bs.stop()


# ---------- DMs ----------

@cli.command()
@click.argument("username")
@click.argument("message")
def queue_dm(username, message):
    """Add a DM to the send queue."""
    bs = get_browser()
    uid = username.lstrip("@")
    db.add_to_dm_queue(uid, uid, message)
    console.print(f"[green]DM queued for @{username}[/green]")
    bs.stop()


@cli.command()
@click.option("--batch", default=10, show_default=True)
def send_dms(batch):
    """Send DMs from the queue (shown in Chrome)."""
    bs = get_browser()
    bs.run_dm_queue(batch_size=batch)
    bs.stop()


# ---------- likes ----------

@cli.command()
@click.argument("username")
@click.option("--count", default=3, show_default=True)
def like_posts(username, count):
    """Like recent posts of a user (shown in Chrome)."""
    bs = get_browser()
    bs.like_recent_posts(username.lstrip("@"), count=count)
    bs.stop()


# ---------- leads ----------

@cli.command()
@click.option("--limit", default=50, show_default=True)
def leads(limit):
    """Show scraped leads."""
    rows = db.get_leads(limit=limit)
    t = Table("username", "followers", "source", "followed", "dm_sent")
    for r in rows:
        t.add_row(
            r["username"],
            str(r["follower_count"] or ""),
            r["source"] or "",
            "yes" if r["followed_at"] else "no",
            "yes" if r["dm_sent_at"] else "no",
        )
    console.print(t)
    console.print(f"[dim]{len(rows)} leads[/dim]")


@cli.command()
def queue_status():
    """Show follow/DM queue sizes."""
    t = Table("Queue", "Pending", "Done", "Skipped/Failed")
    t.add_row(
        "Follow",
        str(db.follow_queue_count("pending")),
        str(db.follow_queue_count("done")),
        str(db.follow_queue_count("skipped")),
    )

    def dm_count(s):
        conn = db.get_conn()
        r = conn.execute("SELECT COUNT(*) FROM dm_queue WHERE status=?", (s,)).fetchone()
        conn.close()
        return r[0]

    t.add_row("DM", str(dm_count("pending")), str(dm_count("sent")), str(dm_count("failed")))
    console.print(t)


# ---------- stats ----------

@cli.command()
def stats():
    """Show today's action stats."""
    db.init_db()
    bs = BrowserScraper()
    bs.print_stats()


# ---------- interactive menu ----------

@cli.command()
def menu():
    """Interactive menu — all actions run in Chrome."""
    db.init_db()
    bs = get_browser()

    while True:
        console.print("\n[bold]== Instagram Scraper (Browser Mode) ==[/bold]")
        console.print("1.  Scrape competitor followers")
        console.print("2.  Run follow queue")
        console.print("3.  View stories of leads")
        console.print("4.  Send DMs from queue")
        console.print("5.  Scrape a Reel")
        console.print("6.  Scrape a hashtag")
        console.print("7.  Like posts of a user")
        console.print("8.  Show today's stats")
        console.print("9.  Show leads")
        console.print("10. Show queue status")
        console.print("0.  Exit")

        choice = Prompt.ask("Choice").strip()

        if choice == "1":
            uname = Prompt.ask("Competitor username").lstrip("@")
            mx = int(Prompt.ask("Max followers", default="300"))
            db.add_competitor(uname)
            bs.scrape_competitor_followers(uname, max_users=mx)

        elif choice == "2":
            batch = int(Prompt.ask("Batch size", default="20"))
            bs.run_follow_queue(batch_size=batch)

        elif choice == "3":
            limit = int(Prompt.ask("Max users", default="30"))
            bs.view_stories_of_leads(limit=limit)

        elif choice == "4":
            batch = int(Prompt.ask("Batch size", default="10"))
            bs.run_dm_queue(batch_size=batch)

        elif choice == "5":
            url = Prompt.ask("Reel URL")
            bs.scrape_reel(url)

        elif choice == "6":
            tag = Prompt.ask("Hashtag (without #)")
            mx = int(Prompt.ask("Max posts", default="30"))
            bs.scrape_hashtag(tag, max_posts=mx)

        elif choice == "7":
            uname = Prompt.ask("Username").lstrip("@")
            count = int(Prompt.ask("Number of posts to like", default="3"))
            bs.like_recent_posts(uname, count=count)

        elif choice == "8":
            bs.print_stats()

        elif choice == "9":
            rows = db.get_leads(limit=50)
            t = Table("username", "source", "followed", "dm")
            for r in rows:
                t.add_row(
                    r["username"], r["source"] or "",
                    "yes" if r["followed_at"] else "no",
                    "yes" if r["dm_sent_at"] else "no",
                )
            console.print(t)

        elif choice == "10":
            cli.commands["queue-status"].invoke(click.Context(cli.commands["queue-status"]))

        elif choice == "0":
            bs.stop()
            break


if __name__ == "__main__":
    cli()
