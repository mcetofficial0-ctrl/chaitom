import os
import io
import re
import time
import base64
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import telebot
import google.generativeai as genai
from google import genai as new_genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont, ImageFile

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

genai.configure(api_key=GEMINI_API_KEY)
image_client = new_genai.Client(api_key=GEMINI_API_KEY)

# ==========================================================
# TEXT AI
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

model = genai.GenerativeModel(
    "gemini-3.6-flash",
    system_instruction=SYSTEM_PROMPT,
    safety_settings=[
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ],
)

chat_history = []
HISTORY_LIMIT = 1000
dialog_context = {}
CONTEXT_LIMIT = 15

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

# ==========================================================
# DRAW — NANO BANANA 2 LITE (FAST)
# ==========================================================

DRAW_MODELS = [
    ("gemini-3.1-flash-lite-image", "Nano Banana 2 Lite", 2),
    ("gemini-3.1-flash-image", "Nano Banana 2", 1),
]


def draw_generate(model_name, prompt):
    response = image_client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio="1:1",
                image_size="1K"
            )
        )
    )

    data = extract_image_bytes(response)

    if not data:
        raise RuntimeError("Модель не вернула изображение.")

    return data


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
        bot.reply_to(
            message,
            "Напиши, что нарисовать."
        )
        return

    enh_prompt = f"""
Create the image exactly according to the user's request.
The request may be written in Russian. Understand Russian naturally.
Preserve the requested objects, characters, actions, composition,
lighting, style and text.

USER REQUEST:
{prompt}
"""

    status = bot.reply_to(
        message,
        "🍌 Рисую..."
    )

    def task():

        image_data = None
        last_error = None

        for model_name, label, attempts in DRAW_MODELS:

            for attempt in range(1, attempts + 1):

                try:

                    print(
                        f"DRAW {label} "
                        f"{attempt}/{attempts}"
                    )

                    image_data = draw_generate(
                        model_name,
                        enh_prompt
                    )

                    print(
                        f"DRAW OK: {label}"
                    )

                    break

                except Exception as e:

                    last_error = e

                    print(
                        f"DRAW ERROR {label}:",
                        repr(e)
                    )

                    if not temp_error(e):
                        break

                    if attempt < attempts:
                        time.sleep(
                            2 ** attempt
                        )

            if image_data:
                break

        if not image_data:

            try:
                bot.edit_message_text(
                    f"❌ Ошибка генерации:\n{str(last_error)[:500]}",
                    message.chat.id,
                    status.message_id
                )
            except Exception:
                pass

            return

        try:
            bot.delete_message(
                message.chat.id,
                status.message_id
            )
        except Exception:
            pass

        file = io.BytesIO(image_data)
        file.name = "generated.png"

        bot.send_photo(
            message.chat.id,
            file,
            reply_to_message_id=message.message_id
        )

    threading.Thread(
        target=task,
        daemon=True
    ).start()

# ==========================================================

# ==========================================================
# EDIT IMAGE
# ==========================================================

def edit_image(image_bytes, user_prompt):
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    prompt = f"""
Edit the provided image according to the user's request.

The request may be written in Russian.
Preserve the subject, identity, composition, perspective,
lighting and background unless explicitly asked to change them.

USER REQUEST:
{user_prompt}

Return only the edited image.
"""

    for model_name, attempts in [
        ("gemini-3.1-flash-image", 3),
        ("gemini-3.1-flash-lite-image", 2)
    ]:
        for attempt in range(attempts):
            try:
                response = image_client.models.generate_content(
                    model=model_name,
                    contents=[prompt, image],
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"]
                    )
                )

                data = extract_image_bytes(response)
                if data:
                    out = io.BytesIO(data)
                    out.name = "edited.png"
                    out.seek(0)
                    return out

                raise RuntimeError(
                    f"{model_name} не вернула изображение."
                )

            except Exception as e:
                if not temp_error(e) or attempt == attempts - 1:
                    if model_name == "gemini-3.1-flash-lite-image":
                        raise
                    break
                time.sleep(min(2 ** (attempt + 1), 10))

    raise RuntimeError("Не удалось отредактировать изображение.")

