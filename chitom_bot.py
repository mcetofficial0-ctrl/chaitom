import telebot
import os
import threading
import random
import io
import base64
import re
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

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

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN")

if not GEMINI_API_KEY:
    raise RuntimeError("Не задан GEMINI_API_KEY")


bot = telebot.TeleBot(
    TELEGRAM_TOKEN,
    parse_mode=None
)

# Старый google.generativeai используется для текстового чата
genai.configure(
    api_key=GEMINI_API_KEY
)

# Новый SDK используется для изображений и видео
image_client = new_genai.Client(
    api_key=GEMINI_API_KEY
)


# ==========================================================
# FILES
# ==========================================================

TEMPLATE_NAME = "template.jpg"
FONT_NAME = "arial.ttf"
RESULT_NAME = "meme_result.jpg"


# Обязательно укажи реальный username бота
BOT_USERNAME = "@chaitom_bot"


# ==========================================================
# CHAT HISTORY
# ==========================================================

chat_history = []
HISTORY_LIMIT = 1000

dialog_context = {}
CONTEXT_LIMIT = 15


# ==========================================================
# GEMINI TEXT MODEL
# ==========================================================

safety_settings = [
    {
        "category": "HARM_CATEGORY_HARASSMENT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_NONE"
    }
]


SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот".
Твой характер: ты ироничный, абсурдный и саркастичный шутник.

ГЛАВНОЕ ПРАВИЛО: ОТВЕЧАЙ ОЧЕНЬ КОРОТКО.
Твои сообщения должны состоять из 1-3 предложений максимум.
Никаких длинных абзацев и долгих монологов.
Руби с плеча, отвечай лаконично, дерзко и по факту.

ПРАВИЛА ТВОЕЙ ВСЕЛЕННОЙ (используй редко и к месту):
1. Слова: "читом", "клубок" и "бастурма".
2. Твои знакомые:
   - Степан Клитор — депрессивный музыкант.
   - Андрей Визард — фанат бургеров.
   - Роман Линкин — суровый фанат My Little Pony.

ВАЖНО:
Ни в коем случае не используй звездочки (*) и форматирование текста.
Пиши строго обычным текстом.
"""


model = genai.GenerativeModel(
    "gemini-3.6-flash",
    system_instruction=SYSTEM_PROMPT,
    safety_settings=safety_settings
)


# ==========================================================
# COMMON HELPERS
# ==========================================================

def is_temporary_gemini_error(exc):
    """
    Определяет временную ошибку Gemini.
    """

    text = str(exc).upper()

    return (
        "429" in text
        or "RESOURCE_EXHAUSTED" in text
        or "503" in text
        or "UNAVAILABLE" in text
        or "HIGH DEMAND" in text
        or "OVERLOADED" in text
        or "SERVICE UNAVAILABLE" in text
        or "TIMEOUT" in text
    )


def sleep_with_log(seconds):
    print(f"WAIT: {seconds} sec.")
    time.sleep(seconds)


# ==========================================================
# DRAW — NANO BANANA PRO
# ==========================================================

DRAW_MODELS = [
    {
        "name": "gemini-3-pro-image",
        "label": "Nano Banana Pro",
        "attempts": 3,
    },
    {
        "name": "gemini-3.1-flash-image",
        "label": "Nano Banana 2",
        "attempts": 2,
    },
]


def extract_image_bytes(response):
    """
    Извлекает raw image bytes из ответа Gemini.
    Работает с разными структурами ответа SDK.
    """

    # ------------------------------------------------------
    # response.parts
    # ------------------------------------------------------

    parts = getattr(response, "parts", None)

    if parts:
        for part in parts:
            inline_data = getattr(
                part,
                "inline_data",
                None
            )

            if inline_data is None:
                continue

            data = getattr(
                inline_data,
                "data",
                None
            )

            if not data:
                continue

            if isinstance(data, str):
                try:
                    return base64.b64decode(data)
                except Exception:
                    return data.encode("utf-8")

            return data

    # ------------------------------------------------------
    # candidates -> content -> parts
    # ------------------------------------------------------

    candidates = getattr(
        response,
        "candidates",
        None
    )

    if candidates:
        for candidate in candidates:

            content = getattr(
                candidate,
                "content",
                None
            )

            if not content:
                continue

            parts = getattr(
                content,
                "parts",
                None
            )

            if not parts:
                continue

            for part in parts:

                inline_data = getattr(
                    part,
                    "inline_data",
                    None
                )

                if inline_data is None:
                    continue

                data = getattr(
                    inline_data,
                    "data",
                    None
                )

                if not data:
                    continue

                if isinstance(data, str):
                    try:
                        return base64.b64decode(data)
                    except Exception:
                        return data.encode("utf-8")

                return data

    return None


def generate_draw_image(model_name, prompt):
    """
    Один запрос к image model.
    """

    response = image_client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"]
        )
    )

    image_bytes = extract_image_bytes(response)

    if not image_bytes:

        response_text = ""

        try:
            response_text = getattr(
                response,
                "text",
                ""
            ) or ""
        except Exception:
            pass

        raise RuntimeError(
            "Модель не вернула изображение."
            + (
                f" Ответ: {response_text[:500]}"
                if response_text
                else ""
            )
        )

    return image_bytes


@bot.message_handler(
    commands=["draw", "gen"]
)
def draw_command(message):

    user_prompt = (
        message.text
        .replace("/draw", "")
        .replace("/gen", "")
        .strip()
    )

    if not user_prompt:
        bot.reply_to(
            message,
            "Напиши, что нарисовать. Например: /draw кот в космосе"
        )
        return

    # ------------------------------------------------------
    # Русский промпт -> подробная инструкция для модели
    # ------------------------------------------------------

    prompt = f"""
