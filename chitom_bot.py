import os
import io
import re
import time
import base64
import random
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests  # Required for free fallback API

import telebot
import google.generativeai as genai
# Note: Google SDK structure can change. Assuming current best practices.
from google.generativeai.types import content_types
from collections.abc import Iterable
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ==========================================================
# CONFIG
# ==========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BOT_USERNAME = "@chaitom_bot"

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN")
if not GEMINI_API_KEY:
    raise RuntimeError("Не задан GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ==========================================================
# PERSISTENT HISTORY (ПАМЯТЬ БОТА)
# ==========================================================

HISTORY_FILE = "chat_history.json"
HISTORY_LIMIT = 1000

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

# ==========================================================
# TEXT & VISION AI (FREE MODELS)
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

# Configuration for safety settings (blocking none, as in original)
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# Standard chat model
text_model = genai.GenerativeModel(
    "gemini-1.5-flash",  # Switched to Flash for faster free tier response
    system_instruction=SYSTEM_PROMPT,
    safety_settings=safety_settings
)

# Vision model for image editing fallback
vision_model = genai.GenerativeModel(
    "gemini-1.5-flash",
    safety_settings=safety_settings
)

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
    # Standard Gemini response parsing
    for part in (getattr(response, "parts", None) or []):
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

# ==========================================================
# DRAW — NANO BANANA 2 (FREE FALLBACK)
# ==========================================================

def draw_generate_pollinations(prompt):
    """Truly free Text-to-Image via Polling Nations GET API"""
    try:
        # Encode prompt and add seed for variability
        encoded_prompt = requests.utils.quote(prompt)
        seed = random.randint(0, 999999)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}"
        
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Verify it's actually an image
        if 'image' not in response.headers.get('Content-Type', ''):
            raise RuntimeError("Nano Banana returned non-image data.")
            
        return response.content
    except Exception as e:
        raise RuntimeError(f"Сбой Nano Banana: {e}")

@bot.message_handler(
    func=lambda m: is_command(m, ["draw", "gen"]),
    content_types=["text", "photo"]
)
def draw_command(message):
    raw = message.caption if message.photo else message.text

    prompt = re.sub(
        r"^/(draw|gen)(@\w+)?\s*",
        "",
        raw or "",
        flags=re.IGNORECASE
    ).strip()

    if not prompt:
        bot.reply_to(message, "Напиши, что нарисовать.")
        return

    # User message
    status = bot.reply_to(message, "🍌 Nano Banana 2 рисует...")

    def task():
        try:
            # We must translate the user's prompt to English for the free API
            translation_prompt = f"Translate the following Russian text to a detailed English image generation prompt: '{prompt}'. Reply ONLY with the English translation."
            translation_response = text_model.generate_content([translation_prompt])
            english_prompt = translation_response.text.strip()
            
            # Generate via truly free API
            image_data = draw_generate_pollinations(english_prompt)

            # Cleanup status
            try:
                bot.delete_message(message.chat.id, status.message_id)
            except Exception:
                pass

            file = io.BytesIO(image_data)
            file.name = "generated.png"

            bot.send_photo(
                message.chat.id,
                file,
                reply_to_message_id=message.message_id
            )

        except Exception as e:
            print("DRAW ERROR:", repr(e))
            try:
                bot.edit_message_text(
                    f"❌ Ошибка Nano Banana:\n{str(e)[:500]}",
                    message.chat.id,
                    status.message_id
                )
            except Exception:
                pass

    threading.Thread(target=task, daemon=True).start()

# ==========================================================
# EDIT IMAGE (FREE VISION + REGENERATION)
# ==========================================================