@bot.message_handler(
    func=lambda m: is_command(m, ["edit"]),
    content_types=["text", "photo"]
)
def edit_command(message):
    target = message if message.photo else message.reply_to_message

    if not target or not target.photo:
        bot.reply_to(
            message,
            "Прикрепи фото к /edit или сделай reply на фото."
        )
        return

    raw = message.caption if message.photo else message.text
    prompt = re.sub(
        r"^/edit(@\w+)?\s*",
        "",
        raw or "",
        flags=re.IGNORECASE
    ).strip()

    if not prompt:
        bot.reply_to(message, "Напиши, что изменить на фото.")
        return

    status = bot.reply_to(message, "Редактирую изображение...")

    try:
        info = bot.get_file(target.photo[-1].file_id)
        data = bot.download_file(info.file_path)

        result = edit_image(data, prompt)

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
            bot.edit_message_text(
                f"Ошибка редактирования:\n{e}",
                message.chat.id,
                status.message_id
            )
        except Exception:
            pass

# ==========================================================
# VEO 3.1 FAST
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
    return types.Image(
        image_bytes=data,
        mime_type="image/jpeg"
    )

def generate_veo(prompt, message_id, source_image=None):
    veo_prompt = f"""
Create an 8-second video according to the user's request.

The request may be written in Russian.
Understand Russian naturally.

Preserve the requested characters, objects, actions,
camera movement, atmosphere, lighting, style and sound.

USER REQUEST:
{prompt}
"""

    config = types.GenerateVideosConfig(
        number_of_videos=1,
        resolution="720p",
        aspect_ratio="16:9"
    )

    source = types.GenerateVideosSource(
        prompt=veo_prompt,
        image=source_image
    )

    operation = image_client.models.generate_videos(
        model=VEO_MODEL,
        source=source,
        config=config
    )

    while not operation.done:
        time.sleep(10)
        operation = image_client.operations.get(operation)

    videos = getattr(
        getattr(operation, "response", None),
        "generated_videos",
        None
    )

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

    prompt = re.sub(
        r"^/(video|vid)(@\w+)?\s*",
        "",
        raw or "",
        flags=re.IGNORECASE
    ).strip()

    source = get_video_image(message)

    if not prompt:
        bot.reply_to(
            message,
            "Напиши, что снять. Например: /video кот бежит по лесу"
        )
        return

    try:
        source_image = download_image(source) if source else None
    except Exception as e:
        bot.reply_to(
            message,
            f"Ошибка загрузки фото: {e}"
        )
        return

    status = bot.reply_to(
        message,
        "🎬 Оживляю изображение..."
        if source_image
        else "🎬 Veo 3.1 Fast рендерит..."
    )

    def task():
        video_file = None
        last_error = None

        for attempt in range(1, 4):
            try:
                print(f"VEO {attempt}/3")

                video_file = generate_veo(
                    prompt,
                    message.message_id,
                    source_image
                )
                break

            except Exception as e:
                last_error = e
                print("VEO ERROR:", repr(e))

                if not temp_error(e) or attempt == 3:
                    break

                time.sleep(min(2 ** attempt, 15))

        if not video_file:
            try:
                bot.edit_message_text(
                    f"❌ Ошибка Veo:\n{last_error}",
                    message.chat.id,
                    status.message_id
                )
            except Exception:
                pass
            return

        try:
            bot.delete_message(
                message.chat.id,
                status.message_id
            )
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

    threading.Thread(
        target=task,
        daemon=True
    ).start()

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
        while i < len(words) and font.getlength(
            (line + " " + words[i]).strip()
        ) <= max_width:
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
            draw.text(
                (x + dx, y + dy),
                text,
                font=font,
                fill="black"
            )

    draw.text(
        xy,
        text,
        font=font,
        fill="white"
    )

def generate_meme(top, middle, bottom):
    if not os.path.exists(TEMPLATE_NAME):
        return None

    if not os.path.exists(FONT_NAME):
        return None

    img = Image.open(TEMPLATE_NAME).convert("RGB")
    draw = ImageDraw.Draw(img)

    font_top = ImageFont.truetype(FONT_NAME, 40)
    font_middle = ImageFont.truetype(FONT_NAME, 40)
    font_bottom = ImageFont.truetype(FONT_NAME, 50)

    w, h = img.size

    # --- Отрисовка верхнего кадра ---
    y_top = 20
    for line in text_wrap(top, font_top, w * 0.9):
        tw = font_top.getlength(line)
        draw_text_outline(
            draw,
            line,
            ((w - tw) / 2, y_top),
            font_top
        )
        y_top += 45

    # --- Отрисовка центрального кадра ---
    y_mid = h * 0.38  # Ставим текст в начало второго кадра
    for line in text_wrap(middle, font_middle, w * 0.9):
        tw = font_middle.getlength(line)
        draw_text_outline(
            draw,
            line,
            ((w - tw) / 2, y_mid),
            font_middle
        )
        y_mid += 45

    # --- Отрисовка нижнего кадра ---
    y_bot = h * 0.72  # Ставим текст в начало третьего кадра
    for line in text_wrap(bottom, font_bottom, w * 0.9):
        tw = font_bottom.getlength(line)
        draw_text_outline(
            draw,
            line,
            ((w - tw) / 2, y_bot),
            font_bottom
        )
        y_bot += 55

    img.save(
        RESULT_NAME,
        "JPEG"
    )

    return RESULT_NAME

