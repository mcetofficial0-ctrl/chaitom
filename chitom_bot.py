import telebot
import os
import threading
import random
import io
import json
import re

from http.server import BaseHTTPRequestHandler, HTTPServer

import google.generativeai as genai

from PIL import Image, ImageDraw, ImageFont, ImageFile

import cv2
import numpy as np


# =========================================================
# НАСТРОЙКИ
# =========================================================

ImageFile.LOAD_TRUNCATED_IMAGES = True

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не найден TELEGRAM_TOKEN")

if not GEMINI_API_KEY:
    raise RuntimeError("Не найден GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)


FONT_NAME = "arial.ttf"
TEMPLATE_NAME = "template.jpg"
RESULT_NAME = "meme_result.jpg"

BOT_USERNAME = "@chaitom_bot"

chat_history = []
HISTORY_LIMIT = 1000

dialog_context = {}
CONTEXT_LIMIT = 15


# =========================================================
# GEMINI
# =========================================================

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

Твой характер: ироничный, абсурдный и саркастичный шутник.
Твой юмор абстрактный и сюрреалистичный, но ты не скатываешься
в бессмысленный бред — всегда сохраняй нить беседы.

Если используешь мат, то редко и исключительно для комедийного акцента.

Ты умеешь видеть картинки и анализировать их.

ПРАВИЛА ТВОЕЙ ВСЕЛЕННОЙ И ЛОР:

1. Периодически вплетай в диалог слова:
"читом", "клубок" и "бастурма".

2. В твоей памяти живут люди:
- Степан Клитор — депрессивный музыкант с суицидальными мыслями.
- Андрей Визард — человек, чья душа и тело требуют только бургеров.
- Роман Линкин — суровый взрослый лоб, фанатеющий от My Little Pony.

ВАЖНО:
Ни в коем случае не используй звездочки (*) и форматирование текста.
Пиши обычным текстом.
"""


model = genai.GenerativeModel(
    "gemini-3.6-flash",
    system_instruction=SYSTEM_PROMPT,
    safety_settings=safety_settings
)


# =========================================================
# РИСОВАНИЕ ТЕКСТА
# =========================================================

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


# =========================================================
# РАЗБОР /EDIT
# =========================================================

def parse_edit_prompt(prompt):

    prompt = prompt.strip()

    # "замени надпись root beer на chitom"
    pattern = (
        r"^(?:замени|поменяй|заменить)"
        r"\s+"
        r"(?:надпись\s+)?"
        r"(.+?)"
        r"\s+на\s+"
        r"(.+?)$"
    )

    match = re.match(
        pattern,
        prompt,
        re.IGNORECASE
    )

    if match:
        old_text = match.group(1).strip()
        new_text = match.group(2).strip()

        return old_text, new_text

    return None, None


# =========================================================
# GEMINI НАХОДИТ СТАРУЮ НАДПИСЬ
# =========================================================

def find_text_area_with_gemini(image, old_text):

    width, height = image.size

    prompt = f"""
Посмотри на изображение.

Найди на нём надпись:

{old_text}

Определи прямоугольник, полностью покрывающий эту надпись.

ВАЖНО:
Изображение имеет размер {width}x{height} пикселей.

Верни ТОЛЬКО JSON:

{{"x1": 0, "y1": 0, "x2": 100, "y2": 100}}

x1 — левая граница
y1 — верхняя граница
x2 — правая граница
y2 — нижняя граница

Не пиши никаких пояснений.
"""

    try:

        response = model.generate_content([
            prompt,
            image
        ])

        result = response.text.strip()

        print("GEMINI BBOX:", result)

        result = result.replace("```json", "")
        result = result.replace("```", "").strip()

        match = re.search(
            r"\{.*?\}",
            result,
            re.DOTALL
        )

        if not match:
            return None

        data = json.loads(
            match.group(0)
        )

        bbox = [
            int(data["x1"]),
            int(data["y1"]),
            int(data["x2"]),
            int(data["y2"])
        ]

        # Проверяем координаты
        bbox[0] = max(0, min(bbox[0], width))
        bbox[1] = max(0, min(bbox[1], height))
        bbox[2] = max(0, min(bbox[2], width))
        bbox[3] = max(0, min(bbox[3], height))

        print("BBOX:", bbox)

        return bbox

    except Exception as e:

        print(
            "Ошибка поиска текста:",
            repr(e)
        )

        return None


# =========================================================
# УДАЛЕНИЕ СТАРОГО ТЕКСТА
# =========================================================

def remove_text_from_image(image, bbox):

    height, width = image.shape[:2]

    x1, y1, x2, y2 = bbox

    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(0, min(x2, width))
    y2 = max(0, min(y2, height))

    if x2 <= x1 or y2 <= y1:
        return image

    # Расширяем область вокруг текста
    padding_x = max(
        5,
        int((x2 - x1) * 0.08)
    )

    padding_y = max(
        5,
        int((y2 - y1) * 0.15)
    )

    x1 = max(
        0,
        x1 - padding_x
    )

    y1 = max(
        0,
        y1 - padding_y
    )

    x2 = min(
        width,
        x2 + padding_x
    )

    y2 = min(
        height,
        y2 + padding_y
    )

    mask = np.zeros(
        (height, width),
        dtype=np.uint8
    )

    mask[y1:y2, x1:x2] = 255

    # Удаляем текст
    result = cv2.inpaint(
        image,
        mask,
        7,
        cv2.INPAINT_TELEA
    )

    return result


# =========================================================
# ОСНОВНОЕ РЕДАКТИРОВАНИЕ
# =========================================================

def edit_user_image(image_bytes, user_prompt):

    try:

        print("EDIT: начинаю обработку")

        # Открываем оригинал
        pil_img = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        print(
            "EDIT: размер:",
            pil_img.size
        )

        # Разбираем запрос
        old_text, new_text = parse_edit_prompt(
            user_prompt
        )

        if not old_text or not new_text:

            print(
                "EDIT: не удалось разобрать:",
                user_prompt
            )

            return None

        print(
            "EDIT OLD:",
            old_text
        )

        print(
            "EDIT NEW:",
            new_text
        )

        # Gemini ищет старую надпись
        bbox = find_text_area_with_gemini(
            pil_img,
            old_text
        )

        if not bbox:

            print(
                "EDIT: надпись не найдена"
            )

            return None

        # PIL -> OpenCV
        cv_image = np.array(
            pil_img
        )

        cv_image = cv2.cvtColor(
            cv_image,
            cv2.COLOR_RGB2BGR
        )

        # Стираем старую надпись
        cv_image = remove_text_from_image(
            cv_image,
            bbox
        )

        # OpenCV -> PIL
        cv_image = cv2.cvtColor(
            cv_image,
            cv2.COLOR_BGR2RGB
        )

        result_img = Image.fromarray(
            cv_image
        )

        # Рисуем новую
        draw = ImageDraw.Draw(
            result_img
        )

        x1, y1, x2, y2 = bbox

        area_width = x2 - x1
        area_height = y2 - y1

        font_size = max(
            12,
            int(area_height * 0.75)
        )

        try:

            font = ImageFont.truetype(
                FONT_NAME,
                font_size
            )

        except:

            font = ImageFont.load_default()


        # Подгоняем текст
        while font_size > 10:

            try:

                tb = draw.textbbox(
                    (0, 0),
                    new_text.upper(),
                    font=font
                )

                text_width = (
                    tb[2] - tb[0]
                )

                if text_width <= area_width:
                    break

                font_size -= 1

                font = ImageFont.truetype(
                    FONT_NAME,
                    font_size
                )

            except:

                break


        tb = draw.textbbox(
            (0, 0),
            new_text.upper(),
            font=font
        )

        text_width = tb[2] - tb[0]
        text_height = tb[3] - tb[1]

        text_x = (
            x1 +
            (area_width - text_width) / 2
        )

        text_y = (
            y1 +
            (area_height - text_height) / 2
        )

        draw_text_with_outline(
            draw,
            new_text.upper(),
            (text_x, text_y),
            font,
            text_color="white",
            outline_color="black"
        )

        # Сохраняем в памяти
        output = io.BytesIO()

        result_img.save(
            output,
            format="JPEG",
            quality=95
        )

        output.seek(0)

        print(
            "EDIT: картинка готова"
        )

        return output

    except Exception as e:

        print(
            "EDIT ERROR:",
            repr(e)
        )

        return None


# =========================================================
# MAKE MEME
# =========================================================

def text_wrap(text, font, max_width):

    lines = []

    if font.getlength(text) <= max_width:
        return [text]

    words = text.split(" ")

    current = ""

    for word in words:

        test = (
            current + " " + word
        ).strip()

        if font.getlength(test) <= max_width:

            current = test

        else:

            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

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
                "ОШИБКА: Не найден template.jpg"
            )

        if not os.path.exists(
            FONT_NAME
        ):
            return (
                "ОШИБКА: Не найден arial.ttf"
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

        max_width = width * 0.9

        # Верх
        lines = text_wrap(
            top_text,
            font_top,
            max_width
        )

        current_y = 20

        for line in lines:

            w = font_top.getlength(
                line
            )

            draw_text_with_outline(
                draw,
                line,
                (
                    (width - w) / 2,
                    current_y
                ),
                font_top
            )

            current_y += (
                font_top.getbbox(line)[3]
                + 5
            )

        # Низ
        lines = text_wrap(
            bottom_text,
            font_bottom,
            max_width
        )

        current_y = height * 0.7

        for line in lines:

            w = font_bottom.getlength(
                line
            )

            draw_text_with_outline(
                draw,
                line,
                (
                    (width - w) / 2,
                    current_y
                ),
                font_bottom
            )

            current_y += (
                font_bottom.getbbox(line)[3]
                + 5
            )

        img.save(
            RESULT_NAME,
            quality=95
        )

        return RESULT_NAME

    except Exception as e:

        return f"ОШИБКА PILLOW: {e}"


# =========================================================
# START
# =========================================================

@bot.message_handler(commands=["start"])
def start_command(message):

    bot.reply_to(
        message,
        "Я на связи! Умею делать мемы (/make_meme), "
        "изменять картинки (/edit) и общаться."
    )


# =========================================================
# ЕДИНЫЙ ОБРАБОТЧИК
#
# ВАЖНО:
# Здесь /edit проверяется ПЕРВЫМ.
# =========================================================

@bot.message_handler(
    content_types=[
        "text",
        "photo",
        "voice",
        "audio"
    ]
)
def handle_message(message):

    print(
        "MESSAGE:",
        repr(message.text)
    )

    # =====================================================
    # /EDIT
    # =====================================================

    if (
        message.text
        and
        message.text.strip().lower().startswith("/edit")
    ):

        print(
            "================================"
        )

        print(
            "EDIT COMMAND DETECTED"
        )

        print(
            "================================"
        )

        # Проверяем reply
        if not message.reply_to_message:

            bot.reply_to(
                message,
                "Сделай reply именно на картинку."
            )

            return

        target = message.reply_to_message

        # Проверяем фото
        if not target.photo:

            bot.reply_to(
                message,
                "Сообщение, на которое ты отвечаешь, "
                "не содержит фотографии."
            )

            return

        # Получаем промпт
        user_prompt = message.text[
            len("/edit"):
        ].strip()

        if not user_prompt:

            bot.reply_to(
                message,
                "Напиши, что изменить.\n\n"
                "Например:\n"
                "/edit замени надпись root beer на chitom"
            )

            return

        # Статус
        status = bot.reply_to(
            message,
            "Редактирую картинку..."
        )

        try:

            # Получаем Telegram photo
            file_id = target.photo[-1].file_id

            print(
                "EDIT FILE ID:",
                file_id
            )

            file_info = bot.get_file(
                file_id
            )

            downloaded = bot.download_file(
                file_info.file_path
            )

            print(
                "EDIT DOWNLOADED:",
                len(downloaded),
                "bytes"
            )

            # Редактируем
            result = edit_user_image(
                downloaded,
                user_prompt
            )

            if result is None:

                bot.edit_message_text(
                    "Не удалось обработать картинку. "
                    "Проверь название старой надписи.",
                    message.chat.id,
                    status.message_id
                )

                return

            # =================================================
            # САМОЕ ВАЖНОЕ
            # ОТПРАВЛЯЕМ РЕЗУЛЬТАТ КАК PHOTO
            # =================================================

            result.seek(0)

            sent = bot.send_photo(
                chat_id=message.chat.id,
                photo=result,
                reply_to_message_id=message.message_id
            )

            print(
                "EDIT PHOTO SENT:",
                sent.message_id
            )

            # Удаляем статус
            try:

                bot.delete_message(
                    message.chat.id,
                    status.message_id
                )

            except Exception:

                pass

        except Exception as e:

            print(
                "EDIT FULL ERROR:",
                repr(e)
            )

            try:

                bot.edit_message_text(
                    f"Ошибка редактирования:\n{e}",
                    message.chat.id,
                    status.message_id
                )

            except:

                bot.send_message(
                    message.chat.id,
                    f"Ошибка редактирования:\n{e}"
                )

        # =================================================
        # КРИТИЧЕСКИ ВАЖНО
        # Не продолжаем обычную обработку Gemini
        # =================================================

        return


    # =====================================================
    # /MAKE_MEME
    # =====================================================

    if (
        message.text
        and
        message.text.strip().lower().startswith(
            "/make_meme"
        )
    ):

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
                f"В истории пока мало фраз "
                f"({len(chat_history)})."
            )

            return

        status = bot.reply_to(
            message,
            "Разматываю клубок истории..."
        )

        try:

            phrases = random.sample(
                chat_history,
                2
            )

            result = generate_meme_image(
                phrases[0],
                phrases[1]
            )

            if (
                isinstance(result, str)
                and
                result.startswith("ОШИБКА")
            ):

                bot.edit_message_text(
                    result,
                    message.chat.id,
                    status.message_id
                )

                return

            with open(
                result,
                "rb"
            ) as photo:

                bot.send_photo(
                    message.chat.id,
                    photo,
                    reply_to_message_id=message.message_id
                )

            os.remove(result)

            bot.delete_message(
                message.chat.id,
                status.message_id
            )

        except Exception as e:

            bot.edit_message_text(
                f"Ошибка создания мема: {e}",
                message.chat.id,
                status.message_id
            )

        return


    # =====================================================
    # ОСТАЛЬНЫЕ СООБЩЕНИЯ
    # =====================================================

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


    # =====================================================
    # ИСТОРИЯ
    # =====================================================

    if (
        message.chat.type in [
            "group",
            "supergroup"
        ]
        and
        text
        and
        not text.startswith("/")
    ):

        if text not in chat_history:

            chat_history.append(text)

            if len(chat_history) > HISTORY_LIMIT:
                chat_history.pop(0)


    # =====================================================
    # КОНТЕКСТ
    # =====================================================

    if chat_id not in dialog_context:

        dialog_context[chat_id] = []


    dialog_context[chat_id].append(
        f"{user_name}: "
        f"{text if text else '[Медиафайл]'}"
    )


    if len(dialog_context[chat_id]) > CONTEXT_LIMIT:

        dialog_context[chat_id].pop(0)


    # =====================================================
    # ГРУППЫ
    # =====================================================

    if message.chat.type in [
        "group",
        "supergroup"
    ]:

        is_mentioned = (
            text
            and
            BOT_USERNAME.lower()
            in text.lower()
        )

        try:

            bot_id = bot.get_me().id

            is_reply = (
                message.reply_to_message
                and
                message.reply_to_message.from_user
                and
                message.reply_to_message.from_user.id
                == bot_id
            )

        except:

            is_reply = False


        if not (
            is_mentioned
            or
            is_reply
        ):

            return


    # =====================================================
    # GEMINI ОБЫЧНОГО ДИАЛОГА
    # =====================================================

    bot.send_chat_action(
        chat_id,
        "typing"
    )

    try:

        history = "\n".join(
            dialog_context[chat_id]
        )

        prompt = (
            f"Вот контекст диалога:\n"
            f"{history}\n\n"
            f"Ответь на последнее сообщение "
            f"пользователя {user_name}."
        )

        contents = [
            prompt
        ]


        # Фото
        if message.photo:

            file_id = message.photo[-1].file_id

            file_info = bot.get_file(
                file_id
            )

            downloaded = bot.download_file(
                file_info.file_path
            )

            image = Image.open(
                io.BytesIO(downloaded)
            ).convert("RGB")

            contents.append(
                image
            )


        # Голос
        elif message.voice:

            file_info = bot.get_file(
                message.voice.file_id
            )

            downloaded = bot.download_file(
                file_info.file_path
            )

            contents.append(
                {
                    "mime_type": "audio/ogg",
                    "data": downloaded
                }
            )


        # Аудио
        elif message.audio:

            file_info = bot.get_file(
                message.audio.file_id
            )

            downloaded = bot.download_file(
                file_info.file_path
            )

            contents.append(
                {
                    "mime_type": "audio/mpeg",
                    "data": downloaded
                }
            )


        response = model.generate_content(
            contents
        )

        reply = (
            response.text
            .replace("*", "")
            .strip()
        )

        bot.reply_to(
            message,
            reply
        )


        dialog_context[chat_id].append(
            f"читом бот: {reply}"
        )


        if len(dialog_context[chat_id]) > CONTEXT_LIMIT:

            dialog_context[chat_id].pop(0)


    except Exception as e:

        print(
            "GEMINI ERROR:",
            repr(e)
        )

        bot.reply_to(
            message,
            f"Ой, мой клубок запутался. Ошибка: {e}"
        )


# =========================================================
# HTTP SERVER
# =========================================================

class DummyHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(
            b"Chitom bot is running!"
        )

    def log_message(
        self,
        format,
        *args
    ):
        pass


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

    server.serve_forever()


threading.Thread(
    target=run_dummy_server,
    daemon=True
).start()


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    print(
        "================================"
    )

    print(
        "ЧИТОМ БОТ ЗАПУЩЕН"
    )

    print(
        "Версия: EDIT-FIX-2026"
    )

    print(
        "================================"
    )

    bot.infinity_polling(
        skip_pending=True
    )
