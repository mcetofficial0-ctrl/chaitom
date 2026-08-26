import telebot
import os
import threading
import random
import time
import io
import json
import re
import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)

TEMPLATE_NAME = 'template.jpg'
FONT_NAME = 'arial.ttf'       
RESULT_NAME = 'meme_result.jpg'

# Обязательно замените на юзернейм вашего бота
BOT_USERNAME = '@chaitom_bot' 

chat_history = []
HISTORY_LIMIT = 1000

dialog_context = {}
CONTEXT_LIMIT = 15

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
]

SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот". 
Твой характер: ты ироничный, абсурдный и саркастичный шутник. Твой юмор абстрактный и сюрреалистичный, но ты не скатываешься в бессмысленный бред — всегда сохраняй нить беседы. Если используешь мат, то редко и исключительно для комедийного акцента. ТЫ УМЕЕШЬ ВИДЕТЬ КАРТИНКИ И РЕДАКТИРОВАТЬ ИХ. Описывай то, что видишь, через призму своего безумного характера.

ПРАВИЛА ТВОЕЙ ВСЕЛЕННОЙ И ЛОР:
1. Периодически вплетай в диалог слова: "читом" (это искаженное название чипсов "читос"), "клубок" (это философская вещь, которую по правилам всегда сосут) и "бастурма" (это блюдо, которое абсолютно всегда подают самым первым в любом меню).
2. В твоей памяти живут люди, которых ты иногда упоминаешь в байках:
   - Степан Клитор — депрессивный музыкант с суицидальными мыслями.
   - Андрей Визард — человек, чья душа и тело требуют только бургеров.
   - Роман Линкин — суровый взрослый лоб, фанатеющий от My Little Pony.

ВАЖНО: Ни в коем случае не используй звездочки (*) и форматирование текста! Пиши строго обычным текстом."""

model = genai.GenerativeModel('gemini-3.6-flash', system_instruction=SYSTEM_PROMPT, safety_settings=safety_settings)

def draw_text_with_outline(draw, text, position, font, text_color="white", outline_color="black"):
    x, y = position
    for adj in range(-2, 3):
        draw.text((x+adj, y), text, font=font, fill=outline_color)
        draw.text((x, y+adj), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=text_color)

def parse_edit_prompt(user_prompt):
    """
    Разбирает:
    /edit замени надпись root beer на chitom
    """
    prompt = user_prompt.strip()

    match = re.match(
        r'^(?:замени|поменяй|заменить)\s+'
        r'(?:надпись\s+)?(.+?)\s+на\s+(.+?)$',
        prompt,
        re.IGNORECASE
    )

    if match:
        return match.group(1).strip(), match.group(2).strip()

    return None, None


def find_text_bbox(image, old_text):
    """Gemini определяет координаты старой надписи."""
    width, height = image.size

    prompt = f"""
Найди на этой картинке текст "{old_text}".

Верни ТОЛЬКО JSON без markdown:
{{"x1": 0, "y1": 0, "x2": 100, "y2": 100}}

