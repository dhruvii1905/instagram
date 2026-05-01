"""
Automated daily schedule.
Run this module to start the background job loop.
"""
import schedule
import time
import random
from rich.console import Console
from instagram_scraper import InstagramScraper
import database as db

console = Console()


def _scraper() -> InstagramScraper:
    s = InstagramScraper()
    if not s.login():
        raise RuntimeError("Login failed — cannot run scheduled jobs.")
    return s


def job_scrape_competitors():
    console.print("\n[bold cyan]== Job: Scrape competitor followers ==[/bold cyan]")
    s = _scraper()
    for comp in db.get_competitors():
        s.scrape_competitor_followers(comp["username"], max_users=300)
        time.sleep(random.randint(60, 180))


def job_run_follows():
    console.print("\n[bold cyan]== Job: Run follow queue ==[/bold cyan]")
    s = _scraper()
    s.run_follow_queue(batch_size=30)


def job_view_stories():
    console.print("\n[bold cyan]== Job: View stories of leads ==[/bold cyan]")
    s = _scraper()
    s.view_stories_of_leads(limit=30)


def job_run_dms():
    console.print("\n[bold cyan]== Job: Send DMs ==[/bold cyan]")
    s = _scraper()
    s.run_dm_queue(batch_size=10)


def job_stats():
    console.print("\n[bold cyan]== Daily Stats ==[/bold cyan]")
    s = _scraper()
    s.print_daily_stats()


def start_scheduler():
    """
    Default schedule:
      08:00  scrape competitor followers
      09:00  follow batch #1  (30)
      11:00  view stories
      13:00  follow batch #2  (30)
      15:00  send DMs
      17:00  follow batch #3  (30)
      19:00  view stories again
      21:00  follow batch #4  (30)
      22:00  stats summary
    """
    schedule.every().day.at("08:00").do(job_scrape_competitors)
    schedule.every().day.at("09:00").do(job_run_follows)
    schedule.every().day.at("11:00").do(job_view_stories)
    schedule.every().day.at("13:00").do(job_run_follows)
    schedule.every().day.at("15:00").do(job_run_dms)
    schedule.every().day.at("17:00").do(job_run_follows)
    schedule.every().day.at("19:00").do(job_view_stories)
    schedule.every().day.at("21:00").do(job_run_follows)
    schedule.every().day.at("22:00").do(job_stats)

    console.print("[bold green]Scheduler started. Press Ctrl+C to stop.[/bold green]")
    while True:
        schedule.run_pending()
        time.sleep(30)