Create the image exactly according to the user's request.

The user's request may be written in Russian.
Understand Russian naturally and preserve all details,
objects, characters, actions, composition, perspective,
lighting, atmosphere, visual style, camera angle,
environment and any requested text.

Do not change the intended meaning.
Do not add unnecessary elements.

USER REQUEST:
{user_prompt}
"""

    status_msg = bot.reply_to(
        message,
        "🍌 Nano Banana Pro рисует..."
    )

    last_error = None
    generated_image = None
    successful_model = None

    # ------------------------------------------------------
    # Models
    # ------------------------------------------------------

    for model_info in DRAW_MODELS:

        model_name = model_info["name"]
        model_label = model_info["label"]
        attempts = model_info["attempts"]

        for attempt in range(1, attempts + 1):

            try:

                print(
                    f"DRAW: {model_label} "
                    f"attempt {attempt}/{attempts}"
                )

                generated_image = generate_draw_image(
                    model_name,
                    prompt
                )

                successful_model = model_label

                print(
                    f"DRAW: success -> {model_label}"
                )

                break

            except Exception as e:

                last_error = e

                print(
                    f"DRAW ERROR: {model_label} "
                    f"attempt {attempt}/{attempts}: "
                    f"{repr(e)}"
                )

                # Не временная ошибка
                if not is_temporary_gemini_error(e):
                    break

                # Остались попытки
                if attempt < attempts:

                    delay = min(
                        2 ** attempt,
                        10
                    )

                    sleep_with_log(delay)

        if generated_image:
            break

        # Перед fallback
        if model_name != DRAW_MODELS[-1]["name"]:

            try:
                bot.edit_message_text(
                    "🍌 Pro занят, переключаюсь на Nano Banana 2...",
                    message.chat.id,
                    status_msg.message_id
                )
            except Exception:
                pass

    # ------------------------------------------------------
    # Send image
    # ------------------------------------------------------

    if generated_image:

        try:

            output = io.BytesIO(
                generated_image
            )

            output.seek(0)
            output.name = "generated.png"

            try:
                bot.delete_message(
                    message.chat.id,
                    status_msg.message_id
                )
            except Exception:
                pass

            bot.send_photo(
                chat_id=message.chat.id,
                photo=output,
                reply_to_message_id=message.message_id
            )

            print(
                f"DRAW: sent via {successful_model}"
            )

        except Exception as e:

            print(
                "DRAW TELEGRAM ERROR:",
                repr(e)
            )

            try:
                bot.edit_message_text(
                    f"Изображение создано, но Telegram не смог его отправить:\n{e}",
                    message.chat.id,
                    status_msg.message_id
                )
            except Exception:
                pass

        return

    # ------------------------------------------------------
    # All models failed
    # ------------------------------------------------------

    error_text = (
        "Не удалось сгенерировать изображение."
    )

    if last_error:
        error_text += (
            f"\n\nПоследняя ошибка:\n{last_error}"
        )

    print(
        "DRAW FINAL ERROR:",
        repr(last_error)
    )

    try:

        bot.edit_message_text(
            error_text,
            message.chat.id,
            status_msg.message_id
        )

    except Exception:

        bot.send_message(
            message.chat.id,
            error_text
        )


# ==========================================================
# EDIT IMAGE
# ==========================================================

def _generate_image_edit(
    model_name,
    edit_prompt,
    original
):
    """
    Один запрос на редактирование изображения.
    """

    return image_client.models.generate_content(
        model=model_name,
        contents=[
            edit_prompt,
            original,
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"]
        )
    )


def edit_user_image(
    image_bytes,
    user_prompt
):
    """
    Редактирование изображения через Nano Banana 2.
    """

    original = Image.open(
        io.BytesIO(image_bytes)
    ).convert("RGB")

    edit_prompt = f"""
