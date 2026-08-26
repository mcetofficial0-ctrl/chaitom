import telebot
import os
import threading
import random
import io
import json
import re

from http.server import BaseHTTPRequestHandler, HTTPServer

import google.generativeai as genai

from PIL import (
    Image,
    ImageDraw,
    ImageFont,
    ImageFile,
    ImageOps
)

import cv2
import numpy as np


# =========================================================
# НАСТРОЙКИ
# =========================================================

ImageFile.LOAD_TRUNCATED_IMAGES = True

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)


TEMPLATE_NAME = "template.jpg"
FONT_NAME = "arial.ttf"
RESULT_NAME = "meme_result.jpg"

BOT_USERNAME = "@chaitom_bot"

chat_history = []
HISTORY_LIMIT = 1000

dialog_context = {}
CONTEXT_LIMIT = 15


# =========================================================
# GEMINI SAFETY
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


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот".

Твой характер: ты ироничный, абсурдный и саркастичный шутник.
Твой юмор абстрактный и сюрреалистичный, но ты не скатываешься
в бессмысленный бред — всегда сохраняй нить беседы.

Если используешь мат, то редко и исключительно для комедийного акцента.

Ты умеешь видеть картинки и анализировать их.

ПРАВИЛА ТВОЕЙ ВСЕЛЕННОЙ И ЛОР:

1. Периодически вплетай в диалог слова:
"читом", "клубок" и "бастурма".

2. В твоей памяти живут люди, которых ты иногда упоминаешь:
- Степан Клитор — депрессивный музыкант с суицидальными мыслями.
- Андрей Визард — человек, чья душа и тело требуют только бургеров.
- Роман Линкин — суровый взрослый лоб, фанатеющий от My Little Pony.

ВАЖНО:
Ни в коем случае не используй звездочки (*) и форматирование текста.
Пиши строго обычным текстом.
"""


model = genai.GenerativeModel(
    "gemini-3.6-flash",
    system_instruction=SYSTEM_PROMPT,
    safety_settings=safety_settings
)


# =========================================================
# TEXT DRAWING
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
# ПОИСК ОБЛАСТИ С ТЕКСТОМ ЧЕРЕЗ GEMINI
# =========================================================

def find_text_area_with_gemini(image, old_text):

    prompt = f"""
Посмотри внимательно на это изображение.

Найди текст:

"{old_text}"

Нужно определить прямоугольную область, которая полностью
покрывает этот текст.

Верни ТОЛЬКО JSON следующего вида:

{{"x1": 100, "y1": 100, "x2": 300, "y2": 200}}

Где:
x1 — левая координата
y1 — верхняя координата
x2 — правая координата
y2 — нижняя координата

Координаты должны быть в пикселях относительно исходного изображения.