def edit_image_free_workflow(image_bytes, user_prompt):
    """Free 'editing' workflow: Analyze image -> enhanced prompt -> new image"""
    
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    
    # Step 1: Use Free Vision Flash to describe the original image in extreme detail
    vision_analysis_prompt = (
        "Analyze this image in extreme detail. Provide a comprehensive, highly detailed English description of every element, "
        "including the subject, setting, lighting, composition, colors, and style."
    )
    analysis_response = vision_model.generate_content([vision_analysis_prompt, img])
    original_description = analysis_response.text.strip()

    # Step 2: Use Text Flash to create a new prompt incorporating the edit
    prompt_engineering_instructions = (
        f"Based on the following detailed description of an original image:\n\n{original_description}\n\n"
        f"Create a NEW, extremely detailed English image generation prompt that depicts the exact same scene, "
        f"but with the following transformation applied: '{user_prompt}'. "
        "Maintain the original composition, style, and identity as much as possible, focusing only on the requested change."
    )
    prompt_response = text_model.generate_content([prompt_engineering_instructions])
    final_english_prompt = prompt_response.text.strip()

    # Step 3: Generate the new image using the truly free API
    print("EDIT: Generating new image via free prompt...")
    modified_image_data = draw_generate_pollinations(final_english_prompt)

    out = io.BytesIO(modified_image_data)
    out.name = "edited.png"
    out.seek(0)
    return out

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
    prompt = re.sub(
        r"^/edit(@\w+)?\s*", "", raw or "", flags=re.IGNORECASE
    ).strip()

    if not prompt:
        bot.reply_to(message, "Напиши, что изменить на фото.")
        return

    status = bot.reply_to(message, "🎨 Анализирую образ...")

    def task():
        try:
            info = bot.get_file(target.photo[-1].file_id)
            image_bytes = bot.download_file(info.file_path)

            # Free workflow
            result = edit_image_free_workflow(image_bytes, prompt)

            try:
                bot.delete_message(message.chat.id, status.message_id)
            except Exception:
                pass

            result.seek(0)
            bot.send_photo(
                message.chat.id,
                result,
                reply_to_message_id=message.message_id
            )

        except Exception as e:
            print("EDIT ERROR:", repr(e))
            try:
                safe_err = str(e)[:500]
                bot.edit_message_text(
                    f"❌ Сбой Nano Banana (Vision):\n{safe_err}",
                    message.chat.id,
                    status.message_id
                )
            except Exception:
                pass

    threading.Thread(target=task, daemon=True).start()

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
        response = text_model.generate_content([music_prompt])
        reply = response.text.replace("*", "").strip()
        bot.edit_message_text(reply, message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"Плеер зажевал кассету: {e}", message.chat.id, status_msg.message_id)

# ==========================================================
# MEMES (PIL-BASED)
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
    from PIL import ImageDraw # Local import to reduce startup
    # Mimicking original logic for robustness
    x, y = xy
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            draw.text((x + dx, y + dy), text, font=font, fill="black")
    draw.text(xy, text, font=font, fill="white")

def generate_meme(top, middle, bottom):
    from PIL import ImageDraw, ImageFont # Local import
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
# START
# ==========================================================

@bot.message_handler(commands=["start"])
def start_command(message):
    bot.reply_to(
        message,
        "/draw — картинка\n"
        "/edit — редактирование фото\n"
        "/make_meme — мем\n"
        "/music — сгенерировать скроббл\n"
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
            ["draw", "gen"], ["edit"],
            ["make_meme"], ["history", "save_history"],
            ["import_history"], ["music"]
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
        history = "\n".join(dialog_context[chat_id])
        prompt = (
            f"Последние сообщения:\n{history}\n\n"
            f"Ответь на последнее сообщение {user_name}."
        )
        contents = [prompt]

        if message.photo:
            info = bot.get_file(message.photo[-1].file_id)
            data = bot.download_file(info.file_path)
            # Use PIL directly here for robustness
            img_io = io.BytesIO(data)
            contents.append(Image.open(img_io).convert("RGB"))

        elif message.voice or message.audio:
            media = message.voice if message.voice else message.audio
            info = bot.get_file(media.file_id)
            data = bot.download_file(info.file_path)
            contents.append({
                "mime_type": "audio/ogg" if message.voice else "audio/mpeg",
                "data": data
            })

        response = text_model.generate_content(contents)
        reply = response.text.replace("*", "")
        bot.reply_to(message, reply)
        dialog_context[chat_id].append(f"читом бот: {reply}")

    except Exception as e:
        print("CHAT ERROR:", repr(e))
        bot.reply_to(message, f"Мой клубок запутался. Ошибка: {e}")

# ==========================================================
# HEALTH SERVER
# ==========================================================

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
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
    print("Читом бот запущен")
    print(f"Загружено {len(chat_history)} фраз в память")
    bot.infinity_polling()