@bot.message_handler(commands=["make_meme"])
def make_meme_command(message):
    if message.chat.type not in ["group", "supergroup"]:
        bot.reply_to(
            message,
            "Эта команда работает только в группе."
        )
        return

    if len(chat_history) < 3:
        bot.reply_to(
            message,
            "Пока мало сообщений для мема. Нужно хотя бы 3."
        )
        return

    status = bot.reply_to(
        message,
        "Делаю мем..."
    )

    try:
        # Берем 3 случайных сообщения вместо 2
        a, b, c = random.sample(
            chat_history,
            3
        )

        result = generate_meme(a, b, c)

        if not result:
            raise RuntimeError(
                "Не найдены template.jpg или arial.ttf."
            )

        with open(result, "rb") as photo:
            bot.send_photo(
                message.chat.id,
                photo,
                reply_to_message_id=message.message_id
            )

        os.remove(result)

        try:
            bot.delete_message(
                message.chat.id,
                status.message_id
            )
        except Exception:
            pass

    except Exception as e:
        bot.edit_message_text(
            f"Ошибка мема: {e}",
            message.chat.id,
            status.message_id
        )

# ==========================================================
# START
# ==========================================================

@bot.message_handler(commands=["start"])
def start_command(message):
    bot.reply_to(
        message,
        "/draw — картинка\n"
        "/video — видео\n"
        "/edit — редактирование фото\n"
        "/make_meme — мем"
    )

# ==========================================================
# GENERAL CHAT
# ==========================================================

@bot.message_handler(
    content_types=["text", "photo", "voice", "audio"]
)
def handle_message(message):

    # Команды медиа не должны попадать в обычный Gemini-чат
    if any(
        is_command(message, cmd)
        for cmd in [
            ["draw", "gen"],
            ["video", "vid"],
            ["edit"]
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

    dialog_context.setdefault(chat_id, [])

    dialog_context[chat_id].append(
        f"{user_name}: {text or '[Медиафайл]'}"
    )

    dialog_context[chat_id] = dialog_context[chat_id][-CONTEXT_LIMIT:]

    if message.chat.type in ["group", "supergroup"]:

        mentioned = (
            text
            and BOT_USERNAME.lower()
            in text.lower()
        )

        replied = False

        try:
            replied = (
                message.reply_to_message
                and message.reply_to_message.from_user.id
                == bot.get_me().id
            )
        except Exception:
            pass

        if not (mentioned or replied):
            return

    try:
        history = "\n".join(
            dialog_context[chat_id]
        )

        prompt = (
            f"Последние сообщения:\n{history}\n\n"
            f"Ответь на последнее сообщение {user_name}."
        )

        contents = [prompt]

        if message.photo:

            info = bot.get_file(
                message.photo[-1].file_id
            )

            data = bot.download_file(
                info.file_path
            )

            contents.append(
                Image.open(
                    io.BytesIO(data)
                ).convert("RGB")
            )

        elif message.voice or message.audio:

            media = (
                message.voice
                if message.voice
                else message.audio
            )

            info = bot.get_file(
                media.file_id
            )

            data = bot.download_file(
                info.file_path
            )

            contents.append({
                "mime_type": (
                    "audio/ogg"
                    if message.voice
                    else "audio/mpeg"
                ),
                "data": data
            })

        response = model.generate_content(
            contents
        )

        reply = response.text.replace(
            "*",
            ""
        )

        bot.reply_to(
            message,
            reply
        )

        dialog_context[chat_id].append(
            f"читом бот: {reply}"
        )

    except Exception as e:
        print(
            "CHAT ERROR:",
            repr(e)
        )

        bot.reply_to(
            message,
            f"Мой клубок запутался. Ошибка: {e}"
        )

# ==========================================================
# HEALTH SERVER
# ==========================================================

class DummyHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"Chaitom bot is running"
        )

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def run_server():
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    HTTPServer(
        ("0.0.0.0", port),
        DummyHandler
    ).serve_forever()


threading.Thread(
    target=run_server,
    daemon=True
).start()

# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    print("Читом бот запущен")
    print("DRAW: Nano Banana Pro")
    print("VIDEO: Veo 3.1 Fast")
    bot.infinity_polling()
