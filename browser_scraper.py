"""
Instagram Browser Scraper
All actions run visibly inside a real Chrome window.
"""
import re
import time
import random
from pathlib import Path
from dotenv import load_dotenv
import os

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from rich.console import Console

import database as db
import rate_limiter as rl

load_dotenv()
console = Console()

USERNAME = os.getenv("INSTAGRAM_USERNAME")
PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
SESSION_ID = os.getenv("INSTAGRAM_SESSION_ID", "")


class BrowserScraper:
    def __init__(self, headless: bool = False):
        self.driver = None
        self.wait = None
        self.headless = headless

    # ------------------------------------------------------------------
    # Browser setup & login
    # ------------------------------------------------------------------

    def start(self):
        console.print("[cyan]Starting Chrome...[/cyan]")
        options = uc.ChromeOptions()
        options.add_argument("--window-size=1280,900")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-notifications")
        if self.headless:
            options.add_argument("--headless=new")

        self.driver = uc.Chrome(options=options, use_subprocess=True, version_main=147)
        self.wait = WebDriverWait(self.driver, 15)
        self._inject_cookies()
        console.print("[green]Chrome started.[/green]")

    def _inject_cookies(self):
        """Set the sessionid cookie so Instagram treats us as logged in."""
        self.driver.get("https://www.instagram.com/")
        time.sleep(2)
        if SESSION_ID:
            self.driver.add_cookie({
                "name": "sessionid",
                "value": SESSION_ID,
                "domain": ".instagram.com",
                "path": "/",
                "secure": True,
            })
            self.driver.refresh()
            time.sleep(3)
            console.print("[green]Session cookie injected — logged in.[/green]")
        else:
            console.print("[yellow]No session ID — please log in manually in the browser.[/yellow]")
            self._wait_for_login()

    def _wait_for_login(self):
        self.driver.get("https://www.instagram.com/accounts/login/")
        console.print("[yellow]Log in manually in the Chrome window. Waiting...[/yellow]")
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(3)
            for c in self.driver.get_cookies():
                if c["name"] == "sessionid" and c["value"]:
                    console.print("[green]Login detected![/green]")
                    return
        console.print("[red]Login timeout.[/red]")

    def stop(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

    def _goto(self, url: str, sleep: float = 2.0):
        self.driver.get(url)
        time.sleep(sleep)

    def _human_type(self, element, text: str):
        for ch in text:
            element.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.15))

    def _dismiss_popups(self):
        for label in ["Not Now", "Not now", "Skip", "Later", "Close", "Allow"]:
            try:
                btn = self.driver.find_element(
                    By.XPATH, f"//button[text()='{label}']"
                )
                btn.click()
                time.sleep(1)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Scrape followers of a competitor
    # ------------------------------------------------------------------

    def scrape_competitor_followers(self, username: str, max_users: int = 300) -> int:
        console.print(f"\n[bold cyan]Scraping followers of @{username}...[/bold cyan]")
        self._goto(f"https://www.instagram.com/{username}/")
        time.sleep(3)
        self._dismiss_popups()

        # Click the Followers count link
        try:
            followers_link = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@href,'/followers/')]")
            ))
            followers_link.click()
            time.sleep(2)
        except TimeoutException:
            console.print(f"[red]Could not open followers for @{username}. Private account?[/red]")
            return 0

        scraped = set()
        scroll_box = None

        # Find the scrollable followers modal
        for _ in range(10):
            try:
                scroll_box = self.driver.find_element(
                    By.XPATH,
                    "//div[@role='dialog']//div[contains(@style,'overflow')]"
                )
                break
            except NoSuchElementException:
                time.sleep(1)

        if not scroll_box:
            console.print("[red]Could not find followers list.[/red]")
            return 0

        console.print(f"[cyan]Scrolling followers list (target: {max_users})...[/cyan]")
        last_count = 0
        stale_rounds = 0

        while len(scraped) < max_users and stale_rounds < 5:
            # Extract visible usernames
            links = self.driver.find_elements(
                By.XPATH,
                "//div[@role='dialog']//a[contains(@href,'/') and @role='link']"
            )
            for link in links:
                href = link.get_attribute("href") or ""
                uname = href.strip("/").split("/")[-1]
                if uname and uname not in scraped and not uname.startswith("http"):
                    scraped.add(uname)
                    db.upsert_lead(uname, source=f"follower:@{username}")
                    db.add_to_follow_queue(uname, source=f"follower:@{username}")

            # Scroll down inside the modal
            self.driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight", scroll_box
            )
            time.sleep(random.uniform(1.5, 2.5))

            if len(scraped) == last_count:
                stale_rounds += 1
            else:
                stale_rounds = 0
                last_count = len(scraped)
                console.print(f"  [dim]{len(scraped)} users scraped...[/dim]")

        db.update_competitor_scraped(username)
        db.log_action("scrape", username, "success", f"{len(scraped)} followers")
        console.print(f"[green]Done — {len(scraped)} followers scraped from @{username}.[/green]")
        return len(scraped)

    # ------------------------------------------------------------------
    # Scrape Reel engagers (likers via URL)
    # ------------------------------------------------------------------

    def scrape_reel(self, reel_url: str) -> int:
        console.print(f"\n[bold cyan]Scraping Reel: {reel_url}[/bold cyan]")
        self._goto(reel_url, sleep=3)
        self._dismiss_popups()

        # Click the likes count to open the likers modal
        added = 0
        try:
            like_btn = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(@class,'like') or contains(text(),'like')]//ancestor::button")
            ))
            like_btn.click()
            time.sleep(2)
        except Exception:
            # Try clicking the likes number directly
            try:
                self.driver.find_element(
                    By.XPATH, "//a[contains(@href, '/liked_by/')]"
                ).click()
                time.sleep(2)
            except Exception:
                console.print("[yellow]Could not open likers — scraping commenters instead.[/yellow]")

        # Grab usernames from modal or comments
        links = self.driver.find_elements(
            By.XPATH,
            "//a[@role='link' and contains(@href,'/') and not(contains(@href,'explore'))]"
        )
        for link in links:
            href = link.get_attribute("href") or ""
            parts = [p for p in href.strip("/").split("/") if p]
            if parts:
                uname = parts[-1]
                if uname and len(uname) > 1 and "instagram" not in uname:
                    db.upsert_lead(uname, source=f"reel:{reel_url}")
                    db.add_to_follow_queue(uname, source=f"reel:{reel_url}")
                    added += 1

        console.print(f"[green]{added} users added from Reel.[/green]")
        return added

    # ------------------------------------------------------------------
    # Hashtag scraping
    # ------------------------------------------------------------------

    def scrape_hashtag(self, hashtag: str, max_posts: int = 30) -> int:
        console.print(f"\n[bold cyan]Scraping #{hashtag}...[/bold cyan]")
        self._goto(f"https://www.instagram.com/explore/tags/{hashtag}/", sleep=3)
        self._dismiss_popups()

        added = 0
        post_links = []

        # Collect post links from the grid
        posts = self.driver.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")
        for p in posts[:max_posts]:
            href = p.get_attribute("href")
            if href and href not in post_links:
                post_links.append(href)

        console.print(f"[dim]Found {len(post_links)} posts. Opening each...[/dim]")

        for post_url in post_links[:max_posts]:
            try:
                self._goto(post_url, sleep=2)
                # Grab the poster's username
                user_link = self.driver.find_element(
                    By.XPATH,
                    "//article//header//a[@role='link']"
                )
                href = user_link.get_attribute("href") or ""
                uname = href.strip("/").split("/")[-1]
                if uname:
                    db.upsert_lead(uname, source=f"hashtag:#{hashtag}")
                    db.add_to_follow_queue(uname, source=f"hashtag:#{hashtag}")
                    added += 1
                    console.print(f"  [dim]@{uname}[/dim]")
                time.sleep(random.uniform(1, 2))
            except Exception:
                pass

        console.print(f"[green]{added} users added from #{hashtag}.[/green]")
        return added

    # ------------------------------------------------------------------
    # Follow queue
    # ------------------------------------------------------------------

    def run_follow_queue(self, batch_size: int = 20) -> int:
        if not rl.can_do("follow"):
            console.print("[yellow]Daily follow limit reached.[/yellow]")
            return 0

        queue = db.get_pending_follows(limit=batch_size)
        done = 0

        for item in queue:
            if not rl.can_do("follow"):
                break
            uname = item["username"]
            console.print(f"  Following @{uname}...")
            try:
                self._goto(f"https://www.instagram.com/{uname}/", sleep=2)
                self._dismiss_popups()

                follow_btn = self.wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//button[text()='Follow' or text()='Follow Back']")
                ))
                follow_btn.click()
                time.sleep(1)

                db.mark_follow_done(item["id"], "done")
                db.log_action("follow", uname, "success")
                conn = db.get_conn()
                conn.execute(
                    "UPDATE leads SET followed_at=datetime('now') WHERE username=?", (uname,)
                )
                conn.commit()
                conn.close()
                console.print(f"  [green]Followed @{uname}[/green]")
                done += 1
                rl.human_delay("follow")

            except TimeoutException:
                console.print(f"  [yellow]No Follow button for @{uname} (private/already following)[/yellow]")
                db.mark_follow_done(item["id"], "skipped")
                db.log_action("follow", uname, "skipped")
            except Exception as e:
                console.print(f"  [red]Error following @{uname}: {e}[/red]")
                db.mark_follow_done(item["id"], "skipped")
                db.log_action("follow", uname, "failed", str(e))

        console.print(f"[green]Follow batch done: {done} followed.[/green]")
        return done

    # ------------------------------------------------------------------
    # Story viewing
    # ------------------------------------------------------------------

    def view_stories(self, usernames: list, limit: int = 30) -> int:
        viewed = 0
        for uname in usernames[:limit]:
            if not rl.can_do("story_view"):
                console.print("[yellow]Daily story view limit reached.[/yellow]")
                break
            try:
                console.print(f"  Viewing story of @{uname}...")
                self._goto(f"https://www.instagram.com/{uname}/", sleep=2)
                self._dismiss_popups()

                # Click the profile picture / story ring
                story_btn = self.driver.find_element(
                    By.XPATH,
                    "//header//canvas/.. | //header//img[@alt]/ancestor::button"
                )
                story_btn.click()
                time.sleep(3)

                # Let story play then press → to advance / close
                self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_RIGHT)
                time.sleep(2)
                self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)

                db.log_action("story_view", uname, "success")
                console.print(f"  [cyan]Viewed story of @{uname}[/cyan]")
                viewed += 1
                rl.human_delay("story_view")

            except Exception:
                # Story may not exist — skip silently
                db.log_action("story_view", uname, "skipped")

        console.print(f"[green]Story views done: {viewed}[/green]")
        return viewed

    def view_stories_of_leads(self, limit: int = 30) -> int:
        leads = db.get_leads(limit=limit, followed=True)
        usernames = [l["username"] for l in leads]
        return self.view_stories(usernames, limit=limit)

    # ------------------------------------------------------------------
    # Like posts
    # ------------------------------------------------------------------

    def like_recent_posts(self, username: str, count: int = 3) -> int:
        console.print(f"  Liking posts of @{username}...")
        liked = 0
        try:
            self._goto(f"https://www.instagram.com/{username}/", sleep=2)
            self._dismiss_popups()
            posts = self.driver.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")
            for post in posts[:count]:
                if not rl.can_do("like"):
                    break
                href = post.get_attribute("href")
                self._goto(href, sleep=2)
                try:
                    # Find the heart/like button
                    like_btn = self.driver.find_element(
                        By.XPATH,
                        "//span[@aria-label='Like' or @aria-label='like']/ancestor::button"
                    )
                    like_btn.click()
                    db.log_action("like", href, "success", username)
                    console.print(f"    [green]Liked post[/green]")
                    liked += 1
                    rl.human_delay("like")
                except Exception:
                    pass
        except Exception as e:
            console.print(f"  [red]Like error: {e}[/red]")
        return liked

    # ------------------------------------------------------------------
    # Send DM
    # ------------------------------------------------------------------

    def run_dm_queue(self, batch_size: int = 10) -> int:
        if not rl.can_do("dm"):
            console.print("[yellow]Daily DM limit reached.[/yellow]")
            return 0

        queue = db.get_pending_dms(limit=batch_size)
        sent = 0

        for item in queue:
            if not rl.can_do("dm"):
                break
            uname = item["username"]
            msg   = item["message"]
            console.print(f"  Sending DM to @{uname}...")
            try:
                self._goto(f"https://www.instagram.com/direct/new/", sleep=3)
                self._dismiss_popups()

                # Search for recipient
                search = self.wait.until(EC.presence_of_element_located(
                    (By.XPATH, "//input[@placeholder='Search...']")
                ))
                self._human_type(search, uname)
                time.sleep(2)

                # Click the first result
                result = self.wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//div[@role='button' and .//span[contains(text(),'" + uname + "')]]")
                ))
                result.click()
                time.sleep(1)

                next_btn = self.driver.find_element(
                    By.XPATH, "//button[text()='Next' or text()='Chat']"
                )
                next_btn.click()
                time.sleep(2)

                # Type and send message
                msg_box = self.wait.until(EC.presence_of_element_located(
                    (By.XPATH, "//div[@role='textbox' and @aria-label='Message']")
                ))
                self._human_type(msg_box, msg)
                time.sleep(0.5)
                msg_box.send_keys(Keys.RETURN)
                time.sleep(1)

                db.mark_dm_done(item["id"], "sent")
                db.log_action("dm", uname, "success")
                conn = db.get_conn()
                conn.execute(
                    "UPDATE leads SET dm_sent_at=datetime('now') WHERE username=?", (uname,)
                )
                conn.commit()
                conn.close()
                console.print(f"  [green]DM sent to @{uname}[/green]")
                sent += 1
                rl.human_delay("dm")

            except Exception as e:
                db.mark_dm_done(item["id"], "failed", str(e))
                db.log_action("dm", uname, "failed", str(e))
                console.print(f"  [red]DM failed for @{uname}: {e}[/red]")

        console.print(f"[green]DM batch done: {sent} sent.[/green]")
        return sent

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def print_stats(self):
        from rich.table import Table
        t = Table(title="Today's Action Stats")
        t.add_column("Action")
        t.add_column("Done today", justify="right")
        t.add_column("Limit", justify="right")
        t.add_column("Remaining", justify="right")
        for action, limit in rl.DAILY_LIMITS.items():
            done = db.actions_today(action)
            t.add_row(action, str(done), str(limit), str(max(0, limit - done)))
        console.print(t)
