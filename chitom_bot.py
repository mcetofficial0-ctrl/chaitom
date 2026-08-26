import telebot
import os
import threading
import random
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from groq import Groq
# Библиотеки для работы с картинками (из пакета Pillow)
from PIL import Image, ImageDraw, ImageFont, ImageFile

# Позволяет Pillow работать с неполными JPG файлами (на всякий случай)
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Ключи и настройки
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

# Настройки имен файлов и путей
TEMPLATE_NAME = 'template.jpg' # Имя файла шаблона в репозитории
FONT_NAME = 'arial.ttf'       # Имя файла шрифта в репозитории
RESULT_NAME = 'meme_result.jpg'
# Замените на реальный юзернейм вашего бота (с собачкой в начале)
BOT_USERNAME = '@твой_юзернейм' 

# Хранилище для истории сообщений (максимум 200 последних фраз)
chat_history = []
HISTORY_LIMIT = 200

# Промпт для Groq (оставляем старый, он отличный!)
SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот". 
Твоя главная и нерушимая задача: в каждом, абсолютно в каждом своем ответе пользователю 
ты обязан использовать ровно три слова: "читом", "бастурма" и "клубок". 
Старайся вписывать их в контекст диалога так, чтобы это звучало забавно или органично.
ВАЖНО: Ни в коем случае не используй звездочки (*) и форматирование текста! Пиши обычным текстом."""

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ МЕМОВ ---

def text_wrap(text, font, max_width):
    """Разбивает длинный текст на строки, чтобы он влезал по ширине."""
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

def draw_text_with_outline(draw, text, position, font, text_color="white", outline_color="black"):
    """Рисует текст с черной обводкой для лучшей читаемости."""
    x, y = position
    # Рисуем обводку (сдвигаем текст на 2 пикселя во все стороны)
    for adj in range(-2, 3):
        draw.text((x+adj, y), text, font=font, fill=outline_color)
        draw.text((x, y+adj), text, font=font, fill=outline_color)
    # Рисуем основной текст сверху
    draw.text((x, y), text, font=font, fill=text_color)

def generate_meme_image(top_text, bottom_text):
    """Загружает шаблон, пишет текст в нужные зоны и сохраняет результат."""
    try:
        # Проверяем, есть ли файлы
        if not os.path.exists(TEMPLATE_NAME) or not os.path.exists(FONT_NAME):
            print("Ошибка: Файлы шаблона или шрифта не найдены!")
            return None

        img = Image.open(TEMPLATE_NAME)
        draw = ImageDraw.Draw(img)
        
        # Настройки шрифтов (разный размер для верха и низа)
        font_top = ImageFont.truetype(FONT_NAME, 40)
        font_bottom = ImageFont.truetype(FONT_NAME, 50)
        
        width, height = img.size
        # Максимальная ширина текста (90% от ширины картинки)
        max_txt_width = width * 0.9 

        # --- Зона 1 (Верх, над головой Муфасы) ---
        lines_top = text_wrap(top_text, font_top, max_txt_width)
        current_h = 20 # Отступ сверху
        for line in lines_top:
            w = font_top.getlength(line)
            # Центрируем строку
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_top)
            # Сдвигаем координаты для следующей строки
            current_h += font_top.getbbox(line)[3] + 5

        # --- Зона 2 (Низ, на третьем фрейме с серьезным Муфасой) ---
        lines_bottom = text_wrap(bottom_text, font_bottom, max_txt_width)
        # Начинаем писать чуть выше нижнего края (примерно 70% высоты)
        current_h = height * 0.7 
        for line in lines_bottom:
            w = font_bottom.getlength(line)
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_bottom)
            current_h += font_bottom.getbbox(line)[3] + 5

        # Сохраняем временный файл
        img.save(RESULT_NAME)
        return RESULT_NAME
    except Exception as e:
        print(f"Ошибка при создании картинки: {e}")
        return None

# --- ОБРАБОТЧИКИ КОМАНД И СООБЩЕНИЙ ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я читом бот. Пиши мне про бастурму или клубок. А командой /make_meme я сделаю мем из ваших фраз!")

@bot.message_handler(commands=['make_meme'])
def make_meme_command(message):
    """Команда для создания мема."""
    # Мемы делаем только в группах
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "Эту команду можно использовать только в групповом чате!")
        return

    # Проверяем, накопилась ли история
    if len(chat_history) < 2:
        bot.reply_to(message, "В истории пока слишком мало сообщений. Пообщайтесь ещё немного, чтобы я набрал материала!")
        return

    # Отправляем сообщение "Бот печатает...", чтобы пользователи ждали
    status_msg = bot.reply_to(message, "Разматываю клубок истории, сейчас будет мем...")

    try:
        # Выбираем две случайные разные фразы из истории
        phrases = random.sample(chat_history, 2)
        top_phrase = phrases[0]
        bottom_phrase = phrases[1]

        # Генерируем картинку
        meme_file = generate_meme_image(top_phrase, bottom_phrase)

        if meme_file:
            # Отправляем готовую картинку
            with open(meme_file, 'rb') as photo:
                bot.send_photo(message.chat.id, photo, reply_to_message_id=message.message_id)
            # Удаляем временный файл, чтобы не занимать место на сервере
            os.remove(meme_file)
        else:
            bot.edit_message_text("Ой, моя бастурма упала. Не удалось создать мем (проверьте логи сервера).", message.chat.id, status_msg.message_id)
            
    except Exception as e:
        bot.edit_message_text(f"Ошибка: {e}", message.chat.id, status_msg.message_id)
    finally:
        # В любом случае удаляем статусное сообщение через пару секунд
        time.sleep(2)
        bot.delete_message(message.chat.id, status_msg.message_id)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Обработка всех остальных сообщений (сбор истории и ответы Groq)."""
    
    # --- БЛОК 1: Сбор истории (для мемов) ---
    # Сохраняем только текстовые сообщения в группах, и не команды
    if message.chat.type in ['group', 'supergroup'] and message.text and not message.text.startswith('/'):
        # Очищаем текст от лишних пробелов и символов
        clean_text = message.text.strip()
        if clean_text and clean_text not in chat_history:
            chat_history.append(clean_text)
            # Если история слишком большая, удаляем старые сообщения
            if len(chat_history) > HISTORY_LIMIT:
                chat_history.pop(0)

    # --- БЛОК 2: Ответы Groq (старая логика) ---
    if message.chat.type in ['group', 'supergroup']:
        is_mentioned = message.text and BOT_USERNAME in message.text
        is_reply = False
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
            is_reply = True
        # Если бота не звали — игнорируем
        if not (is_mentioned or is_reply):
            return

    # Запрос к нейросети
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message.text}
            ],
            # Используем актуальную модель qwen, которую вы нашли!
            model="qwen/qwen3.8-27b", 
        )
        response_text = chat_completion.choices[0].message.content
        clean_reply = response_text.replace('*', '')
        bot.reply_to(message, clean_reply)
    except Exception as e:
        # Не спамим ошибками в чат, просто пишем в логи Render
        print(f"Ошибка Groq: {e}")

# ==========================================
# ЧИТОМ ДЛЯ RENDER: Фейковый веб-сервер
# ==========================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot with Meme maker is running!')

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()
# ==========================================

if __name__ == '__main__':
    print("Читом бот с мемоделом запущен...")
    bot.infinity_polling()
