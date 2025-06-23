import os
import time
import random
import logging
import json
from typing import Any, Dict, List, Optional, Callable
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://anekdotovstreet.com"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "12"))
SENT_FILE = "sent_anekdots.json"
CATEGORY_MAP_FILE = "category_pages.json"

try:
    MESSAGE_INTERVAL_SECONDS = int(os.getenv("MESSAGE_INTERVAL_SECONDS", "1800"))
except ValueError:
    MESSAGE_INTERVAL_SECONDS = 1800

if MESSAGE_INTERVAL_SECONDS < 60:
    MESSAGE_INTERVAL_SECONDS = 60
elif MESSAGE_INTERVAL_SECONDS > 86400:
    MESSAGE_INTERVAL_SECONDS = 86400

CATEGORIES = [
    "korotkie-anekdoty", "detskie-anekdoty", "armiya", "blondinki", "vovochka",
    "dengi", "eda-napitki", "genskie", "givotnye", "zakon", "znamenitosti", "igry",
    "istoricheskie", "kompyuternye", "literaturnye-pro-pisateley", "medicinskie",
    "mugskie", "multiki-skazki", "nacionalnosti", "novye-russkie", "personagi",
    "politicheskie", "poshlye", "prazdniki", "rabota-professii", "religiya",
    "sverhestestvennoe", "semeynye", "sport", "starye-sovetskie", "studenty",
    "transport", "turisty", "chernyy-yumor", "shkola", "raznoe", "raznoe-2",
    "raznoe-3", "populyarnye-anekdoty", "svegie-anekdoty"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("anekdot_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def retry_with_backoff(base_delay: int = 5, max_delay: int = 300) -> Callable:
    def decorator(func: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            while True:
                try:
                    return func(*args, **kwargs)
                except (
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    ConnectionResetError
                ) as e:
                    logging.warning(f"Network error: {e}. Retrying in {delay} seconds.")
                except Exception as e:
                    logging.error(f"Unexpected error: {e}. Retrying in {delay} seconds.")
                time.sleep(delay)
                delay = min(delay * 2, max_delay)
        return wrapper
    return decorator


def check_internet_connection() -> bool:
    urls = ["https://www.google.com",  "https://discord.com"]
    for url in urls:
        try:
            requests.get(url, timeout=5)
        except requests.exceptions.ConnectionError as e:
            logging.warning(f"No connection to {url}: {e}")
            return False
    return True


def load_sent() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(SENT_FILE):
        return {}
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as file:
            if os.path.getsize(SENT_FILE) == 0:
                return {}
            return json.load(file)
    except Exception as e:
        logging.error(f"Failed to read {SENT_FILE}: {e}")
        return {}


def save_sent(sent_dict: Dict[str, Dict[str, str]]) -> None:
    try:
        with open(SENT_FILE, "w", encoding="utf-8") as file:
            json.dump(sent_dict, file, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to write {SENT_FILE}: {e}")


def cleanup_sent(sent_dict: Dict[str, Dict[str, str]], days: int = 30) -> None:
    cutoff = datetime.now() - timedelta(days=days)
    keys_to_delete = [
        key for key, value in sent_dict.items()
        if datetime.fromisoformat(value["timestamp"]) < cutoff
    ]
    for key in keys_to_delete:
        del sent_dict[key]


def scan_categories() -> Dict[str, Any]:
    logging.info("Scanning categories...")
    headers = {"User-Agent": "Mozilla/5.0"}
    result = {}
    total = len(CATEGORIES)

    for idx, category in enumerate(CATEGORIES, start=1):
        page = 1
        while True:
            url = f"{BASE_URL}/{category}/" + (f"{page}/" if page > 1 else "")
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 404:
                    break
            except Exception as e:
                logging.warning(f"Request error for {url}: {e}")
                break

            page += 1
            time.sleep(0.1)

        result[category] = page - 1

    data = {
        "last_scan": datetime.now().isoformat(),
        "next_scan": (datetime.now() + timedelta(hours=CHECK_INTERVAL_HOURS)).isoformat(),
        "categories": result
    }

    try:
        with open(CATEGORY_MAP_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        logging.info("Categories updated")
    except Exception as e:
        logging.error(f"Failed to write {CATEGORY_MAP_FILE}: {e}")

    return data


def load_category_map() -> Dict[str, Any]:
    if os.path.exists(CATEGORY_MAP_FILE):
        try:
            with open(CATEGORY_MAP_FILE, "r", encoding="utf-8") as file:
                if os.path.getsize(CATEGORY_MAP_FILE) == 0:
                    return scan_categories()
                return json.load(file)
        except Exception as e:
            logging.error(f"Failed to read {CATEGORY_MAP_FILE}: {e}")
    return scan_categories()


@retry_with_backoff()
def get_anekdots_from_category(category: str, max_pages: int) -> List[Dict[str, str]]:
    headers = {"User-Agent": "Mozilla/5.0"}
    if category == "svegie-anekdoty":
        date = (datetime.now() - timedelta(days=random.randint(0, 30))).strftime("%Y-%m-%d")
        url = f"{BASE_URL}/date/{date}/"
    else:
        page = random.randint(1, max_pages)
        url = f"{BASE_URL}/{category}/" + (f"{page}/" if page > 1 else "")

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    anekdots = []

    for block in soup.find_all("div", class_="anekdot-text"):
        parent_div = block.find_parent("div", class_="row")
        if not parent_div:
            continue

        vote_span = parent_div.find("span", id=lambda x: x and x.startswith("anekdot"))
        if not vote_span:
            continue

        anek_id = vote_span.get("id").replace("anekdot", "")
        paragraph = block.find("p")
        if not paragraph:
            continue

        text = paragraph.get_text("\n", strip=True)
        anekdots.append({"id": anek_id, "text": text})

    return anekdots


@retry_with_backoff()
def send_to_discord(text: str) -> bool:
    content = text[:1990] + "..." if len(text) > 1990 else text
    payload = {"content": content}

    response = requests.post(WEBHOOK_URL, json=payload)
    if response.status_code == 204:
        logging.info("Message successfully sent to Discord.")
        return True
    logging.warning(f"Discord returned status code {response.status_code}")
    return False


def main() -> None:
    if not WEBHOOK_URL:
        logging.critical("WEBHOOK_URL is not set.")
        return

    sent = load_sent()
    data = load_category_map()
    category_map = data.get("categories", {})
    next_scan = datetime.fromisoformat(data.get("next_scan", "1970-01-01T00:00:00"))

    logging.info(f"Message interval: {MESSAGE_INTERVAL_SECONDS} seconds")
    logging.info("Bot started.")

    try:
        while True:
            if not check_internet_connection():
                logging.warning("No internet connection. Waiting 10 seconds...")
                time.sleep(10)
                continue

            if datetime.now() >= next_scan:
                data = scan_categories()
                category_map = data["categories"]
                next_scan = datetime.fromisoformat(data["next_scan"])

            category = random.choice(CATEGORIES)
            anekdots = get_anekdots_from_category(category, category_map.get(category, 10))

            new_anekdots = [a for a in anekdots if a["id"] not in sent]
            if new_anekdots:
                selected = random.choice(new_anekdots)
                if send_to_discord(selected["text"]):
                    sent[selected["id"]] = {"timestamp": datetime.now().isoformat(), "text": selected["text"]}
                    cleanup_sent(sent)
                    save_sent(sent)
            else:
                logging.info("No new jokes found.")

            time.sleep(MESSAGE_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logging.info("Bot manually stopped.")


if __name__ == "__main__":
    main()