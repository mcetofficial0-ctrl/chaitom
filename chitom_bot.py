import os
import io
import re
import time
import base64
import random
import json
import threading
import unicodedata
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

import telebot
import google.generativeai as genai
from google import genai as new_genai
from google.genai import types
from openai import OpenAI
from free_chat import GroqChat, ChatUnavailable
from PIL import Image, ImageDraw, ImageFont, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ==========================================================
# CONFIG
# ==========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_CHAT_MODEL = os.environ.get("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")
HF_TOKEN = os.environ.get("HF_TOKEN")  # Бесплатный токен от Hugging Face
HF_IMAGE_API = os.environ.get(
    "HF_IMAGE_API", "https://router.huggingface.co/hf-inference/models"
).rstrip("/")
BOT_USERNAME = "@chaitom_bot"

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN")
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    image_client = new_genai.Client(api_key=GEMINI_API_KEY)
else:
    image_client = None
chat_client = (
    OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1",
           timeout=30.0, max_retries=0)
    if GROQ_API_KEY else None
)

# ==========================================================
# PERSISTENT HISTORY (ПАМЯТЬ БОТА)
# ==========================================================

HISTORY_FILE = "chat_history.json"
HISTORY_LIMIT = 1000
RYM_ALBUMS_FILE = os.path.join(os.path.dirname(__file__), "rym_albums.json")
RYM_FILMS_FILE = os.path.join(os.path.dirname(__file__), "rym_films.json")
RYM_FILM_COVER_CACHE_FILE = os.path.join(
    os.path.dirname(__file__), "rym_film_covers.json"
)
RYM_FILM_CHART_URL = (
    "https://rateyourmusic.com/charts/esoteric/film/all-time/"
    "separate:live,archival,soundtrack/"
)
RYM_CHART_URL = (
    "https://rateyourmusic.com/charts/esoteric/album/all-time/"
    "g:%2dclassical%2dmusic/separate:live,archival,soundtrack/"
)

def load_chat_history():
    """Загружает историю из файла при старте скрипта"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    print(f"Загружено {len(data)} сообщений из памяти.")
                    return data
        except Exception as e:
            print(f"Ошибка чтения {HISTORY_FILE}:", e)
    return []

def save_chat_history():
    """Сохраняет текущую историю на диск"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(chat_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения {HISTORY_FILE}:", e)

# Инициализируем историю из файла при запуске!
chat_history = load_chat_history()
dialog_context = {}
CONTEXT_LIMIT = 15

def load_rym_albums():
    """Загружает локальный снимок чарта RYM без запросов к защищённой странице."""
    try:
        with open(RYM_ALBUMS_FILE, "r", encoding="utf-8") as f:
            albums = json.load(f)
        valid_albums = [
            album for album in albums
            if isinstance(album, dict)
            and album.get("title")
            and album.get("url", "").startswith("https://rateyourmusic.com/release/")
        ]
        print(f"Загружено {len(valid_albums)} альбомов из чарта RYM.")
        return valid_albums
    except Exception as e:
        print("Ошибка загрузки каталога RYM:", e)
        return []

rym_albums = load_rym_albums()