Размер картинки: {width}x{height}.
Координаты должны быть в пикселях исходной картинки.
Прямоугольник должен полностью покрывать старую надпись.
"""

    response = model.generate_content([prompt, image])
    raw = response.text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    match = re.search(r'\{.*?\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"Gemini не вернул координаты: {raw}")

    data = json.loads(match.group(0))

    bbox = [
        int(data["x1"]),
        int(data["y1"]),
        int(data["x2"]),
        int(data["y2"])
    ]

    bbox[0] = max(0, min(bbox[0], width - 1))
    bbox[1] = max(0, min(bbox[1], height - 1))
    bbox[2] = max(0, min(bbox[2], width))
    bbox[3] = max(0, min(bbox[3], height))

    return bbox


def edit_user_image(image_bytes, user_prompt):
    """
    Удаляет старую надпись и рисует новую.
    Возвращает BytesIO с ГОТОВОЙ КАРТИНКОЙ.
    """
    try:
        old_text, new_text = parse_edit_prompt(user_prompt)

        if not old_text or not new_text:
            raise ValueError(
                "Формат команды: /edit замени надпись СТАРАЯ на НОВАЯ"
            )

        print("EDIT OLD:", old_text)
        print("EDIT NEW:", new_text)

        # Открываем оригинал
        pil_img = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        # Gemini находит старую надпись
        bbox = find_text_bbox(
            pil_img,
            old_text
        )

        print("EDIT BBOX:", bbox)

        # PIL -> OpenCV
        cv_img = np.array(pil_img)
        cv_img = cv2.cvtColor(
            cv_img,
            cv2.COLOR_RGB2BGR
        )

        h, w = cv_img.shape[:2]
        x1, y1, x2, y2 = bbox

        # Небольшой запас вокруг старого текста
        pad_x = max(5, int((x2 - x1) * 0.10))
        pad_y = max(5, int((y2 - y1) * 0.20))

        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(w, x2 + pad_x)
        y2 = min(h, y2 + pad_y)

        mask = np.zeros(
            (h, w),
            dtype=np.uint8
        )

        mask[y1:y2, x1:x2] = 255

        # Убираем старую надпись
        cv_img = cv2.inpaint(
            cv_img,
            mask,
            7,
            cv2.INPAINT_TELEA
        )

        # OpenCV -> PIL
        cv_img = cv2.cvtColor(
            cv_img,
            cv2.COLOR_BGR2RGB
        )

        result = Image.fromarray(cv_img)
        draw = ImageDraw.Draw(result)

        # Подбираем шрифт под старую область
        area_width = x2 - x1
        area_height = y2 - y1

        font_size = max(
            12,
            int(area_height * 0.75)
        )

        while font_size >= 10:
            try:
                font = ImageFont.truetype(
                    FONT_NAME,
                    font_size
                )

                tb = draw.textbbox(
                    (0, 0),
                    new_text.upper(),
                    font=font
                )

                text_width = tb[2] - tb[0]

                if text_width <= area_width * 0.95:
                    break

                font_size -= 1

            except Exception:
                font = ImageFont.load_default()
                break

        # Центрируем новую надпись
        tb = draw.textbbox(
            (0, 0),
            new_text.upper(),
            font=font
        )

        text_width = tb[2] - tb[0]
        text_height = tb[3] - tb[1]

        text_x = x1 + (area_width - text_width) / 2
        text_y = y1 + (area_height - text_height) / 2

        draw_text_with_outline(
            draw,
            new_text.upper(),
            (text_x, text_y),
            font,
            text_color="white",
            outline_color="black"
        )

        # Возвращаем ИМЕННО картинку
        output = io.BytesIO()

        result.save(
            output,
            format="JPEG",
            quality=95
        )

        output.seek(0)
        output.name = "edited.jpg"

        return output

    except Exception as e:
        print("EDIT IMAGE ERROR:", repr(e))
        return None

def text_wrap(text, font, max_width):
    lines = []
    if font.getlength(text) <= max_width:
        lines.append(text)
    else:
        words = text.split(' ')
        i = 0
        while i < len(words):
            line = ''
            while i < len(words) and font.getlength(line + words[i]) <= max_width:
                line = line + words[i] + ' '
                i += 1
            if not line:
                line = words[i]
                i += 1
            lines.append(line.strip())
    return lines

def generate_meme_image(top_text, bottom_text):
    try:
        if not os.path.exists(TEMPLATE_NAME):
            return "ОШИБКА: Не найден файл картинки 'template.jpg'"
        if not os.path.exists(FONT_NAME):
            return "ОШИБКА: Не найден файл шрифта 'arial.ttf'"

        img = Image.open(TEMPLATE_NAME)
        draw = ImageDraw.Draw(img)
        font_top = ImageFont.truetype(FONT_NAME, 40)
        font_bottom = ImageFont.truetype(FONT_NAME, 50)
        width, height = img.size
        max_txt_width = width * 0.9 

        lines_top = text_wrap(top_text, font_top, max_txt_width)
        current_h = 20
        for line in lines_top:
            w = font_top.getlength(line)
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_top)
            current_h += font_top.getbbox(line)[3] + 5

        lines_bottom = text_wrap(bottom_text, font_bottom, max_txt_width)
        current_h = height * 0.7 
        for line in lines_bottom:
            w = font_bottom.getlength(line)
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_bottom)
            current_h += font_bottom.getbbox(line)[3] + 5

        img.save(RESULT_NAME)
        return RESULT_NAME
    except Exception as e:
        return f"ОШИБКА PILLOW: {e}"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Я на связи! /make_meme — сделать мем, /edit [текст] — изменить картинку.")

@bot.message_handler(
    func=lambda m: bool(
        m.text and
        m.text.strip().lower().startswith("/edit")
    ),
    content_types=["text"]
)
def edit_command(message):
    print("========== EDIT HANDLER ==========")
    print("EDIT TEXT:", repr(message.text))

    # Команда должна быть reply на фото
    target_message = message.reply_to_message

    if not target_message or not target_message.photo:
        bot.reply_to(
            message,
            "Сделай reply именно на картинку.\n"
            "Например:\n"
            "/edit замени надпись root beer на chitom"
        )
        return

    user_prompt = message.text.strip()

    # Убираем /edit и возможный @username
    user_prompt = re.sub(
        r"^/edit(?:@\w+)?\s*",
        "",
        user_prompt,
        count=1,
        flags=re.IGNORECASE
    ).strip()

    if not user_prompt:
        bot.reply_to(
            message,
            "Напиши, что изменить.\n"
            "Например:\n"
            "/edit замени надпись root beer на chitom"
        )
        return

    status_msg = bot.reply_to(
        message,
        "Редактирую картинку..."
    )

    try:
        file_id = target_message.photo[-1].file_id
        file_info = bot.get_file(file_id)

        downloaded_file = bot.download_file(
            file_info.file_path
        )

        print(
            "EDIT: downloaded",
            len(downloaded_file),
            "bytes"
        )

        photo_bio = edit_user_image(
            downloaded_file,
            user_prompt
        )

        if photo_bio is None:
            bot.edit_message_text(
                "Не удалось изменить картинку. "
                "Проверь название старой надписи.",
                message.chat.id,
                status_msg.message_id
            )
            return

        photo_bio.seek(0)

        # ВАЖНО: отправляем только PHOTO.
        bot.send_photo(
            chat_id=message.chat.id,
            photo=photo_bio,
            reply_to_message_id=message.message_id
        )

        print("EDIT: PHOTO SENT SUCCESSFULLY")

        try:
            bot.delete_message(
                message.chat.id,
                status_msg.message_id
            )
        except Exception:
            pass

    except Exception as e:
        print("EDIT SEND ERROR:", repr(e))

        try:
            bot.edit_message_text(
                f"Ошибка обработки картинки:\n{e}",
                message.chat.id,
                status_msg.message_id
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                f"Ошибка обработки картинки:\n{e}"
            )



@bot.message_handler(commands=['make_meme'])
def make_meme_command(message):
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "Эту команду можно использовать только в групповом чате!")
        return

    if len(chat_history) < 2:
        bot.reply_to(message, f"В истории пока маловато фраз ({len(chat_history)}). Пишите еще!")
        return

    status_msg = bot.reply_to(message, "Разматываю клубок истории, сейчас будет мем...")

    try:
        phrases = random.sample(chat_history, 2)
        meme_result = generate_meme_image(phrases[0], phrases[1])

        if meme_result and meme_result.startswith("ОШИБКА"):
            bot.edit_message_text(f"Ой, моя бастурма упала. Причина:\n{meme_result}", message.chat.id, status_msg.message_id)
        elif meme_result:
            with open(meme_result, 'rb') as photo:
                bot.send_photo(message.chat.id, photo, reply_to_message_id=message.message_id)
            os.remove(meme_result)
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
             bot.edit_message_text("Неизвестная ошибка создания мема.", message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"Системная ошибка: {e}", message.chat.id, status_msg.message_id)

@bot.message_handler(content_types=['text', 'photo', 'voice', 'audio'])
def handle_message(message):
    # /edit обрабатывается отдельным обработчиком выше.
    # Никогда не отправляем /edit в обычный Gemini-чат.
    if message.text and message.text.strip().lower().startswith("/edit"):
        return

    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Аноним"
    text = message.text or message.caption or ""
    text = text.strip()

    if message.chat.type in ['group', 'supergroup'] and text and not text.startswith('/'):
        if text not in chat_history:
            chat_history.append(text)
            if len(chat_history) > HISTORY_LIMIT:
                chat_history.pop(0)

    if chat_id not in dialog_context:
        dialog_context[chat_id] = []
    
    log_text = text if text else "[Медиафайл]"
    dialog_context[chat_id].append(f"{user_name}: {log_text}")
    if len(dialog_context[chat_id]) > CONTEXT_LIMIT:
        dialog_context[chat_id].pop(0)

    if message.chat.type in ['group', 'supergroup']:
        is_mentioned = text and BOT_USERNAME in text
        is_reply = message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id
        if not (is_mentioned or is_reply):
            return

    bot.send_chat_action(chat_id, 'typing')

    try:
        history_text = "\n".join(dialog_context[chat_id])
        prompt = (
            f"Вот контекст диалога:\n{history_text}\n\n"
            f"Ответь на последнее сообщение пользователя {user_name}."
        )

        gemini_contents = [prompt]

        if message.photo:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            image = Image.open(io.BytesIO(downloaded_file))
            gemini_contents.append(image)

        elif message.voice or message.audio:
            file_id = message.voice.file_id if message.voice else message.audio.file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            mime_type = "audio/ogg" if message.voice else "audio/mpeg"
            gemini_contents.append({"mime_type": mime_type, "data": downloaded_file})

        response = model.generate_content(gemini_contents)
        clean_reply = response.text.replace('*', '')
        bot.reply_to(message, clean_reply)
        
        dialog_context[chat_id].append(f"читом бот: {clean_reply}")
        if len(dialog_context[chat_id]) > CONTEXT_LIMIT:
            dialog_context[chat_id].pop(0)
            
    except Exception as e:
        print(f"Ошибка Gemini: {e}")
        bot.reply_to(message, f"Ой, мой клубок запутался при обработке файла. Ошибка: {e}")

# ==========================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running!')

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == '__main__':
    print("Читом бот запущен...")
    bot.infinity_polling()
