"""
Run this once to get your Instagram sessionid automatically.
It opens a real Chrome window, logs in, grabs the cookie, saves to .env
"""
import os
import time
import re
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

load_dotenv()
USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")


def save_session_id(session_id: str):
    with open(ENV_FILE, "r") as f:
        content = f.read()

    if "INSTAGRAM_SESSION_ID=" in content:
        content = re.sub(
            r"INSTAGRAM_SESSION_ID=.*",
            f"INSTAGRAM_SESSION_ID={session_id}",
            content,
        )
    else:
        content += f"\nINSTAGRAM_SESSION_ID={session_id}\n"

    with open(ENV_FILE, "w") as f:
        f.write(content)


def get_session():
    print("[*] Starting Chrome...")
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--window-size=1080,900")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    wait = WebDriverWait(driver, 30)

    try:
        print("[*] Opening instagram.com...")
        driver.get("https://www.instagram.com/accounts/login/")
        time.sleep(3)

        # Accept cookies if prompted
        try:
            accept_btn = driver.find_element(
                By.XPATH, "//button[contains(text(),'Allow') or contains(text(),'Accept')]"
            )
            accept_btn.click()
            time.sleep(1)
        except Exception:
            pass

        print(f"[*] Typing username: {USERNAME}")
        user_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        user_field.clear()
        user_field.send_keys(USERNAME)
        time.sleep(0.5)

        pass_field = driver.find_element(By.NAME, "password")
        pass_field.clear()
        pass_field.send_keys(PASSWORD)
        time.sleep(0.5)
        pass_field.send_keys(Keys.RETURN)

        print("[*] Logging in... waiting for home page...")
        # Wait until URL changes away from login page
        wait.until(lambda d: "/accounts/login" not in d.current_url)
        time.sleep(3)

        # Handle "Save login info?" or "Turn on notifications?" popups
        for _ in range(3):
            try:
                not_now = driver.find_element(
                    By.XPATH,
                    "//button[text()='Not Now' or text()='Not now' or text()='Skip']"
                )
                not_now.click()
                time.sleep(2)
            except Exception:
                break

        # Grab the sessionid cookie
        cookies = driver.get_cookies()
        session_id = None
        for cookie in cookies:
            if cookie["name"] == "sessionid":
                session_id = cookie["value"]
                break

        if session_id:
            save_session_id(session_id)
            print(f"\n[OK] sessionid saved to .env successfully!")
            print(f"     Value: {session_id[:20]}...{session_id[-10:]}")
            print("\nNow run:  python main.py login")
        else:
            print("\n[!] Could not find sessionid cookie.")
            print("    Cookies found:", [c["name"] for c in cookies])
            print("    If 2FA appeared, solve it in the browser window and run again.")

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        time.sleep(2)
        driver.quit()


if __name__ == "__main__":
    get_session()