def load_rym_films():
    """Загружает локальный снимок film-чарта RYM. НИКАКИХ запросов к RYM."""
    try:
        with open(RYM_FILMS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        films = raw.get("films", []) if isinstance(raw, dict) else raw
        if not isinstance(films, list):
            raise ValueError("неожиданный формат rym_films.json")

        valid = []
        seen_ranks = set()

        for item in films:
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()

            try:
                rank = int(item.get("rank"))
            except (TypeError, ValueError):
                continue

            if (
                not title
                or rank < 1
                or rank in seen_ranks
                or not url.startswith("https://rateyourmusic.com/")
            ):
                continue

            clean = {
                "rank": rank,
                "title": title,
                "url": url,
            }

            year = str(item.get("year", "")).strip()
            cover = str(item.get("cover", "")).strip()

            if year:
                clean["year"] = year
            if cover.startswith("https://"):
                clean["cover"] = cover

            valid.append(clean)
            seen_ranks.add(rank)

        valid.sort(key=lambda x: x["rank"])
        print(
            f"Загружено {len(valid)} фильмов из локального снимка RYM.",
            flush=True,
        )
        return valid

    except Exception as e:
        print("Ошибка загрузки локального rym_films.json:", e, flush=True)
        return []

rym_films = load_rym_films()

# ==========================================================
# FILM POSTERS — DIRECTLY FROM RATEYOURMUSIC + LOCAL CACHE
# ==========================================================

film_cover_cache_lock = threading.Lock()


def load_film_cover_cache():
    """
    Кеш постеров именно с RYM/Sonemic CDN.
    """
    try:
        with open(RYM_FILM_COVER_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return {
                str(key): str(value)
                for key, value in data.items()
                if isinstance(value, str)
                and (
                    value.startswith("https://e.snmc.io/")
                    or value.startswith("https://i.snmc.io/")
                )
            }

    except FileNotFoundError:
        pass
    except Exception as e:
        print("RYM FILM COVER CACHE LOAD ERROR:", repr(e), flush=True)

    return {}


film_cover_cache = load_film_cover_cache()


def save_film_cover_cache():
    try:
        tmp_path = RYM_FILM_COVER_CACHE_FILE + ".tmp"

        with film_cover_cache_lock:
            payload = dict(film_cover_cache)

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

        os.replace(tmp_path, RYM_FILM_COVER_CACHE_FILE)

    except Exception as e:
        print("RYM FILM COVER CACHE SAVE ERROR:", repr(e), flush=True)


def film_cover_cache_key(film):
    return f"{film.get('rank', '')}|{film.get('title', '').strip()}"


def rym_slug(text):
    """
    Строит вероятный slug индивидуальной film-страницы RYM.
    Сам чарт при этом не запрашивается.
    """
    text = str(text or "").strip()

    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    base = ascii_text if ascii_text.strip() else text

    base = base.casefold()
    base = re.sub(r"[’'`]+", "", base)
    base = re.sub(r"[^a-z0-9а-яё]+", "-", base, flags=re.IGNORECASE)
    return base.strip("-")


def extract_rym_cover_from_html(html):
    """
    Достаёт только ссылку Sonemic CDN из HTML страницы RYM.
    """
    if not html:
        return None

    candidates = []

    meta_patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]

    for pattern in meta_patterns:
        for value in re.findall(pattern, html, flags=re.IGNORECASE):
            value = (
                value.replace("&amp;", "&")
                .replace("\\/", "/")
                .strip()
            )
            if "snmc.io/" in value:
                candidates.append(value)

    for value in re.findall(
        r'https://(?:e|i)\.snmc\.io/i/[^"\'<>\\\s]+',
        html,
        flags=re.IGNORECASE,
    ):
        candidates.append(value.replace("\\/", "/").strip())

    if not candidates:
        return None

    preferred = [
        url for url in candidates
        if "/i/" in url and "/300/" in url
    ]

    return (preferred or candidates)[0]


def fetch_rym_html(url):
    """
    Читает только индивидуальную film/search страницу RYM.
    Эзотерический chart по сети не читается.
    """
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=(5, 12),
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type:
        raise RuntimeError(
            f"RYM page returned non-HTML content: {content_type}"
        )

    return response.text


def discover_rym_film_cover(film):
    """
    Получает постер именно с RateYourMusic/Sonemic.

    Порядок:
    1) cover уже есть в локальном rym_films.json;
    2) cover уже есть в локальном кеше;
    3) открывается индивидуальная RYM film page;
    4) при необходимости — сохранённая RYM search URL.

    Чарт RYM по сети НЕ открывается.
    """
    embedded = str(film.get("cover", "")).strip()

    if (
        embedded.startswith("https://e.snmc.io/")
        or embedded.startswith("https://i.snmc.io/")
    ):
        return embedded

    cache_key = film_cover_cache_key(film)

    with film_cover_cache_lock:
        cached = film_cover_cache.get(cache_key)

    if cached:
        return cached

    title = str(film.get("title", "")).strip()
    if not title:
        return None

    candidate_pages = []

    slug = rym_slug(title)
    if slug:
        candidate_pages.append(
            f"https://rateyourmusic.com/film/{slug}/"
        )

    saved_url = str(film.get("url", "")).strip()
    if saved_url.startswith("https://rateyourmusic.com/"):
        candidate_pages.append(saved_url)

    candidate_pages = list(dict.fromkeys(candidate_pages))

    for page_url in candidate_pages:
        try:
            print("RYM FILM POSTER PAGE:", page_url, flush=True)

            html = fetch_rym_html(page_url)
            cover_url = extract_rym_cover_from_html(html)

            if not cover_url:
                continue

            if not (
                cover_url.startswith("https://e.snmc.io/")
                or cover_url.startswith("https://i.snmc.io/")
            ):
                continue

            with film_cover_cache_lock:
                film_cover_cache[cache_key] = cover_url

            save_film_cover_cache()

            print("RYM FILM POSTER FOUND:", cover_url, flush=True)
            return cover_url

        except Exception as e:
            print(
                "RYM FILM POSTER LOOKUP ERROR:",
                type(e).__name__,
                str(e)[:300],
                flush=True,
            )

    return None


# ==========================================================
# AI MODELS
# ==========================================================

SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот".
Твой характер: ироничный, абсурдный и саркастичный шутник.

Отвечай очень коротко: 1-3 предложения.
Никаких длинных монологов.

Иногда используй слова: "читом", "клубок", "бастурма".

Твои знакомые:
- Степан Клитор — депрессивный музыкант.
- Андрей Визард — фанат бургеров.
- Роман Линкин — фанат My Little Pony.

Не используй звездочки и markdown."""

conversation = GroqChat(chat_client, SYSTEM_PROMPT, model=GROQ_CHAT_MODEL)

model = genai.GenerativeModel(
    "gemini-3.6-flash",  # Возвращаем нашу стабильную рабочую версию!
    system_instruction=SYSTEM_PROMPT,
    safety_settings=[
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ],
) if GEMINI_API_KEY else None

# ==========================================================
# HELPERS
# ==========================================================

def temp_error(e):
    s = str(e).upper()
    return any(x in s for x in (
        "429", "503", "RESOURCE_EXHAUSTED",
        "UNAVAILABLE", "HIGH DEMAND",
        "OVERLOADED", "TIMEOUT", "DEADLINE"
    ))

def extract_image_bytes(response):
    for part in (getattr(response, "parts", None) or []):
        data = getattr(getattr(part, "inline_data", None), "data", None)
        if data:
            return base64.b64decode(data) if isinstance(data, str) else data

    for candidate in (getattr(response, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                return base64.b64decode(data) if isinstance(data, str) else data

    return None

def is_command(message, names):
    text = (message.text or message.caption or "").strip()
    return bool(re.match(
        rf"^/({'|'.join(names)})(@\w+)?(?:\s|$)",
        text,
        re.IGNORECASE
    ))

def download_rym_cover(url):
    """Скачивает обложку RYM CDN для отправки в Telegram."""
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ChaitomBot/1.0)"},
        timeout=20,
    )
    response.raise_for_status()
    if "image" not in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("RYM вернул не изображение")
    if len(response.content) > 10 * 1024 * 1024:
        raise RuntimeError("Обложка RYM слишком большая")
    return response.content

# ==========================================================
# RANDOM RATE YOUR MUSIC ALBUM
# ==========================================================

@bot.message_handler(commands=["rym"])
def rym_command(message):
    if not rym_albums:
        bot.reply_to(message, f"Каталог RYM пока не загрузился. Сам чарт: {RYM_CHART_URL}")
        return

    candidates = random.sample(rym_albums, min(6, len(rym_albums)))
    selected = candidates[0]
    last_error = None

    for album in candidates:
        if not album.get("cover"):
            continue

        caption = (
            f"🎲 RYM #{album.get('rank', '?')}\n"
            f"{album.get('artist', 'Unknown Artist')} — {album['title']}\n\n"
            f"{album['url']}"
        )

        try:
            cover = io.BytesIO(download_rym_cover(album["cover"]))
            cover.name = "rym_cover.jpg"
            bot.send_photo(
                message.chat.id,
                cover,
                caption=caption,
                reply_to_message_id=message.message_id,
            )
            return
        except Exception as e:
            last_error = e
            print("RYM COVER ERROR:", repr(e))

            try:
                bot.send_photo(
                    message.chat.id,
                    album["cover"],
                    caption=caption,
                    reply_to_message_id=message.message_id,
                )
                return
            except Exception as telegram_error:
                last_error = telegram_error
                print("RYM TELEGRAM COVER ERROR:", repr(telegram_error))

    fallback = (
        f"🎲 RYM #{selected.get('rank', '?')}\n"
        f"{selected.get('artist', 'Unknown Artist')} — {selected['title']}\n\n"
        f"{selected['url']}"
    )
    if last_error:
        print("RYM FINAL ERROR:", repr(last_error))
    bot.reply_to(message, fallback)

@bot.message_handler(commands=["film"])
def film_command(message):
    """
    Как /rym:
    - фильм выбирается из локального rym_films.json;
    - постер берётся только с RYM/Sonemic CDN;
    - найденный CDN URL кешируется локально.
    """
    if not rym_films:
        bot.reply_to(
            message,
            "Локальный каталог фильмов RYM не загрузился."
        )
        return

    candidates = random.sample(
        rym_films,
        min(6, len(rym_films))
    )

    selected = candidates[0]
    last_error = None

    status = bot.reply_to(
        message,
        "🎬 Выбираю фильм и беру постер с RYM..."
    )

    def caption_for(film):
        year = f" ({film['year']})" if film.get("year") else ""

        return (
            f"🎬 RYM film #{film['rank']}\n"
            f"{film['title']}{year}\n\n"
            f"{film['url']}"
        )

    def task():
        nonlocal last_error

        for film in candidates:
            try:
                cover_url = discover_rym_film_cover(film)

                if not cover_url:
                    continue

                caption = caption_for(film)

                # Полностью как /rym: сначала скачиваем CDN сами.
                try:
                    cover = io.BytesIO(
                        download_rym_cover(cover_url)
                    )
                    cover.name = "rym_film_cover.jpg"

                    bot.send_photo(
                        message.chat.id,
                        cover,
                        caption=caption[:1024],
                        reply_to_message_id=message.message_id,
                    )

                    try:
                        bot.delete_message(
                            message.chat.id,
                            status.message_id,
                        )
                    except Exception:
                        pass

                    print(
                        f"FILM OK: #{film['rank']} "
                        f"{film['title']} + RYM cover",
                        flush=True,
                    )
                    return

                except Exception as download_error:
                    last_error = download_error
                    print(
                        "RYM FILM COVER DOWNLOAD ERROR:",
                        repr(download_error),
                        flush=True,
                    )

                    # И тот же запасной путь: Telegram качает CDN URL.
                    try:
                        bot.send_photo(
                            message.chat.id,
                            cover_url,
                            caption=caption[:1024],
                            reply_to_message_id=message.message_id,
                        )

                        try:
                            bot.delete_message(
                                message.chat.id,
                                status.message_id,
                            )
                        except Exception:
                            pass

                        return

                    except Exception as telegram_error:
                        last_error = telegram_error

            except Exception as e:
                last_error = e
                print("RYM FILM ERROR:", repr(e), flush=True)

        try:
            bot.delete_message(
                message.chat.id,
                status.message_id,
            )
        except Exception:
            pass

        if last_error:
            print(
                "RYM FILM FINAL COVER ERROR:",
                repr(last_error),
                flush=True,
            )

        bot.reply_to(
            message,
            caption_for(selected)
        )

    threading.Thread(
        target=task,
        daemon=True,
    ).start()



# ==========================================================
# RANDOM WIKIPEDIA IMAGE — RATE LIMIT FIX
# ==========================================================

WIKIPEDIA_API_URL = "https://ru.wikipedia.org/w/api.php"
WIKIMEDIA_COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"

WIKI_HEADERS = {
    "User-Agent": (
        "ChaitomBot/1.0 "
        "(Telegram bot; random Wikipedia image; "
        "https://t.me/chaitom_bot)"
    ),
    "Accept": "application/json",
}


def wiki_request(url, params, attempts=3):
    """
    Один аккуратный MediaWiki-запрос с обработкой 429.
    В старой версии бот мог сделать до 12 быстрых запросов подряд,
    из-за чего Wikimedia начинала отвечать Too Many Requests.
    """
    last_error = None

    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                params=params,
                headers=WIKI_HEADERS,
                timeout=(5, 15),
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")

                try:
                    delay = int(retry_after)
                except (TypeError, ValueError):
                    delay = 3 + attempt * 3

                delay = max(2, min(delay, 15))

                print(
                    f"WIKI 429: retry after {delay}s",
                    flush=True,
                )

                if attempt < attempts - 1:
                    time.sleep(delay)
                    continue

            response.raise_for_status()
            return response.json()

        except Exception as e:
            last_error = e

            print(
                f"WIKI REQUEST ERROR {attempt + 1}/{attempts}:",
                repr(e),
                flush=True,
            )

            if attempt < attempts - 1:
                time.sleep(2 + attempt * 2)

    raise RuntimeError(
        f"Wikimedia API недоступен: {last_error}"
    )


def get_random_wikipedia_page_image():
    """
    Вместо 12 отдельных random-запросов получаем до 10 случайных
    статей ЗА ОДИН API-запрос и выбираем первую подходящую картинку.
    """
    data = wiki_request(
        WIKIPEDIA_API_URL,
        {
            "action": "query",
            "generator": "random",
            "grnnamespace": 0,
            "grnlimit": 10,
            "prop": "pageimages|info",
            "piprop": "original|thumbnail",
            "pithumbsize": 1280,
            "inprop": "url",
            "redirects": 1,
            "format": "json",
            "formatversion": 2,
            "origin": "*",
        },
        attempts=2,
    )

    pages = data.get("query", {}).get("pages", [])

    random.shuffle(pages)

    for page in pages:
        title = str(page.get("title", "")).strip()
        article_url = str(page.get("fullurl", "")).strip()

        image_url = (
            page.get("original", {}).get("source")
            or page.get("thumbnail", {}).get("source")
        )

        if not isinstance(image_url, str):
            continue

        lower_url = image_url.lower()

        if not image_url.startswith("https://"):
            continue

        # Telegram не умеет SVG как обычное фото.
        if lower_url.endswith(".svg") or ".svg?" in lower_url:
            continue

        return {
            "title": title or "Wikipedia",
            "article_url": article_url,
            "image_url": image_url,
            "source": "Wikipedia",
        }

    return None


def get_random_commons_image():
    """
    Резерв: случайный файл из Wikimedia Commons.
    Большинство изображений Википедии физически хранятся именно там.

    Это тоже делается одним API-запросом — без спама запросами.
    """
    data = wiki_request(
        WIKIMEDIA_COMMONS_API_URL,
        {
            "action": "query",
            "generator": "random",
            "grnnamespace": 6,
            "grnlimit": 10,
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "iiurlwidth": 1280,
            "format": "json",
            "formatversion": 2,
            "origin": "*",
        },
        attempts=3,
    )

    pages = data.get("query", {}).get("pages", [])
    random.shuffle(pages)

    for page in pages:
        title = str(page.get("title", "")).strip()

        info_list = page.get("imageinfo") or []
        if not info_list:
            continue

        info = info_list[0]

        mime = str(info.get("mime", "")).lower()
        image_url = (
            info.get("thumburl")
            or info.get("url")
        )

        if not isinstance(image_url, str):
            continue

        if not image_url.startswith("https://"):
            continue

        if not mime.startswith("image/"):
            continue

        # SVG/GIF/TIFF часто плохо идут через Telegram send_photo.
        if mime in {
            "image/svg+xml",
            "image/gif",
            "image/tiff",
        }:
            continue

        page_url = (
            "https://commons.wikimedia.org/wiki/"
            + requests.utils.quote(title.replace(" ", "_"))
        )

        return {
            "title": title.removeprefix("File:"),
            "article_url": page_url,
            "image_url": image_url,
            "source": "Wikimedia Commons",
        }

    return None


def download_wiki_image(image_url):
    """
    Скачиваем выбранную картинку только ОДИН раз.
    """
    response = requests.get(
        image_url,
        headers={
            "User-Agent": WIKI_HEADERS["User-Agent"],
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        },
        timeout=(5, 20),
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        ""
    ).lower()

    if "image" not in content_type:
        raise RuntimeError(
            f"Источник вернул не изображение: {content_type}"
        )

    image_bytes = response.content

    if not image_bytes:
        raise RuntimeError("Получена пустая картинка.")

    if len(image_bytes) > 10 * 1024 * 1024:
        raise RuntimeError("Картинка больше 10 МБ.")

    return image_bytes


def get_random_wikipedia_image():
    """
    Схема без бесконечного спама API:
      1. один batch-запрос к русской Wikipedia;
      2. при неудаче — один batch-запрос к Wikimedia Commons;
      3. скачивание только одной выбранной картинки.
    """
    last_error = None

    try:
        item = get_random_wikipedia_page_image()
        if item:
            item["image_bytes"] = download_wiki_image(
                item["image_url"]
            )
            return item

    except Exception as e:
        last_error = e
        print(
            "RAND_WIKI WIKIPEDIA ERROR:",
            repr(e),
            flush=True,
        )

    try:
        item = get_random_commons_image()
        if item:
            item["image_bytes"] = download_wiki_image(
                item["image_url"]
            )
            return item

    except Exception as e:
        last_error = e
        print(
            "RAND_WIKI COMMONS ERROR:",
            repr(e),
            flush=True,
        )

    raise RuntimeError(
        "Не удалось получить случайную картинку. "
        f"Последняя ошибка: {last_error}"
    )


@bot.message_handler(commands=["rand_wiki"])
def rand_wiki_command(message):
    status = bot.reply_to(
        message,
        "🌐 Ищу случайную картинку..."
    )

    def task():
        try:
            item = get_random_wikipedia_image()

            caption = (
                f"🌐 {item['title']}\n"
                f"Источник: {item['source']}\n"
                f"{item['article_url']}"
            ).strip()

            photo = io.BytesIO(item["image_bytes"])
            photo.name = "wikipedia_random.jpg"

            bot.send_photo(
                message.chat.id,
                photo,
                caption=caption[:1024],
                reply_to_message_id=message.message_id,
            )

            try:
                bot.delete_message(
                    message.chat.id,
                    status.message_id,
                )
            except Exception:
                pass

            print(
                "RAND_WIKI OK:",
                item["source"],
                item["title"],
                flush=True,
            )

        except Exception as e:
            print(
                "RAND_WIKI ERROR:",
                repr(e),
                flush=True,
            )

            try:
                bot.edit_message_text(
                    f"❌ Не удалось получить картинку:\n"
                    f"{str(e)[:600]}",
                    message.chat.id,
                    status.message_id,
                )
            except Exception:
                pass

    threading.Thread(
        target=task,
        daemon=True,
    ).start()


# ==========================================================
# DRAW — FREE API (HUGGING FACE / FALLBACK)
# ==========================================================

def draw_generate_pollinations(prompt):
    """Резервное бесплатное рисование (Nano Banana 2)"""
    try:
        encoded_prompt = requests.utils.quote(prompt)
        seed = random.randint(0, 999999)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}"
        
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        if 'image' not in response.headers.get('Content-Type', ''):
            raise RuntimeError("Nano Banana GET API returned non-image data.")
        return response.content
    except Exception as e:
        raise RuntimeError(f"Сбой Nano Banana fallback: {e}")

def draw_generate_hf(prompt):
    """Генерация картинки через Hugging Face (FLUX.1-schnell)"""
    if not HF_TOKEN:
        raise RuntimeError("Не задан HF_TOKEN.")

    payload = {"inputs": prompt}
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    response = requests.post(
        f"{HF_IMAGE_API}/black-forest-labs/FLUX.1-schnell",
        headers=headers,
        json=payload,
        timeout=60
    )
    if response.status_code == 503:
        raise RuntimeError("Hugging Face загружает модель, попробуй через минуту.")
    response.raise_for_status()
    if 'image' not in response.headers.get('Content-Type', ''):
         raise RuntimeError("HF draw API returned non-image data.")
    return response.content

@bot.message_handler(
    func=lambda m: is_command(m, ["draw", "gen"]),
    content_types=["text", "photo"]
)
def draw_command(message):
    raw = message.caption if message.photo else message.text
    prompt = re.sub(r"^/(draw|gen)(@\w+)?\s*", "", raw or "", flags=re.IGNORECASE).strip()

    if not prompt:
        bot.reply_to(message, "Напиши, что нарисовать.")
        return

    status = bot.reply_to(message, "🎨 Рисую через бесплатный API...")

    def task():
        image_data = None
        last_error = None

        english_prompt = prompt

        try:
            image_data = draw_generate_hf(english_prompt)
            print("DRAW OK: Hugging Face (FLUX)")
        except Exception as e:
            print("DRAW HF ERROR:", repr(e))
            print("DRAW: Запускаю резервный план (Polling Nations)...")
            try:
                image_data = draw_generate_pollinations(english_prompt)
                print("DRAW OK: Nano Banana Fallback")
            except Exception as backup_e:
                 last_error = backup_e
                 print("DRAW FINAL FALLBACK ERROR:", repr(backup_e))

        if image_data:
            try:
                bot.delete_message(message.chat.id, status.message_id)
            except Exception:
                pass
            file = io.BytesIO(image_data)
            file.name = "generated.png"
            bot.send_photo(message.chat.id, file, reply_to_message_id=message.message_id)
        else:
             try:
                 bot.edit_message_text(f"❌ Оба мольберта сломались:\n{str(last_error)[:500]}", message.chat.id, status.message_id)
             except Exception: pass

    threading.Thread(target=task, daemon=True).start()

# ==========================================================
# EDIT IMAGE — FREE INSTRUCTPIX2PIX (HUGGING FACE) + FALLBACK
# ==========================================================

def edit_image_hf(image_bytes, user_prompt):
    """Редактирование картинки через Hugging Face (InstructPix2Pix)"""
    if not HF_TOKEN:
        raise RuntimeError("Не задан HF_TOKEN.")

    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "inputs": user_prompt,
        "image": base64_image,
        "num_inference_steps": 25,
        "image_guidance_scale": 1.5,
        "guidance_scale": 7.5
    }
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    response = requests.post(
        f"{HF_IMAGE_API}/timbrooks/instruct-pix2pix",
        headers=headers,
        json=payload,
        timeout=90
    )
    if response.status_code == 503:
        raise RuntimeError("Hugging Face загружает модель, попробуй через минуту.")
    response.raise_for_status()
    if 'image' not in response.headers.get('Content-Type', ''):
         raise RuntimeError("HF edit API returned non-image data.")
    return response.content

@bot.message_handler(
    func=lambda m: is_command(m, ["edit"]),
    content_types=["text", "photo"]
)
def edit_command(message):
    target = message if message.photo else message.reply_to_message

    if not target or not target.photo:
        bot.reply_to(
            message,
            "Прикрепи фото к /edit или сделай reply на фото.\n\n"
            "Пример:\n"
            "/edit добавь человеку очки"
        )
        return

    raw = message.caption if message.photo else message.text
    prompt = re.sub(r"^/edit(@\w+)?\s*", "", raw or "", flags=re.IGNORECASE).strip()

    if not prompt:
        bot.reply_to(message, "Напиши, что изменить на фото.")
        return

    status = bot.reply_to(message, "🎨 Редактирую через бесплатный API...")

    def task():
        try:
            info = bot.get_file(target.photo[-1].file_id)
            image_bytes = bot.download_file(info.file_path)
            edited_image_data = None
            last_error = None

            english_prompt = prompt

            try:
                edited_image_data = edit_image_hf(image_bytes, english_prompt)
                print("EDIT OK: Hugging Face (InstructPix2Pix)")
            except Exception as e:
                print("EDIT HF I2I ERROR:", repr(e))
                print("EDIT: Запускаю бесплатный резервный план без Gemini...")
                try:
                    fallback_prompt = (
                        "Create an image matching this requested transformation as closely as possible: "
                        f"{english_prompt}. Keep the result photorealistic and high quality."
                    )
                    edited_image_data = draw_generate_pollinations(fallback_prompt)
                    print("EDIT OK: Pollinations prompt fallback")

                except Exception as e_regen:
                    last_error = e_regen
                    print("EDIT FINAL FALLBACK ERROR:", repr(e_regen))

                if not edited_image_data:
                    last_error = e

            if edited_image_data:
                try:
                    bot.delete_message(message.chat.id, status.message_id)
                except Exception:
                    pass
                file = io.BytesIO(edited_image_data)
                file.name = "edited.png"
                bot.send_photo(message.chat.id, file, reply_to_message_id=message.message_id)
            else:
                 try:
                     bot.edit_message_text(f"❌ Оба метода редактирования сломались:\n{str(last_error)[:500]}", message.chat.id, status.message_id)
                 except Exception: pass

        except Exception as e_download:
            print("EDIT DOWNLOAD ERROR:", repr(e_download))
            try:
                bot.edit_message_text(f"❌ Ошибка загрузки фото: {e_download}", message.chat.id, status.message_id)
            except Exception: pass

    threading.Thread(target=task, daemon=True).start()

# ==========================================================
# VEO 3.1 FAST (VIDEO - Платный Гугл, может выдавать 429)
# ==========================================================

VEO_MODEL = "veo-3.1-fast-generate-preview"

def get_video_image(message):
    if message.photo:
        return message
    reply = message.reply_to_message
    if reply and reply.photo:
        return reply
    return None

def download_image(message):
    info = bot.get_file(message.photo[-1].file_id)
    data = bot.download_file(info.file_path)
    return types.Image(image_bytes=data, mime_type="image/jpeg")

def generate_veo(prompt, message_id, source_image=None):
    veo_prompt = f"Create a short video: {prompt}"
    config = types.GenerateVideosConfig(
        number_of_videos=1,
        resolution="720p",
        aspect_ratio="16:9"
    )
    source = types.GenerateVideosSource(prompt=veo_prompt, image=source_image)

    operation = image_client.models.generate_videos(
        model=VEO_MODEL, source=source, config=config
    )

    while not operation.done:
        time.sleep(10)
        operation = image_client.operations.get(operation)

    videos = getattr(getattr(operation, "response", None), "generated_videos", None)
    if not videos or not videos[0].video:
        raise RuntimeError("Veo не вернул видео.")

    video = videos[0].video
    image_client.files.download(file=video)

    filename = f"veo_{message_id}_{int(time.time())}.mp4"
    video.save(filename)
    return filename

@bot.message_handler(
    func=lambda m: is_command(m, ["video", "vid"]),
    content_types=["text", "photo"]
)
def video_command(message):
    raw = message.caption if message.photo else message.text
    prompt = re.sub(r"^/(video|vid)(@\w+)?\s*", "", raw or "", flags=re.IGNORECASE).strip()
    source = get_video_image(message)

    if not prompt:
        bot.reply_to(message, "Напиши, что снять. Например: /video кот бежит по лесу")
        return
    if image_client is None:
        bot.reply_to(message, "Для видео администратору нужно настроить GEMINI_API_KEY.")
        return

    try:
        source_image = download_image(source) if source else None
    except Exception as e:
        bot.reply_to(message, f"Ошибка загрузки фото: {e}")
        return

    status = bot.reply_to(
        message,
        "🎬 Оживляю изображение..." if source_image else "🎬 Veo 3.1 Fast рендерит..."
    )

    def task():
        video_file = None
        last_error = None

        for attempt in range(1, 4):
            try:
                video_file = generate_veo(prompt, message.message_id, source_image)
                break
            except Exception as e:
                last_error = e
                if not temp_error(e) or attempt == 3:
                    break
                time.sleep(min(2 ** attempt, 15))

        if not video_file:
            try:
                bot.edit_message_text(
                    f"❌ Ошибка Veo (возможно лимит):\n{str(last_error)[:500]}",
                    message.chat.id,
                    status.message_id
                )
            except Exception:
                pass
            return

        try:
            bot.delete_message(message.chat.id, status.message_id)
        except Exception:
            pass

        try:
            with open(video_file, "rb") as video:
                bot.send_video(
                    message.chat.id,
                    video,
                    reply_to_message_id=message.message_id,
                    supports_streaming=True
                )
        finally:
            try:
                os.remove(video_file)
            except Exception:
                pass

    threading.Thread(target=task, daemon=True).start()

# ==========================================================
# MEMES
# ==========================================================

TEMPLATE_NAME = "template.jpg"
FONT_NAME = "arial.ttf"
RESULT_NAME = "meme_result.jpg"

def text_wrap(text, font, max_width):
    lines, words, i = [], text.split(), 0
    while i < len(words):
        line = ""
        while i < len(words) and font.getlength((line + " " + words[i]).strip()) <= max_width:
            line = (line + " " + words[i]).strip()
            i += 1
        if not line:
            line = words[i]
            i += 1
        lines.append(line)
    return lines

def draw_text_outline(draw, text, xy, font):
    x, y = xy
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            draw.text((x + dx, y + dy), text, font=font, fill="black")
    draw.text(xy, text, font=font, fill="white")

def generate_meme(top, middle, bottom):
    if not os.path.exists(TEMPLATE_NAME) or not os.path.exists(FONT_NAME):
        return None

    img = Image.open(TEMPLATE_NAME).convert("RGB")
    draw = ImageDraw.Draw(img)

    font_top = ImageFont.truetype(FONT_NAME, 40)
    font_middle = ImageFont.truetype(FONT_NAME, 40)
    font_bottom = ImageFont.truetype(FONT_NAME, 50)

    w, h = img.size

    y_top = 20
    for line in text_wrap(top, font_top, w * 0.9):
        tw = font_top.getlength(line)
        draw_text_outline(draw, line, ((w - tw) / 2, y_top), font_top)
        y_top += 45

    y_mid = h * 0.38
    for line in text_wrap(middle, font_middle, w * 0.9):
        tw = font_middle.getlength(line)
        draw_text_outline(draw, line, ((w - tw) / 2, y_mid), font_middle)
        y_mid += 45

    y_bot = h * 0.72
    for line in text_wrap(bottom, font_bottom, w * 0.9):
        tw = font_bottom.getlength(line)
        draw_text_outline(draw, line, ((w - tw) / 2, y_bot), font_bottom)
        y_bot += 55

    img.save(RESULT_NAME, "JPEG")
    return RESULT_NAME

@bot.message_handler(commands=["make_meme"])
def make_meme_command(message):
    if message.chat.type not in ["group", "supergroup"]:
        bot.reply_to(message, "Эта команда работает только в группе.")
        return

    if len(chat_history) < 3:
        bot.reply_to(
            message,
            f"Пока мало сообщений в памяти ({len(chat_history)}/{HISTORY_LIMIT}). Нужно хотя бы 3."
        )
        return

    status = bot.reply_to(message, "Делаю мем...")

    try:
        a, b, c = random.sample(chat_history, 3)
        result = generate_meme(a, b, c)

        if not result:
            raise RuntimeError("Не найдены template.jpg или arial.ttf.")

        with open(result, "rb") as photo:
            bot.send_photo(message.chat.id, photo, reply_to_message_id=message.message_id)

        os.remove(result)
        try:
            bot.delete_message(message.chat.id, status.message_id)
        except Exception:
            pass

    except Exception as e:
        bot.edit_message_text(f"Ошибка мема: {e}", message.chat.id, status.message_id)

# ==========================================================
# HISTORY MANAGEMENT COMMANDS
# ==========================================================

@bot.message_handler(commands=["history", "save_history"])
def history_status_command(message):
    bot.reply_to(
        message,
        f"📊 Память бота:\nСохранено фраз: {len(chat_history)}/{HISTORY_LIMIT}.\n"
        f"Все сообщения авто-сохраняются в `chat_history.json`."
    )

@bot.message_handler(commands=["import_history"], content_types=["document", "text"])
def import_history_command(message):
    target_message = message if message.document else message.reply_to_message

    if not target_message or not target_message.document:
        bot.reply_to(
            message,
            "Сделай Reply (Ответить) на отправленный файл с командой `/import_history` "
            "или прикрепи файл сразу с этой командой в поле 'Подпись'."
        )
        return

    try:
        status_msg = bot.reply_to(message, "📂 Читаю файл, ищу фразы...")
        file_info = bot.get_file(target_message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        text_content = downloaded.decode("utf-8", errors="ignore")
        file_name = target_message.document.file_name.lower()

        raw_lines = []

        if file_name.endswith(".json"):
            try:
                data = json.loads(text_content)
                if isinstance(data, dict) and "messages" in data:
                    for msg in data["messages"]:
                        text_data = msg.get("text", "")
                        if isinstance(text_data, str):
                            raw_lines.append(text_data)
                        elif isinstance(text_data, list):
                            full_text = "".join(
                                part if isinstance(part, str) else part.get("text", "")
                                for part in text_data if isinstance(part, (str, dict))
                            )
                            raw_lines.append(full_text)
                elif isinstance(data, list):
                    raw_lines = [str(item) for item in data if isinstance(item, (str, int))]
            except json.JSONDecodeError:
                bot.edit_message_text("❌ Ошибка: Невалидный JSON файл.", message.chat.id, status_msg.message_id)
                return

        elif file_name.endswith(".html"):
            matches = re.findall(r'<div class="text"[^>]*>(.*?)</div>', text_content, re.DOTALL | re.IGNORECASE)
            for match in matches:
                clean_text = re.sub(r'<[^>]+>', ' ', match).strip()
                clean_text = clean_text.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&amp;', '&')
                raw_lines.append(clean_text)

        else:
            raw_lines = text_content.splitlines()

        added_count = 0
        for line in raw_lines:
            line = line.strip()
            if line and not line.startswith("/"):
                if line not in chat_history:
                    chat_history.append(line)
                    added_count += 1
                    if len(chat_history) > HISTORY_LIMIT:
                        chat_history.pop(0)

        save_chat_history()
        bot.edit_message_text(
            f"✅ Успешно импортировано {added_count} новых фраз из файла `{target_message.document.file_name}`!\n"
            f"Всего в памяти: {len(chat_history)}/{HISTORY_LIMIT}.",
            message.chat.id, status_msg.message_id
        )

    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка при обработке файла: {e}")

# ==========================================================
# MUSIC SCROBBLE (AI-GENERATED)
# ==========================================================

@bot.message_handler(commands=["music"])
def music_command(message):
    status_msg = bot.reply_to(message, "🎧 Скробблю астральные частоты...")
    user_name = message.from_user.username or message.from_user.first_name or "Аноним"

    music_prompt = f"""
Сгенерируй фейковый музыкальный скроббл. Придумай АБСОЛЮТНО НОВОЕ, смешное, дикое и максимально абсурдное название трека, имя исполнителя и 3-5 жанровых хештегов. 
Тематика: бытовой сюрреализм, интернет-шизофрения, нелепые ситуации или забавный бред. Делай акцент на юмор и странность.

Ответь СТРОГО по этому шаблону (без markdown-звездочек, сохрани пустые строки и эмодзи):
{user_name} 🔥 [Случайное число от 1 до 100]

🔊 [Название трека] ∙ [Случайное число от 1 до 15] ♫
[Исполнитель]

#[тег1] #[тег2] #[тег3]
"""
    try:
        reply = conversation.reply(music_prompt)
        bot.edit_message_text(reply, message.chat.id, status_msg.message_id)
    except ChatUnavailable as e:
        bot.edit_message_text(str(e), message.chat.id, status_msg.message_id)
    except Exception as e:
        print("MUSIC ERROR:", type(e).__name__, flush=True)
        bot.edit_message_text("Плеер зажевал кассету. Попробуй чуть позже.", message.chat.id, status_msg.message_id)

# ==========================================================
# START
# ==========================================================

@bot.message_handler(commands=["start"])
def start_command(message):
    bot.reply_to(
        message,
        "/draw — нарисовать картинку\n"
        "/edit — отредактировать фото\n"
        "/video — видео\n"
        "/make_meme — мем\n"
        "/music — сгенерировать скроббл\n"
        "/rym — случайный альбом из эзотерического топа RYM\n"
        "/film — случайный фильм из эзотерического топа RYM\n"
        "/history — статус памяти фраз\n"
        "/import_history — загрузить текстовый файл с фразами"
    )

# ==========================================================
# GENERAL CHAT
# ==========================================================

@bot.message_handler(content_types=["text", "photo", "voice", "audio"])
def handle_message(message):
    if any(
        is_command(message, cmd)
        for cmd in [
            ["draw", "gen"], ["edit"], ["video", "vid"],
            ["make_meme"], ["history", "save_history"],
            ["import_history"], ["music"], ["rym"], ["film"]
        ]
    ):
        return

    chat_id = message.chat.id
    text = (message.text or message.caption or "").strip()
    user_name = message.from_user.first_name or "Аноним"

    if (
        message.chat.type in ["group", "supergroup"]
        and text
        and not text.startswith("/")
        and text not in chat_history
    ):
        chat_history.append(text)
        if len(chat_history) > HISTORY_LIMIT:
            chat_history.pop(0)
        save_chat_history()

    dialog_context.setdefault(chat_id, [])
    dialog_context[chat_id].append(f"{user_name}: {text or '[Медиафайл]'}")
    dialog_context[chat_id] = dialog_context[chat_id][-CONTEXT_LIMIT:]

    if message.chat.type in ["group", "supergroup"]:
        mentioned = text and BOT_USERNAME.lower() in text.lower()
        replied = False
        try:
            replied = (
                message.reply_to_message
                and message.reply_to_message.from_user.id == bot.get_me().id
            )
        except Exception:
            pass

        if not (mentioned or replied):
            return

    try:
        if message.photo:
            bot.reply_to(message, "Пока умею читать текст и слушать голосовые. Опиши фотографию словами — обсудим!")
            return

        elif message.voice or message.audio:
            media = message.voice if message.voice else message.audio
            info = bot.get_file(media.file_id)
            data = bot.download_file(info.file_path)
            file_name = "voice.ogg" if message.voice else (media.file_name or "audio.mp3")
            transcript = conversation.transcribe(data, file_name)
            dialog_context[chat_id][-1] = f"{user_name}: {transcript}"

        # Keep the recent conversation inside the free provider's token limits.
        history = "\n".join(line[-600:] for line in dialog_context[chat_id][-10:])

        prompt = (
            f"Последние сообщения:\n{history}\n\n"
            f"Ответь на последнее сообщение {user_name}."
        )

        reply = conversation.reply(prompt)
        bot.reply_to(message, reply)
        dialog_context[chat_id].append(f"читом бот: {reply}")

    except ChatUnavailable as e:
        bot.reply_to(message, str(e))
    except Exception as e:
        print("CHAT ERROR:", type(e).__name__, flush=True)
        bot.reply_to(message, "Мой клубок запутался. Попробуй написать ещё раз чуть позже.")

# ==========================================================
# HEALTH SERVER
# ==========================================================

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health/film":
            self.send_response(200 if film_health["status"] == "ok" else 503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(film_health).encode("utf-8"))
            return
        if self.path == "/health/chat":
            # Reports the last check; never triggers a paid/free API request.
            self.send_response(200 if conversation.health["status"] == "ok" else 503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(conversation.health).encode("utf-8"))
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Chaitom bot is running")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass

def run_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    print("Читом бот запущен; разговоры через Groq", flush=True)
    print(f"Загружено {len(chat_history)} фраз в память")
    # One synthetic request per startup verifies the deployed key and model.
    threading.Thread(target=conversation.startup_check, daemon=True).start()
    bot.infinity_polling()