Edit the provided image directly according to this instruction.

The user's instruction may be written in Russian.
Understand it naturally and preserve the intended meaning.

USER INSTRUCTION:
{user_prompt}

Keep the original image, subject identity,
composition, perspective, camera angle, lighting,
background and details unless the instruction explicitly
asks to change them.

Only make the requested edits.
Do not describe the image.
Do not answer with text.
Return the edited image itself.
"""

    models_to_try = [
        "gemini-3.1-flash-image",
        "gemini-3.1-flash-lite-image",
    ]

    last_error = None

    for model_name in models_to_try:

        attempts = (
            3
            if model_name == "gemini-3.1-flash-image"
            else 2
        )

        for attempt in range(attempts):

            try:

                print(
                    f"EDIT: model={model_name}, "
                    f"attempt={attempt + 1}/{attempts}"
                )

                response = _generate_image_edit(
                    model_name,
                    edit_prompt,
                    original
                )

                result_bytes = extract_image_bytes(
                    response
                )

                if result_bytes:

                    output = io.BytesIO(
                        result_bytes
                    )

                    output.seek(0)
                    output.name = "edited.png"

                    print(
                        "EDIT: image received from",
                        model_name
                    )

                    return output

                raise RuntimeError(
                    f"{model_name} не вернул изображение."
                )

            except Exception as e:

                last_error = e

                print(
                    f"EDIT ERROR {model_name}:",
                    repr(e)
                )

                if not is_temporary_gemini_error(e):
                    raise

                if attempt < attempts - 1:

                    delay = min(
                        2 ** (attempt + 1),
                        10
                    )

                    sleep_with_log(delay)

    raise RuntimeError(
        "Не удалось отредактировать изображение.\n"
        f"Последняя ошибка: {last_error}"
    )


def is_edit_message(message):

    text = (
        message.text
        or message.caption
        or ""
    ).strip()

    return bool(
        re.match(
            r"^/edit(?:@\w+)?(?:\s|$)",
            text,
            re.IGNORECASE
        )
    )


@bot.message_handler(
    func=is_edit_message,
    content_types=["text", "photo"]
)
def edit_command(message):

    print("========== /EDIT ==========")

    print(
        "TEXT:",
        repr(message.text)
    )

    print(
        "CAPTION:",
        repr(message.caption)
    )

    # Фото прямо в сообщении
    # или reply на существующее фото
    target_message = (
        message
        if message.photo
        else message.reply_to_message
    )

    if (
        not target_message
        or not target_message.photo
    ):
        bot.reply_to(
            message,
            "Прикрепи изображение к /edit "
            "или сделай reply на фото.\n\n"
            "Пример:\n"
            "/edit добавь ему солнечные очки"
        )
        return

    raw_text = (
        message.caption
        if message.photo
        else message.text
    )

    raw_text = raw_text or ""

    user_prompt = re.sub(
        r"^/edit(?:@\w+)?\s*",
        "",
        raw_text.strip(),
        count=1,
        flags=re.IGNORECASE
    ).strip()

    if not user_prompt:

        bot.reply_to(
            message,
            "Напиши промпт для изменения картинки.\n"
            "Например: /edit добавь ему солнечные очки"
        )

        return

    status = bot.reply_to(
        message,
        "Редактирую изображение..."
    )

    try:

        file_id = target_message.photo[-1].file_id

        file_info = bot.get_file(
            file_id
        )

        image_bytes = bot.download_file(
            file_info.file_path
        )

        print(
            "EDIT: downloaded",
            len(image_bytes),
            "bytes"
        )

        print(
            "EDIT PROMPT:",
            user_prompt
        )

        edited_photo = edit_user_image(
            image_bytes,
            user_prompt
        )

        edited_photo.seek(0)

        try:

            bot.delete_message(
                message.chat.id,
                status.message_id
            )

        except Exception:
            pass

        bot.send_photo(
            chat_id=message.chat.id,
            photo=edited_photo,
            reply_to_message_id=message.message_id
        )

        print(
            "EDIT: EDITED PHOTO SENT"
        )

    except Exception as e:

        print(
            "EDIT ERROR:",
            repr(e)
        )

        try:

            bot.edit_message_text(
                f"Ошибка редактирования: {e}",
                message.chat.id,
                status.message_id
            )

        except Exception:

            bot.send_message(
                message.chat.id,
                f"Ошибка редактирования: {e}"
            )


# ================= VEO 3.1 FAST =================

VEO_MODEL = "veo-3.1-fast-generate-preview"


def veo_temp_error(e):
    s = str(e).upper()
    return any(x in s for x in [
        "429", "503", "RESOURCE_EXHAUSTED",
        "UNAVAILABLE", "HIGH DEMAND",
        "OVERLOADED", "TIMEOUT", "DEADLINE"
    ])


def get_video_image(message):
    if message.photo:
        return message

    if message.reply_to_message and message.reply_to_message.photo:
        return message.reply_to_message

    return None


def download_video_image(message):
    file = bot.get_file(message.photo[-1].file_id)
    data = bot.download_file(file.file_path)
    return Image.open(io.BytesIO(data)).convert("RGB")


def generate_veo(prompt, message_id, image=None):
    veo_prompt = f"""
