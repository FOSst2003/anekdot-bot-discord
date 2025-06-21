import os
import time
import random
import logging
import json
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import requests

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def retry_with_backoff(max_retries=5, base_delay=5, max_delay=60):
    def decorator(func):
        def wrapper(*args, **kwargs):
            retries = 0
            delay = base_delay
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionResetError) as e:
                    logging.warning(f"Сетевая ошибка: {e}. Повтор через {delay} сек.")
                    time.sleep(delay)
                    retries += 1
                    delay = min(delay * 2, max_delay)
            logging.error("Превышено количество попыток. Прерываю операцию.")
            return None
        return wrapper
    return decorator

def check_internet_connection():
    try:
        requests.get("https://www.google.com",  timeout=5)
        return True
    except requests.exceptions.ConnectionError:
        return False

def load_sent():
    if not os.path.exists(SENT_FILE):
        return {}
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            if os.path.getsize(SENT_FILE) == 0:
                logging.warning("Файл sent_anekdots.json пустой")
                return {}
            return json.load(f)
    except Exception as e:
        logging.error(f"Ошибка чтения файла {SENT_FILE}: {e}")
        return {}

def save_sent(sent_dict):
    try:
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            json.dump(sent_dict, f, ensure_ascii=False, indent=2)
        logging.info("Сохранены отправленные анекдоты")
    except Exception as e:
        logging.error(f"Ошибка записи файла {SENT_FILE}: {e}")

def scan_categories():
    logging.info("Сканирование категорий...")
    headers = {"User-Agent": "Mozilla/5.0"}
    result = {}
    total = len(CATEGORIES)
    processed = 0
    MAX_LINE_LENGTH = 80
    print(f"Сканирование: 0/{total} категорий".ljust(MAX_LINE_LENGTH), end="", flush=True)

    for category in CATEGORIES:
        page = 1
        while True:
            url = f"{BASE_URL}/{category}/" + (f"{page}/" if page > 1 else "")
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 404:
                    break
            except Exception as e:
                logging.error(f"Ошибка при сканировании {url}: {e}")
                break
            page += 1
            time.sleep(0.1)
        result[category] = page - 1
        processed += 1
        print(f"\rСканирование: {processed}/{total} категорий обработано".ljust(MAX_LINE_LENGTH), end="", flush=True)

    print(f"\r{'Категории обновлены.'.ljust(MAX_LINE_LENGTH)}")
    now = datetime.now()
    data = {
        "last_scan": now.isoformat(),
        "next_scan": (now + timedelta(hours=CHECK_INTERVAL_HOURS)).isoformat(),
        "categories": result
    }

    try:
        with open(CATEGORY_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info("Категории обновлены")
    except Exception as e:
        logging.error(f"Ошибка записи файла {CATEGORY_MAP_FILE}: {e}")

    return data

def load_category_map():
    if os.path.exists(CATEGORY_MAP_FILE):
        try:
            with open(CATEGORY_MAP_FILE, "r", encoding="utf-8") as f:
                if os.path.getsize(CATEGORY_MAP_FILE) == 0:
                    logging.warning("Файл category_pages.json пустой")
                    return scan_categories()
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка чтения файла {CATEGORY_MAP_FILE}: {e}")
    return scan_categories()

@retry_with_backoff()
def get_anekdots_from_category(category, max_pages):
    headers = {"User-Agent": "Mozilla/5.0"}

    if category == "svegie-anekdoty":
        date = (datetime.now() - timedelta(days=random.randint(0, 30))).strftime("%Y-%m-%d")
        url = f"{BASE_URL}/date/{date}/"
    else:
        page = random.randint(1, max_pages)
        url = f"{BASE_URL}/{category}/" + (f"{page}/" if page > 1 else "")

    try:
        logging.info(f"Парсинг: {url}")
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        anekdots = []

        for block in soup.find_all("div", class_="anekdot-text"):
            p = block.find("p")
            if not p:
                continue

            text = p.get_text("\n", strip=True)
            parent_div = block.find_parent("div", class_="row")

            if not parent_div:
                continue

            vote_span = parent_div.find("span", id=lambda x: x and x.startswith("anekdot"))

            if not vote_span:
                continue

            anek_id = vote_span.get("id").replace("anekdot", "")
            link_tag = parent_div.find("a", href=True)

            if link_tag:
                href = link_tag["href"]
                if href.startswith(("http://", "https://")):
                    url = href
                else:
                    url = BASE_URL + href
            else:
                url = None

            anekdots.append({"id": anek_id, "text": text})

        return anekdots
    except Exception as e:
        logging.error(f"Ошибка парсинга {category}: {e}")
        return []

@retry_with_backoff()
def send_to_discord(text):
    content = text[:1990] + "..." if len(text) > 1990 else text
    payload = {"content": content}

    for attempt in range(1, 6):
        try:
            r = requests.post(WEBHOOK_URL, json=payload)
            if r.status_code == 204:
                logging.info(f"Отправлено (попытка {attempt})")
                return True
            logging.warning(f"Код ответа {r.status_code} (попытка {attempt})")
        except Exception as e:
            logging.error(f"Ошибка отправки (попытка {attempt}): {e}")
        time.sleep(10)

    return False

def main():
    if not WEBHOOK_URL:
        logging.critical("WEBHOOK_URL не установлен!")
        return

    sent = load_sent()
    data = load_category_map()
    category_map = data.get("categories", {})
    next_scan = datetime.fromisoformat(data.get("next_scan", "1970-01-01T00:00:00"))

    logging.info(f"Интервал между отправками: {MESSAGE_INTERVAL_SECONDS} секунд")
    logging.info("Бот запущен")

    try:
        while True:
            if not check_internet_connection():
                logging.warning("Нет подключения к интернету. Ожидание 5 минут...")
                time.sleep(300)
                continue

            current_time = datetime.now()

            if current_time >= next_scan:
                logging.info("Обновление карты категорий")
                data = scan_categories()
                category_map = data["categories"]
                next_scan = datetime.fromisoformat(data["next_scan"])

            category = random.choice(CATEGORIES)
            anekdots = get_anekdots_from_category(category, category_map.get(category, 10))
            category_sent = sent.get(category, {})
            new = [a for a in anekdots if a["id"] not in category_sent]

            if new:
                a = random.choice(new)
                if send_to_discord(a["text"]):
                    sent.setdefault(category, {})[a["id"]] = {"text": a["text"]}
                    save_sent(sent)
            else:
                logging.info("Нет новых анекдотов.")

            time.sleep(MESSAGE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную (Ctrl + C)")
        exit(0)

if __name__ == "__main__":
    main()