Не пиши никаких пояснений.
Только JSON.
"""

    try:

        response = model.generate_content([
            prompt,
            image
        ])

        result = response.text.strip()

        print("Gemini bbox response:", result)

        # Удаляем markdown-блоки, если Gemini их добавил
        result = result.replace("```json", "")
        result = result.replace("```", "")
        result = result.strip()

        # Ищем JSON даже если Gemini добавил текст
        json_match = re.search(
            r'\{.*?\}',
            result,
            re.DOTALL
        )

        if not json_match:
            print("Gemini не вернул JSON")
            return None

        data = json.loads(
            json_match.group(0)
        )

        bbox = [
            int(data["x1"]),
            int(data["y1"]),
            int(data["x2"]),
            int(data["y2"])
        ]

        print("Найдена область:", bbox)

        return bbox

    except Exception as e:

        print(
            "Ошибка определения области текста:",
            repr(e)
        )

        return None


# =========================================================
# УДАЛЕНИЕ ТЕКСТА
# =========================================================

def remove_text_from_image(img, bbox):

    try:

        height, width = img.shape[:2]

        x1, y1, x2, y2 = bbox

        # Ограничиваем координаты
        x1 = max(
            0,
            min(x1, width - 1)
        )

        y1 = max(
            0,
            min(y1, height - 1)
        )

        x2 = max(
            0,
            min(x2, width)
        )

        y2 = max(
            0,
            min(y2, height)
        )

        if x2 <= x1 or y2 <= y1:
            return img

        # Небольшой запас вокруг текста
        padding = max(
            5,
            int(min(width, height) * 0.015)
        )

        x1 = max(
            0,
            x1 - padding
        )

        y1 = max(
            0,
            y1 - padding
        )

        x2 = min(
            width,
            x2 + padding
        )

        y2 = min(
            height,
            y2 + padding
        )

        # Создаём маску
        mask = np.zeros(
            (height, width),
            dtype=np.uint8
        )

        mask[
            y1:y2,
            x1:x2
        ] = 255

        # Inpainting
        result = cv2.inpaint(
            img,
            mask,
            5,
            cv2.INPAINT_TELEA
        )

        return result

    except Exception as e:

        print(
            "Ошибка inpainting:",
            repr(e)
        )

        return img


# =========================================================
# РАЗБОР КОМАНДЫ /edit
# =========================================================

def parse_edit_prompt(user_prompt):

    """
    Пример:

    замени надпись root beer на chitom

    возвращает:

    old_text = root beer
    new_text = chitom
    """

    text = user_prompt.strip()

    # Основной вариант
    pattern = (
        r'^(?:замени|поменяй|заменить)'
        r'\s+'
        r'(?:надпись\s+)?'
        r'(.+?)'
        r'\s+на\s+'
        r'(.+?)$'
    )

    match = re.match(
        pattern,
        text,
        re.IGNORECASE
    )

    if match:

        old_text = match.group(1).strip()
        new_text = match.group(2).strip()

        return old_text, new_text

    # Вариант без слова "надпись"
    pattern = (
        r'^(.+?)\s+на\s+(.+?)$'
    )

    match = re.match(
        pattern,
        text,
        re.IGNORECASE
    )

    if match:

        old_text = match.group(1).strip()
        new_text = match.group(2).strip()

        old_text = re.sub(
            r'^(замени|поменяй|заменить)\s+',
            '',
            old_text,
            flags=re.IGNORECASE
        )

        old_text = re.sub(
            r'^надпись\s+',
            '',
            old_text,
            flags=re.IGNORECASE
        )

        return old_text, new_text

    return None, None


# =========================================================
# ОСНОВНОЕ РЕДАКТИРОВАНИЕ КАРТИНКИ
# =========================================================

def edit_user_image(image_bytes, user_prompt):

    try:

        # ---------------------------------------------
        # Открываем изображение
        # ---------------------------------------------

        pil_img = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        print(
            "Размер исходного изображения:",
            pil_img.size
        )


        # ---------------------------------------------
        # Определяем старый и новый текст
        # ---------------------------------------------

        old_text, new_text = parse_edit_prompt(
            user_prompt
        )

        if not old_text or not new_text:

            print(
                "Не удалось разобрать команду:",
                user_prompt
            )

            return None


        print(
            "Старый текст:",
            old_text
        )

        print(
            "Новый текст:",
            new_text
        )


        # ---------------------------------------------
        # Gemini ищет текст
        # ---------------------------------------------

        bbox = find_text_area_with_gemini(
            pil_img,
            old_text
        )

        if not bbox:

            print(
                "Не удалось найти старую надпись"
            )

            return None


        # ---------------------------------------------
        # OpenCV
        # ---------------------------------------------

        img = np.array(
            pil_img
        )

        # RGB -> BGR
        img = cv2.cvtColor(
            img,
            cv2.COLOR_RGB2BGR
        )


        # ---------------------------------------------
        # Удаляем старую надпись
        # ---------------------------------------------

        img = remove_text_from_image(
            img,
            bbox
        )


        # ---------------------------------------------
        # BGR -> RGB
        # ---------------------------------------------

        img = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2RGB
        )

        result_img = Image.fromarray(
            img
        )


        # ---------------------------------------------
        # Новая надпись
        # ---------------------------------------------

        draw = ImageDraw.Draw(
            result_img
        )

        width, height = result_img.size

        x1, y1, x2, y2 = bbox

        text_width = x2 - x1
        text_height = y2 - y1


        # Начальный размер шрифта
        font_size = max(
            12,
            int(text_height * 0.8)
        )


        # Загружаем шрифт
        try:

            font = ImageFont.truetype(
                FONT_NAME,
                font_size
            )

        except Exception:

            font = ImageFont.load_default()


        # ---------------------------------------------
        # Подгоняем размер текста
        # ---------------------------------------------

        while font_size > 10:

            try:

                text_bbox = draw.textbbox(
                    (0, 0),
                    new_text.upper(),
                    font=font
                )

                current_width = (
                    text_bbox[2] -
                    text_bbox[0]
                )

                if current_width <= text_width:
                    break

                font_size -= 1

                font = ImageFont.truetype(
                    FONT_NAME,
                    font_size
                )

            except Exception:

                break


        # ---------------------------------------------
        # Размер новой надписи
        # ---------------------------------------------

        text_bbox = draw.textbbox(
            (0, 0),
            new_text.upper(),
            font=font
        )

        new_width = (
            text_bbox[2] -
            text_bbox[0]
        )

        new_height = (
            text_bbox[3] -
            text_bbox[1]
        )


        # ---------------------------------------------
        # Центрируем новую надпись
        # ---------------------------------------------

        text_x = (
            x1 +
            (text_width - new_width) / 2
        )

        text_y = (
            y1 +
            (text_height - new_height) / 2
        )


        # ---------------------------------------------
        # Рисуем
        # ---------------------------------------------

        draw_text_with_outline(
            draw,
            new_text.upper(),
            (text_x, text_y),
            font,
            text_color="white",
            outline_color="black"
        )


        # ---------------------------------------------
        # Сохраняем в память
        # ---------------------------------------------

        bio = io.BytesIO()

        result_img.save(
            bio,
            format="JPEG",
            quality=95
        )

        bio.seek(0)

        print(
            "Изображение успешно обработано"
        )

        return bio


    except Exception as e:

        print(
            "ОШИБКА edit_user_image:",
            repr(e)
        )

        return None


# =========================================================
# СОЗДАНИЕ МЕМА
# =========================================================

def text_wrap(text, font, max_width):

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
                and
                font.getlength(
                    line + words[i]
                ) <= max_width
            ):

                line += words[i] + " "

                i += 1

            if not line:

                line = words[i]

                i += 1

            lines.append(
                line.strip()
            )

    return lines


def generate_meme_image(top_text, bottom_text):

    try:

        if not os.path.exists(
            TEMPLATE_NAME
        ):

            return (
                "ОШИБКА: Не найден файл "
                "'template.jpg'"
            )


        if not os.path.exists(
            FONT_NAME
        ):

            return (
                "ОШИБКА: Не найден файл "
                "'arial.ttf'"
            )


        img = Image.open(
            TEMPLATE_NAME
        )

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

        max_txt_width = width * 0.9


        # Верхний текст
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
                font_top.getbbox(line)[3]
                + 5
            )


        # Нижний текст
        lines_bottom = text_wrap(
            bottom_text,
            font_bottom,
            max_txt_width
        )

        current_h = height * 0.7


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
                font_bottom.getbbox(line)[3]
                + 5
            )


        img.save(
            RESULT_NAME
        )

        return RESULT_NAME


    except Exception as e:

        return (
            f"ОШИБКА PILLOW: {e}"
        )


# =========================================================
# START
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def send_welcome(message):

    bot.reply_to(
        message,
        "Я на связи! Умею делать мемы "
        "(/make_meme), изменять картинки "
        "по промпту (/edit) и общаться."
    )


# =========================================================
# MAKE MEME
# =========================================================

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
        "Разматываю клубок истории, сейчас будет мем..."
    )


    try:

        phrases = random.sample(
            chat_history,
            2
        )

        meme_result = generate_meme_image(
            phrases[0],
            phrases[1]
        )


        if (
            meme_result
            and
            meme_result.startswith("ОШИБКА")
        ):

            bot.edit_message_text(
                f"Ой, моя бастурма упала. "
                f"Причина:\n{meme_result}",
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


            os.remove(
                meme_result
            )


            bot.delete_message(
                message.chat.id,
                status_msg.message_id
            )


        else:

            bot.edit_message_text(
                "Неизвестная ошибка создания мема.",
                message.chat.id,
                status_msg.message_id
            )


    except Exception as e:

        bot.edit_message_text(
            f"Системная ошибка: {e}",
            message.chat.id,
            status_msg.message_id
        )


# =========================================================
# ОБЫЧНЫЕ СООБЩЕНИЯ + /edit
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

    # =====================================================
    # /EDIT
    # ОБРАБАТЫВАЕМ ДО ЛЮБОЙ GEMINI-ЛОГИКИ
    # =====================================================

    if (
        message.text
        and
        message.text.strip().lower().startswith("/edit")
    ):

        print()
        print("==============================")
        print("=== /EDIT COMMAND DETECTED ===")
        print("==============================")
        print(
            "TEXT:",
            message.text
        )


        # Команда должна быть reply на картинку
        if (
            not message.reply_to_message
            or
            not message.reply_to_message.photo
        ):

            bot.reply_to(
                message,
                "Сделай реплай на картинку и напиши:\n\n"
                "/edit замени надпись root beer на chitom"
            )

            return


        target_message = (
            message.reply_to_message
        )


        user_prompt = (
            message.text
            .replace(
                "/edit",
                "",
                1
            )
            .strip()
        )


        if not user_prompt:

            bot.reply_to(
                message,
                "Напиши, что именно заменить.\n\n"
                "Например:\n"
                "/edit замени надпись root beer на chitom"
            )

            return


        status_msg = bot.reply_to(
            message,
            "Ищу старую надпись и стираю её..."
        )


        try:

            # ---------------------------------------------
            # Получаем Telegram-файл
            # ---------------------------------------------

            file_id = (
                target_message
                .photo[-1]
                .file_id
            )


            print(
                "FILE ID:",
                file_id
            )


            file_info = bot.get_file(
                file_id
            )


            downloaded_file = bot.download_file(
                file_info.file_path
            )


            print(
                "Фото скачано:",
                len(downloaded_file),
                "bytes"
            )


            # ---------------------------------------------
            # Редактируем
            # ---------------------------------------------

            photo_bio = edit_user_image(
                downloaded_file,
                user_prompt
            )


            if photo_bio is None:

                bot.edit_message_text(
                    "Не удалось найти или заменить "
                    "старую надпись.",
                    message.chat.id,
                    status_msg.message_id
                )

                return


            # ---------------------------------------------
            # Отправляем ИМЕННО КАРТИНКУ
            # ---------------------------------------------

            photo_bio.seek(0)


            print(
                "Отправляю изменённую картинку..."
            )


            bot.send_photo(
                chat_id=message.chat.id,
                photo=photo_bio,
                reply_to_message_id=message.message_id
            )


            # ---------------------------------------------
            # Удаляем статус
            # ---------------------------------------------

            try:

                bot.delete_message(
                    message.chat.id,
                    status_msg.message_id
                )

            except Exception:

                pass


            print(
                "=== /EDIT SUCCESS ==="
            )


        except Exception as e:

            print()
            print(
                "=== /EDIT ERROR ==="
            )
            print(
                repr(e)
            )


            try:

                bot.edit_message_text(
                    f"Ошибка редактирования:\n{e}",
                    message.chat.id,
                    status_msg.message_id
                )

            except Exception:

                bot.send_message(
                    message.chat.id,
                    f"Ошибка редактирования:\n{e}"
                )


        # ВАЖНО:
        # после /edit больше ничего не делаем
        return


    # =====================================================
    # ДАЛЬШЕ ИДЁТ ОБЫЧНАЯ ЛОГИКА БОТА
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
    )

    text = text.strip()


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

            chat_history.append(
                text
            )


            if len(chat_history) > HISTORY_LIMIT:

                chat_history.pop(0)


    # =====================================================
    # КОНТЕКСТ
    # =====================================================

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
                message.reply_to_message.from_user.id
                == bot_id
            )

        except Exception:

            is_reply = False


        if not (
            is_mentioned
            or
            is_reply
        ):

            return


    # =====================================================
    # GEMINI
    # =====================================================

    bot.send_chat_action(
        chat_id,
        "typing"
    )


    try:

        history_text = "\n".join(
            dialog_context[chat_id]
        )


        prompt = (
            f"Вот контекст диалога:\n"
            f"{history_text}\n\n"
            f"Ответь на последнее сообщение "
            f"пользователя {user_name}."
        )


        gemini_contents = [
            prompt
        ]


        # =================================================
        # ФОТО
        # =================================================

        if message.photo:

            file_id = (
                message
                .photo[-1]
                .file_id
            )


            file_info = bot.get_file(
                file_id
            )


            downloaded_file = bot.download_file(
                file_info.file_path
            )


            image = Image.open(
                io.BytesIO(
                    downloaded_file
                )
            )


            gemini_contents.append(
                image
            )


        # =================================================
        # ГОЛОС / АУДИО
        # =================================================

        elif (
            message.voice
            or
            message.audio
        ):

            file_id = (
                message.voice.file_id
                if message.voice
                else message.audio.file_id
            )


            file_info = bot.get_file(
                file_id
            )


            downloaded_file = bot.download_file(
                file_info.file_path
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


        # =================================================
        # GEMINI RESPONSE
        # =================================================

        response = model.generate_content(
            gemini_contents
        )


        clean_reply = (
            response.text
            .replace("*", "")
            .strip()
        )


        bot.reply_to(
            message,
            clean_reply
        )


        # Сохраняем ответ бота
        dialog_context[chat_id].append(
            f"читом бот: {clean_reply}"
        )


        if (
            len(dialog_context[chat_id])
            > CONTEXT_LIMIT
        ):

            dialog_context[chat_id].pop(0)


    except Exception as e:

        print(
            "Ошибка Gemini:",
            repr(e)
        )


        bot.reply_to(
            message,
            "Ой, мой клубок запутался "
            f"при обработке файла. Ошибка: {e}"
        )


# =========================================================
# HTTP SERVER
# =========================================================

class DummyHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.end_headers()

        self.wfile.write(
            b"Bot with memory stream is running!"
        )


    def log_message(
        self,
        format,
        *args
    ):
        # Отключаем лишний HTTP-лог
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


# =========================================================
# ЗАПУСК
# =========================================================

threading.Thread(
    target=run_dummy_server,
    daemon=True
).start()


if __name__ == "__main__":

    print(
        "Читом бот запущен..."
    )

    bot.infinity_polling()
