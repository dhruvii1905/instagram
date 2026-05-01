import os
import json
import random
import time
from pathlib import Path
from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired, BadPassword,
    TwoFactorRequired, UserNotFound, MediaNotFound,
)
from rich.console import Console

import database as db
import rate_limiter as rl

load_dotenv()
console = Console()

USERNAME    = os.getenv("INSTAGRAM_USERNAME")
PASSWORD    = os.getenv("INSTAGRAM_PASSWORD")
SESSION_ID  = os.getenv("INSTAGRAM_SESSION_ID", "")
SESSION_FILE = os.getenv("SESSION_FILE", "session.json")
PROXY        = os.getenv("PROXY", "")


class InstagramScraper:
    def __init__(self):
        self.cl = Client()
        if PROXY:
            self.cl.set_proxy(PROXY)
        self.cl.delay_range = [2, 5]
        # Set a realistic Android device fingerprint before any request
        self.cl.set_device({
            "app_version": "269.0.0.18.75",
            "android_version": 26,
            "android_release": "8.0.0",
            "dpi": "480dpi",
            "resolution": "1080x1920",
            "manufacturer": "OnePlus",
            "device": "devitron",
            "model": "6T Dev",
            "cpu": "qcom",
            "version_code": "314665256",
        })
        self.cl.set_user_agent()
        self._logged_in = False

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self) -> bool:
        session_path = Path(SESSION_FILE)

        # 1. Saved session.json — re-inject the cookie directly, no API call
        if session_path.exists() and SESSION_ID:
            self._inject_session(SESSION_ID, session_path)
            console.print("[green]Logged in via saved session.[/green]")
            return True

        # 2. sessionid in .env — inject directly, no API call needed
        if SESSION_ID:
            try:
                self._inject_session(SESSION_ID, session_path)
                console.print("[green]Logged in via session ID cookie.[/green]")
                return True
            except Exception as e:
                console.print(f"[yellow]Session ID inject failed: {e}[/yellow]")

        # 3. Username + password login
        try:
            self.cl.login(USERNAME, PASSWORD)
            self.cl.dump_settings(session_path)
            console.print("[green]Login successful. Session saved.[/green]")
            self._logged_in = True
            return True
        except BadPassword:
            console.print("[red]Wrong password.[/red]")
        except TwoFactorRequired:
            code = input("2FA code: ").strip()
            self.cl.login(USERNAME, PASSWORD, verification_code=code)
            self.cl.dump_settings(session_path)
            self._logged_in = True
            return True
        except ChallengeRequired as e:
            console.print(f"[red]Instagram challenge required: {e}[/red]")
            console.print("[yellow]Open the Instagram app, complete the security check, then retry.[/yellow]")
        except Exception as e:
            if "Expecting value" in str(e):
                console.print("[yellow]API login blocked — launching Chrome to get session automatically...[/yellow]")
                return self._browser_login()
            console.print(f"[red]Login error: {e}[/red]")
        return False

    def _browser_login(self) -> bool:
        """
        Open Chrome, wait for manual login, then auto-capture the sessionid cookie.
        No form automation — avoids all bot-detection crashes.
        """
        import re
        import time as _time

        try:
            import undetected_chromedriver as uc
        except ImportError:
            console.print("[red]Run: pip install undetected-chromedriver[/red]")
            return False

        console.print("\n[bold cyan]Chrome will open — log in to Instagram MANUALLY in that window.[/bold cyan]")
        console.print("[yellow]The script will auto-capture your session once you are logged in.[/yellow]\n")

        options = uc.ChromeOptions()
        options.add_argument("--window-size=1100,900")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        driver = uc.Chrome(options=options, use_subprocess=True, version_main=147)

        try:
            driver.get("https://www.instagram.com/accounts/login/")
            console.print("[cyan]Waiting for you to log in... (up to 3 minutes)[/cyan]")

            # Poll every 3 seconds until sessionid cookie appears
            deadline = _time.time() + 180
            session_id = None
            while _time.time() < deadline:
                _time.sleep(3)
                try:
                    for cookie in driver.get_cookies():
                        if cookie["name"] == "sessionid" and cookie["value"]:
                            session_id = cookie["value"]
                            break
                    if session_id:
                        break
                except Exception:
                    pass

            if not session_id:
                console.print("[red]Timed out — no login detected. Please try again.[/red]")
                return False

            console.print("[green]Login detected! Saving session...[/green]")
            _time.sleep(1)

            # Save to .env
            env_path = Path(".env")
            env_text = env_path.read_text() if env_path.exists() else ""
            if "INSTAGRAM_SESSION_ID=" in env_text:
                env_text = re.sub(
                    r"INSTAGRAM_SESSION_ID=.*",
                    f"INSTAGRAM_SESSION_ID={session_id}",
                    env_text,
                )
            else:
                env_text += f"\nINSTAGRAM_SESSION_ID={session_id}\n"
            env_path.write_text(env_text)
            console.print("[green]sessionid saved to .env[/green]")

            # Inject session directly — no mobile API call, bypasses IP block
            self._inject_session(session_id, Path(SESSION_FILE))
            console.print("[bold green]Login successful![/bold green]")
            return True

        except Exception as e:
            console.print(f"[red]Browser login error: {e}[/red]")
            return False
        finally:
            _time.sleep(1)
            try:
                driver.quit()
            except Exception:
                pass

    def _inject_session(self, session_id: str, save_path: Path):
        """
        Set sessionid directly in instagrapi without making any API call.
        Works even when the mobile API endpoint is IP-blocked.
        """
        # Inject into the underlying requests session
        self.cl.private.cookies.set("sessionid", session_id, domain=".instagram.com")

        # Update instagrapi's internal settings dict
        settings = self.cl.get_settings()
        settings.setdefault("cookies", {})["sessionid"] = session_id
        settings.setdefault("authorization_data", {})["sessionid"] = session_id
        self.cl.set_settings(settings)

        self.cl.dump_settings(save_path)
        self._logged_in = True

    def _require_login(self):
        if not self._logged_in:
            raise RuntimeError("Not logged in. Call login() first.")

    # ------------------------------------------------------------------
    # User info
    # ------------------------------------------------------------------

    def get_user_id(self, username: str) -> str | None:
        try:
            return str(self.cl.user_id_from_username(username))
        except UserNotFound:
            return None

    def get_user_info(self, username: str) -> dict | None:
        try:
            uid = self.get_user_id(username)
            if not uid:
                return None
            info = self.cl.user_info(uid)
            return {
                "username":        info.username,
                "user_id":         str(info.pk),
                "full_name":       info.full_name,
                "bio":             info.biography,
                "follower_count":  info.follower_count,
                "following_count": info.following_count,
                "post_count":      info.media_count,
                "is_private":      info.is_private,
            }
        except Exception as e:
            console.print(f"[red]get_user_info({username}): {e}[/red]")
            return None

    # ------------------------------------------------------------------
    # Competitor follower scraping
    # ------------------------------------------------------------------

    def scrape_competitor_followers(self, competitor_username: str,
                                    max_users: int = 500) -> int:
        """
        Scrape followers of competitor_username, store them as leads and
        add them to the follow queue.  Returns number of users added.
        """
        self._require_login()
        console.print(f"[cyan]Scraping followers of @{competitor_username}...[/cyan]")

        uid = self.get_user_id(competitor_username)
        if not uid:
            console.print(f"[red]User @{competitor_username} not found.[/red]")
            return 0

        added = 0
        try:
            followers = self.cl.user_followers(uid, amount=max_users)
            for user in followers.values():
                uname = user.username
                db.upsert_lead(
                    username=uname,
                    user_id=str(user.pk),
                    full_name=user.full_name,
                    is_private=user.is_private,
                    source=f"follower:@{competitor_username}",
                )
                db.add_to_follow_queue(uname, str(user.pk),
                                       source=f"follower:@{competitor_username}")
                added += 1
                db.log_action("scrape", uname, "success",
                              f"follower of @{competitor_username}")
                if added % 50 == 0:
                    console.print(f"  [dim]{added} scraped so far...[/dim]")
                rl.human_delay("scrape")

            db.update_competitor_scraped(competitor_username)
            console.print(f"[green]Done. {added} followers scraped from @{competitor_username}.[/green]")
        except Exception as e:
            console.print(f"[red]Error scraping followers: {e}[/red]")
            db.log_action("scrape", competitor_username, "failed", str(e))

        return added

    # ------------------------------------------------------------------
    # Reel engagement scraping
    # ------------------------------------------------------------------

    def scrape_reel_engagers(self, reel_url_or_code: str,
                             include_likers: bool = True,
                             include_commenters: bool = True) -> int:
        """
        Scrape users who liked/commented on a Reel and add them to leads + follow queue.
        Pass the short code (e.g. 'C123abc') or full URL.
        """
        self._require_login()
        # Extract media_pk from URL or short code
        try:
            if reel_url_or_code.startswith("http"):
                media_pk = self.cl.media_pk_from_url(reel_url_or_code)
            else:
                media_pk = self.cl.media_pk_from_code(reel_url_or_code)
        except Exception as e:
            console.print(f"[red]Could not resolve reel: {e}[/red]")
            return 0

        source = f"reel:{media_pk}"
        added = 0

        if include_likers:
            try:
                likers = self.cl.media_likers(media_pk)
                for user in likers:
                    db.upsert_lead(user.username, str(user.pk),
                                   full_name=user.full_name, source=source)
                    db.add_to_follow_queue(user.username, str(user.pk), source=source)
                    added += 1
                    rl.human_delay("scrape")
                console.print(f"[green]{len(likers)} likers scraped.[/green]")
            except MediaNotFound:
                console.print("[red]Reel not found or private.[/red]")
            except Exception as e:
                console.print(f"[red]Likers error: {e}[/red]")

        if include_commenters:
            try:
                comments = self.cl.media_comments(media_pk, amount=200)
                for c in comments:
                    uname = c.user.username
                    db.upsert_lead(uname, str(c.user.pk),
                                   full_name=c.user.full_name, source=source)
                    db.add_to_follow_queue(uname, str(c.user.pk), source=source)
                    added += 1
                    rl.human_delay("scrape")
                console.print(f"[green]{len(comments)} commenters scraped.[/green]")
            except Exception as e:
                console.print(f"[red]Commenters error: {e}[/red]")

        return added

    # ------------------------------------------------------------------
    # Hashtag / location scraping
    # ------------------------------------------------------------------

    def scrape_hashtag(self, hashtag: str, max_posts: int = 50) -> int:
        """Scrape recent posters of a hashtag as leads."""
        self._require_login()
        console.print(f"[cyan]Scraping #{hashtag}...[/cyan]")
        added = 0
        try:
            medias = self.cl.hashtag_medias_recent(hashtag, amount=max_posts)
            for media in medias:
                uname = media.user.username
                db.upsert_lead(uname, str(media.user.pk),
                               full_name=media.user.full_name,
                               source=f"hashtag:#{hashtag}")
                db.add_to_follow_queue(uname, str(media.user.pk),
                                       source=f"hashtag:#{hashtag}")
                added += 1
                rl.human_delay("scrape")
        except Exception as e:
            console.print(f"[red]Hashtag scrape error: {e}[/red]")
        console.print(f"[green]{added} leads from #{hashtag}.[/green]")
        return added

    # ------------------------------------------------------------------
    # Follow queue execution
    # ------------------------------------------------------------------

    def run_follow_queue(self, batch_size: int = 20) -> int:
        """
        Process up to batch_size follows from the queue, respecting daily limit.
        Returns number of follows performed.
        """
        self._require_login()
        remaining = rl.remaining("follow")
        to_do = min(batch_size, remaining)
        if to_do == 0:
            console.print("[yellow]Daily follow limit reached.[/yellow]")
            return 0

        queue = db.get_pending_follows(limit=to_do)
        done = 0
        for item in queue:
            if not rl.can_do("follow"):
                break
            uname = item["username"]
            uid   = item["user_id"]
            try:
                if not uid:
                    uid = self.get_user_id(uname)
                if uid:
                    self.cl.user_follow(int(uid))
                    db.mark_follow_done(item["id"], "done")
                    db.log_action("follow", uname, "success")
                    # also update the lead record
                    conn = db.get_conn()
                    conn.execute(
                        "UPDATE leads SET followed_at=datetime('now') WHERE username=?",
                        (uname,)
                    )
                    conn.commit()
                    conn.close()
                    console.print(f"  [green]Followed @{uname}[/green]")
                    done += 1
                    rl.human_delay("follow")
                else:
                    db.mark_follow_done(item["id"], "skipped")
                    db.log_action("follow", uname, "skipped", "uid not found")
            except Exception as e:
                db.mark_follow_done(item["id"], "skipped")
                db.log_action("follow", uname, "failed", str(e))
                console.print(f"  [red]Follow @{uname} failed: {e}[/red]")
                time.sleep(random.randint(10, 30))

        console.print(f"[green]Follow batch done: {done} followed.[/green]")
        return done

    # ------------------------------------------------------------------
    # Story viewing
    # ------------------------------------------------------------------

    def view_stories(self, usernames: list[str]) -> int:
        """
        View stories of the given users. Respects daily story_view limit.
        Returns number of story views performed.
        """
        self._require_login()
        viewed = 0
        for uname in usernames:
            if not rl.can_do("story_view"):
                console.print("[yellow]Daily story view limit reached.[/yellow]")
                break
            try:
                uid = self.get_user_id(uname)
                if not uid:
                    continue
                stories = self.cl.user_stories(int(uid))
                if not stories:
                    continue
                story_ids = [s.pk for s in stories]
                self.cl.story_seen(story_ids)
                db.log_action("story_view", uname, "success",
                              f"{len(story_ids)} stories")
                console.print(f"  [cyan]Viewed {len(story_ids)} stories of @{uname}[/cyan]")
                viewed += len(story_ids)
                rl.human_delay("story_view")
            except Exception as e:
                db.log_action("story_view", uname, "failed", str(e))
                console.print(f"  [red]Story view @{uname} failed: {e}[/red]")

        console.print(f"[green]Story views done: {viewed} stories viewed.[/green]")
        return viewed

    def view_stories_of_leads(self, limit: int = 30) -> int:
        """View stories of leads who have been followed but not yet DM'd."""
        leads = db.get_leads(limit=limit, followed=True)
        usernames = [l["username"] for l in leads]
        return self.view_stories(usernames)

    # ------------------------------------------------------------------
    # DM queue execution
    # ------------------------------------------------------------------

    def run_dm_queue(self, batch_size: int = 10) -> int:
        """
        Send DMs from the queue. Respects daily DM limit.
        Returns number of DMs sent.
        """
        self._require_login()
        remaining = rl.remaining("dm")
        to_do = min(batch_size, remaining)
        if to_do == 0:
            console.print("[yellow]Daily DM limit reached.[/yellow]")
            return 0

        queue = db.get_pending_dms(limit=to_do)
        sent = 0
        for item in queue:
            if not rl.can_do("dm"):
                break
            uname = item["username"]
            uid   = item["user_id"]
            msg   = item["message"]
            try:
                if not uid:
                    uid = self.get_user_id(uname)
                if uid:
                    self.cl.direct_send(msg, [int(uid)])
                    db.mark_dm_done(item["id"], "sent")
                    db.log_action("dm", uname, "success")
                    # update lead record
                    conn = db.get_conn()
                    conn.execute(
                        "UPDATE leads SET dm_sent_at=datetime('now') WHERE username=?",
                        (uname,)
                    )
                    conn.commit()
                    conn.close()
                    console.print(f"  [green]DM sent to @{uname}[/green]")
                    sent += 1
                    rl.human_delay("dm")
                else:
                    db.mark_dm_done(item["id"], "failed", "uid not found")
            except Exception as e:
                db.mark_dm_done(item["id"], "failed", str(e))
                db.log_action("dm", uname, "failed", str(e))
                console.print(f"  [red]DM @{uname} failed: {e}[/red]")
                time.sleep(random.randint(30, 90))

        console.print(f"[green]DM batch done: {sent} sent.[/green]")
        return sent

    # ------------------------------------------------------------------
    # Like posts
    # ------------------------------------------------------------------

    def like_recent_posts(self, username: str, count: int = 3) -> int:
        """Like the most recent `count` posts of a user."""
        self._require_login()
        liked = 0
        try:
            uid = self.get_user_id(username)
            if not uid:
                return 0
            medias = self.cl.user_medias(int(uid), amount=count)
            for media in medias:
                if not rl.can_do("like"):
                    break
                self.cl.media_like(media.pk)
                db.log_action("like", str(media.pk), "success", username)
                liked += 1
                rl.human_delay("like")
        except Exception as e:
            db.log_action("like", username, "failed", str(e))
            console.print(f"[red]Like error for @{username}: {e}[/red]")
        return liked

    # ------------------------------------------------------------------
    # Trending audio detection
    # ------------------------------------------------------------------

    def get_trending_audio(self, limit: int = 20) -> list[dict]:
        """
        Fetch trending Reels and extract audio info.
        Returns list of {title, artist, usage_count, audio_id}.
        """
        self._require_login()
        results = []
        try:
            reels = self.cl.reels_tray()
            seen_audio = set()
            for reel in reels[:limit]:
                for item in (reel.get("items") or []):
                    music = item.get("music_metadata") or {}
                    track = music.get("music_info", {}).get("music_asset_info", {})
                    audio_id = track.get("audio_id")
                    if audio_id and audio_id not in seen_audio:
                        seen_audio.add(audio_id)
                        results.append({
                            "audio_id":    audio_id,
                            "title":       track.get("title", ""),
                            "artist":      track.get("display_artist", ""),
                            "duration_ms": track.get("duration_in_ms", 0),
                        })
        except Exception as e:
            console.print(f"[red]Trending audio error: {e}[/red]")
        return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def print_daily_stats(self):
        from rich.table import Table
        table = Table(title="Today's Action Stats")
        table.add_column("Action")
        table.add_column("Done today", justify="right")
        table.add_column("Daily limit", justify="right")
        table.add_column("Remaining", justify="right")
        for action, limit in rl.DAILY_LIMITS.items():
            done = db.actions_today(action)
            rem  = max(0, limit - done)
            table.add_row(action, str(done), str(limit), str(rem))
        console.print(table)