Create an 8-second video exactly according to the user's request.
The request may be written in Russian. Understand Russian naturally.
Preserve requested characters, objects, actions, camera movement,
lighting, atmosphere, style and sounds.

USER REQUEST:
{prompt}
"""

    config = types.GenerateVideosConfig(
        number_of_videos=1,
        resolution="720p",
        aspect_ratio="16:9"
    )

    if image:
        operation = image_client.models.generate_videos(
            model=VEO_MODEL,
            prompt=veo_prompt,
            image=image,
            config=config
        )
    else:
        operation = image_client.models.generate_videos(
            model=VEO_MODEL,
            prompt=veo_prompt,
            config=config
        )

    while not operation.done:
        time.sleep(10)
        operation = image_client.operations.get(operation)

    videos = getattr(operation.response, "generated_videos", None)

    if not videos or not videos[0].video:
        raise RuntimeError("Veo не вернул видео.")

    video = videos[0].video
    image_client.files.download(file=video)

    filename = f"veo_{message_id}_{int(time.time())}.mp4"
    video.save(filename)

    return filename


@bot.message_handler(
    commands=["video", "vid"],
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
            "Напиши, что снять.\n"
            "Например: /video кот идёт по Луне"
        )
        return

    try:
        source_image = download_video_image(source) if source else None
    except Exception as e:
        bot.reply_to(message, f"Ошибка загрузки фото: {e}")
        return

    status = bot.reply_to(
        message,
        "🎬 Оживляю изображение..." if source_image
        else "🎬 Veo 3.1 Fast рендерит..."
    )

    def task():
        video_file = None
        last_error = None

        for attempt in range(1, 4):
            try:
                video_file = generate_veo(
                    prompt,
                    message.message_id,
                    source_image
                )
                break

            except Exception as e:
                last_error = e
                print(f"VEO ERROR {attempt}/3:", repr(e))

                if not veo_temp_error(e) or attempt == 3:
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
        except Exception as e:
            bot.send_message(
                message.chat.id,
                f"Ошибка отправки видео: {e}"
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


# ==========================================================
# MEME
# ==========================================================

def draw_text_with_outline(
    draw,
    text,
    position,
    font,
    text_color="white",
    outline_color="black"
):

    x, y = position

    for adj in range(-2, 3):

        draw.text(
            (x + adj, y),
            text,
            font=font,
            fill=outline_color
        )

        draw.text(
            (x, y + adj),
            text,
            font=font,
            fill=outline_color
        )

    draw.text(
        (x, y),
        text,
        font=font,
        fill=text_color
    )


def text_wrap(
    text,
    font,
    max_width
):

    lines = []

    if font.getlength(text) <= max_width:

        lines.append(text)

    else:

        words = text.split(" ")
        i = 0

        while i < len(words):

            line = ""

            while (
                i < len(words)
                and font.getlength(
                    line + words[i]
                ) <= max_width
            ):

                line = (
                    line
                    + words[i]
                    + " "
                )

                i += 1

            if not line:

                line = words[i]
                i += 1

            lines.append(
                line.strip()
            )

    return lines


def generate_meme_image(
    top_text,
    bottom_text
):

    try:

        if not os.path.exists(
            TEMPLATE_NAME
        ):

            return (
                "ОШИБКА: Не найден "
                "template.jpg"
            )

        if not os.path.exists(
            FONT_NAME
        ):

            return (
                "ОШИБКА: Не найден "
                "arial.ttf"
            )

        img = Image.open(
            TEMPLATE_NAME
        ).convert("RGB")

        draw = ImageDraw.Draw(
            img
        )

        font_top = ImageFont.truetype(
            FONT_NAME,
            40
        )

        font_bottom = ImageFont.truetype(
            FONT_NAME,
            50
        )

        width, height = img.size

        max_txt_width = (
            width * 0.9
        )

        # TOP

        lines_top = text_wrap(
            top_text,
            font_top,
            max_txt_width
        )

        current_h = 20

        for line in lines_top:

            w = font_top.getlength(
                line
            )

            draw_text_with_outline(
                draw,
                line,
                (
                    (width - w) / 2,
                    current_h
                ),
                font_top
            )

            current_h += (
                font_top.getbbox(
                    line
                )[3]
                + 5
            )

        # BOTTOM

        lines_bottom = text_wrap(
            bottom_text,
            font_bottom,
            max_txt_width
        )

        current_h = (
            height * 0.7
        )

        for line in lines_bottom:

            w = font_bottom.getlength(
                line
            )

            draw_text_with_outline(
                draw,
                line,
                (
                    (width - w) / 2,
                    current_h
                ),
                font_bottom
            )

            current_h += (
                font_bottom.getbbox(
                    line
                )[3]
                + 5
            )

        img.save(
            RESULT_NAME,
            "JPEG"
        )

        return RESULT_NAME

    except Exception as e:

        return (
            f"ОШИБКА PILLOW: {e}"
        )


@bot.message_handler(
    commands=["make_meme"]
)
def make_meme_command(message):

    if message.chat.type not in [
        "group",
        "supergroup"
    ]:

        bot.reply_to(
            message,
            "Эту команду можно использовать "
            "только в групповом чате!"
        )

        return

    if len(chat_history) < 2:

        bot.reply_to(
            message,
            f"В истории пока маловато фраз "
            f"({len(chat_history)}). Пишите еще!"
        )

        return

    status_msg = bot.reply_to(
        message,
        "Делаю мем..."
    )

    try:

        phrases = random.sample(
            chat_history,
            2
        )

        meme_result = (
            generate_meme_image(
                phrases[0],
                phrases[1]
            )
        )

        if (
            meme_result
            and meme_result.startswith(
                "ОШИБКА"
            )
        ):

            bot.edit_message_text(
                f"Ошибка:\n{meme_result}",
                message.chat.id,
                status_msg.message_id
            )

        elif meme_result:

            with open(
                meme_result,
                "rb"
            ) as photo:

                bot.send_photo(
                    message.chat.id,
                    photo,
                    reply_to_message_id=message.message_id
                )

            try:
                os.remove(
                    meme_result
                )
            except Exception:
                pass

            try:
                bot.delete_message(
                    message.chat.id,
                    status_msg.message_id
                )
            except Exception:
                pass

        else:

            bot.edit_message_text(
                "Неизвестная ошибка мема.",
                message.chat.id,
                status_msg.message_id
            )

    except Exception as e:

        bot.edit_message_text(
            f"Системная ошибка: {e}",
            message.chat.id,
            status_msg.message_id
        )


# ==========================================================
# START
# ==========================================================

@bot.message_handler(
    commands=["start"]
)
def send_welcome(message):

    bot.reply_to(
        message,
        "Я на связи. "
        "/draw — рисовать ИИ, "
        "/video — сгенерировать видео, "
        "/make_meme — мем, "
        "/edit — фотошоп."
    )


# ==========================================================
# GENERAL MESSAGE HANDLER
# ==========================================================

@bot.message_handler(
    content_types=[
        "text",
        "photo",
        "voice",
        "audio"
    ]
)
def handle_message(message):

    # /edit обрабатывается отдельно
    if is_edit_message(message):
        return

    incoming_text = (
        message.text
        or message.caption
        or ""
    ).strip()

    if incoming_text.lower().startswith(
        "/edit"
    ):
        return

    chat_id = message.chat.id

    user_name = (
        message.from_user.first_name
        or "Аноним"
    )

    text = (
        message.text
        or message.caption
        or ""
    ).strip()

    # ------------------------------------------------------
    # Group history
    # ------------------------------------------------------

    if (
        message.chat.type
        in ["group", "supergroup"]
        and text
        and not text.startswith("/")
    ):

        if text not in chat_history:

            chat_history.append(
                text
            )

            if len(chat_history) > HISTORY_LIMIT:

                chat_history.pop(
                    0
                )

    # ------------------------------------------------------
    # Dialog context
    # ------------------------------------------------------

    if chat_id not in dialog_context:

        dialog_context[chat_id] = []

    log_text = (
        text
        if text
        else "[Медиафайл]"
    )

    dialog_context[chat_id].append(
        f"{user_name}: {log_text}"
    )

    if (
        len(dialog_context[chat_id])
        > CONTEXT_LIMIT
    ):

        dialog_context[chat_id].pop(
            0
        )

    # ------------------------------------------------------
    # Group mention / reply
    # ------------------------------------------------------

    if message.chat.type in [
        "group",
        "supergroup"
    ]:

        is_mentioned = (
            text
            and BOT_USERNAME.lower()
            in text.lower()
        )

        is_reply = False

        try:

            is_reply = (
                message.reply_to_message
                and message.reply_to_message.from_user.id
                == bot.get_me().id
            )

        except Exception:
            pass

        if not (
            is_mentioned
            or is_reply
        ):

            return

    # ------------------------------------------------------
    # Typing
    # ------------------------------------------------------

    bot.send_chat_action(
        chat_id,
        "typing"
    )

    try:

        history_text = "\n".join(
            dialog_context[chat_id]
        )

        prompt = (
            "Вот последние сообщения "
            "в этом чате:\n"
            f"{history_text}\n\n"
            f"Основываясь на этом диалоге, "
            f"ответь на последнее сообщение "
            f"пользователя {user_name}."
        )

        gemini_contents = [
            prompt
        ]

        # --------------------------------------------------
        # Photo
        # --------------------------------------------------

        if message.photo:

            file_id = (
                message.photo[-1].file_id
            )

            file_info = bot.get_file(
                file_id
            )

            downloaded_file = (
                bot.download_file(
                    file_info.file_path
                )
            )

            image = Image.open(
                io.BytesIO(
                    downloaded_file
                )
            )

            gemini_contents.append(
                image
            )

        # --------------------------------------------------
        # Voice / Audio
        # --------------------------------------------------

        elif (
            message.voice
            or message.audio
        ):

            file_id = (
                message.voice.file_id
                if message.voice
                else message.audio.file_id
            )

            file_info = bot.get_file(
                file_id
            )

            downloaded_file = (
                bot.download_file(
                    file_info.file_path
                )
            )

            mime_type = (
                "audio/ogg"
                if message.voice
                else "audio/mpeg"
            )

            gemini_contents.append(
                {
                    "mime_type": mime_type,
                    "data": downloaded_file
                }
            )

        # --------------------------------------------------
        # Gemini
        # --------------------------------------------------

        response = model.generate_content(
            gemini_contents
        )

        clean_reply = (
            response.text
            .replace("*", "")
        )

        bot.reply_to(
            message,
            clean_reply
        )

        dialog_context[chat_id].append(
            f"читом бот: {clean_reply}"
        )

        if (
            len(dialog_context[chat_id])
            > CONTEXT_LIMIT
        ):

            dialog_context[chat_id].pop(
                0
            )

    except Exception as e:

        print(
            f"Ошибка Gemini: {e}"
        )

        bot.reply_to(
            message,
            f"Мой клубок запутался. Ошибка: {e}"
        )


# ==========================================================
# RENDER / HEALTH CHECK SERVER
# ==========================================================

class DummyHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.end_headers()

        self.wfile.write(
            b"Chaitom bot with Veo 3.1 Fast is running!"
        )

    def do_HEAD(self):

        self.send_response(
            200
        )

        self.end_headers()

    def log_message(
        self,
        format,
        *args
    ):
        return


def run_dummy_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    server = HTTPServer(
        (
            "0.0.0.0",
            port
        ),
        DummyHandler
    )

    print(
        f"Health server running on port {port}"
    )

    server.serve_forever()


threading.Thread(
    target=run_dummy_server,
    daemon=True
).start()


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":

    print(
        "Читом бот запущен."
    )

    print(
        "Image: Nano Banana Pro"
    )

    print(
        "Video: Veo 3.1 Fast"
    )

    bot.infinity_polling()
