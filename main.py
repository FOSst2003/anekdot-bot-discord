import os
import requests
from bs4 import BeautifulSoup
import time
import random
import logging
import json
from datetime import datetime, timedelta

BASE_URL = "https://anekdotovstreet.com"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "12"))
SENT_FILE = "sent_anekdots.json"
CATEGORY_MAP_FILE = "category_pages.json"

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


def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            if os.path.getsize(SENT_FILE) == 0:
                logging.warning("Файл sent_anekdots.json пустой")
                return set()
            data = json.load(f)
            return {(c, t) for c, lst in data.items() for t in lst}
    except Exception as e:
        logging.error(f"Ошибка чтения файла {SENT_FILE}: {e}")
        return set()


def save_sent(sent_set):
    try:
        data = {}
        for category, text in sent_set:
            data.setdefault(category, []).append(text)
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info("Сохранены отправленные анекдоты")
    except Exception as e:
        logging.error(f"Ошибка записи файла {SENT_FILE}: {e}")


def scan_categories():
    logging.info("Сканирование категорий...")
    headers = {"User-Agent": "Mozilla/5.0"}
    result = {}

    for category in CATEGORIES:
        for page in range(1, 100):
            url = f"{BASE_URL}/{category}/" + (f"{page}/" if page > 1 else "")
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 404:
                    break
            except Exception as e:
                logging.error(f"Ошибка при сканировании {url}: {e}")
                break
            result[category] = page
        logging.info(f"{category}: {result.get(category, 1)} стр.")

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
        return [p.get_text("\n", strip=True) for b in soup.find_all("div", class_="anekdot-text") if (p := b.find("p"))]
    except Exception as e:
        logging.error(f"Ошибка парсинга {category}: {e}")
        return []


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
        time.sleep(30)
    return False


def main():
    if not WEBHOOK_URL:
        logging.critical("WEBHOOK_URL не установлен!")
        return

    sent = load_sent()
    data = load_category_map()
    category_map = data.get("categories", {})
    next_scan = datetime.fromisoformat(data.get("next_scan", "1970-01-01T00:00:00"))

    logging.info("Бот запущен")

    try:
        while True:
            current_time = datetime.now()
            if current_time >= next_scan:
                logging.info("Обновление карты категорий")
                data = scan_categories()
                category_map = data["categories"]
                next_scan = datetime.fromisoformat(data["next_scan"])

            category = random.choice(CATEGORIES)
            anekdots = get_anekdots_from_category(category, category_map.get(category, 10))
            new = [a for a in anekdots if (category, a) not in sent]

            if new:
                a = random.choice(new)
                if send_to_discord(a):
                    sent.add((category, a))
                    save_sent(sent)
            else:
                logging.info("Нет новых анекдотов.")

            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную (Ctrl + C)")
        exit(0)


if __name__ == "__main__":
    main()