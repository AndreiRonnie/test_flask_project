import os
import json
import logging
import time
import threading
from flask import Flask, request, jsonify
import requests

# Попробуем импортировать openai в блоке try/except, чтобы отловить возможные проблемы
try:
    import openai
except ImportError as e:
    print(f"Не удалось импортировать openai: {e}")
    openai = None  # Можем установить в None, чтобы не ломать дальнейший код

# ------------------------------------------------------
# Настройка прокси (раскомментируйте/уберите при необходимости)
# ------------------------------------------------------
proxy_host = "213.225.237.177"
proxy_port = "9239"
proxy_user = "user27099"
proxy_pass = "qf08ja"

proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
os.environ['http_proxy'] = proxy_url
os.environ['https_proxy'] = proxy_url

# ------------------------------------------------------
# Логирование в файл (DEBUG)
# ------------------------------------------------------
BASE_DIR = os.getcwd()  # Рабочая директория (uWSGI обычно запускается из /home/uXXXX/...)
LOGFILE_PATH = os.path.join(BASE_DIR, 'bot2.log')

logging.basicConfig(
    filename=LOGFILE_PATH,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# ------------------------------------------------------
# Считываем OpenAI API Key из файла (если есть)
# ------------------------------------------------------
KEY_FILE_PATH = os.path.join(BASE_DIR, 'openai_key.txt')
if os.path.exists(KEY_FILE_PATH):
    try:
        with open(KEY_FILE_PATH, 'r', encoding='utf-8') as f:
            OPENAI_API_KEY = f.read().strip()
        logging.info(f"OpenAI ключ считан из файла {KEY_FILE_PATH}")
    except Exception as e:
        logging.error(f"Ошибка при чтении ключа из {KEY_FILE_PATH}: {e}")
        OPENAI_API_KEY = ""
else:
    logging.warning(f"Файл ключа {KEY_FILE_PATH} не найден. Будет использоваться заглушка.")
    OPENAI_API_KEY = ""

# Если openai импортирован, присваиваем ключ (или None)
if openai:
    openai.api_key = OPENAI_API_KEY if OPENAI_API_KEY else None
else:
    print("Внимание! Модуль openai не импортирован. ChatGPT-запросы работать не будут.")

# ------------------------------------------------------
# Создаём Flask-приложение
# ------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# ------------------------------------------------------
# Папка для хранения файлов-диалогов
# ------------------------------------------------------
CONVERSATIONS_DIR = os.path.join(BASE_DIR, 'conversations')
if not os.path.exists(CONVERSATIONS_DIR):
    try:
        os.makedirs(CONVERSATIONS_DIR)
        logging.info(f"Создана папка {CONVERSATIONS_DIR}")
    except Exception as e:
        logging.error(f"Ошибка при создании папки {CONVERSATIONS_DIR}: {e}")

# ------------------------------------------------------
# Фоновая задача: каждые N секунд логируем, что бот "жив"
# ------------------------------------------------------
def periodic_logger():
    while True:
        logging.info("Periodic log message: the bot8 is running")
        time.sleep(60)  # каждые 60 секунд

thread = threading.Thread(target=periodic_logger, daemon=True)
thread.start()

# ------------------------------------------------------
# Функции для хранения/загрузки диалога
# ------------------------------------------------------
def get_conversation_file_path(conversation_id) -> str:
    """
    Приводим conversation_id к строке,
    чтобы безопасно вызывать .replace().
    """
    conversation_str = str(conversation_id)
    safe_id = conversation_str.replace('/', '_').replace('\\', '_')
    return os.path.join(CONVERSATIONS_DIR, f"conversation_{safe_id}.json")

def get_default_system_history() -> list:
    """
    Обновлённый текст системного сообщения.
    """
    system_message = {
        "role": "system",
        "content": (
        "Ты — ассистент, который помогает студентам (платно) и старается узнать "
        "детали об их заказе: тип работы (курсовая, диплом, реферат и т.д.), "
        "срок выполнения, методические материалы, предмет (или специальность), "
        "тему работы, проверку на антиплагиат и требуемый процент оригинальности. "
        "Другие детали уточнять не нужно. "
        "Будь вежливым, дружелюбным, отвечай кратко и по делу, задавай вопросы, "
        "чтобы собрать информацию о заказе. "
        "После сбора этой информации о заказе, ты должен оценить стоимость помощи "
        "студенту фразой: «За X рублей отличный автор готов помочь "
        "выполнить вашу работу в указанные сроки и по указанным требованиям. "
        "Что скажете?»\n\n"
        "X — это примерная стоимость, зависящая от типа работы и предмета:\n"
        "- Реферат (гуманитарные) ~ 1500₽\n"
        "- Курсовая (гуманитарные) ~ 3000₽\n"
        "- Контрольная (гуманитарные) ~ 1500₽\n"
        "- Задача (гуманитарные) ~ 1000₽\n"
        "- Отчёт по практике (гуманитарные) ~ 3000₽\n"
        "- Дипломная/ВКР/магистерская (гуманитарные) ~ 10000₽\n\n"
        "По экономическим дисциплинам цена в 1.5 раза выше, "
        "по техническим (точным) — в 2 раза выше.\n\n"
        "Проверка на антиплагиат бывает двух видов: бесплатная (по etxt) и вузовская. "
        "Вузовская проверка сложнее и дороже, так как в ней участвует больше модулей. "
        "Для контрольных работ и задач антиплагиат не нужен, "
        "но важно смотреть сами задачи.\n\n"
        "Если нужно решить что-то онлайн или срок меньше дня, мы помочь не сможем.\n"
        "Реальную стоимость ты должен определить сам, исходя из сложности темы, сроков "
        "и требований к антиплагиату, но она не должна отличаться более чем в 2 раза "
        "относительно рекомендованной минимальной. "
        "Желательно называть «психологически приятную» стоимость, например 990₽ вместо 1000₽."
    )
}
    return [system_message]

def load_history(conversation_id) -> list:
    filepath = get_conversation_file_path(conversation_id)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                history_data = json.load(f)
                logging.debug(f"Загружена история из файла {filepath}: {history_data}")
                return history_data
        except Exception as e:
            logging.error(f"Ошибка при чтении истории {filepath}: {e}")
            return get_default_system_history()
    else:
        logging.debug(f"Файл истории не найден, возвращаем default system history: {filepath}")
        return get_default_system_history()

def save_history(conversation_id, history: list):
    filepath = get_conversation_file_path(conversation_id)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logging.debug(f"История сохранена в файл {filepath}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении истории {filepath}: {e}")

# ------------------------------------------------------
# Функция вызова ChatGPT или "заглушки"
# ------------------------------------------------------
def get_chatgpt_response(user_text: str, conversation_id) -> str:
    """
    1) Загружаем историю
    2) Добавляем сообщение 'user'
    3) Если openai не импортирован или ключ пуст -> заглушка
    4) Иначе реальный запрос к ChatGPT
    5) Сохраняем ответ
    """
    if not openai or not OPENAI_API_KEY:
        logging.warning("OpenAI недоступен (нет ключа?), возвращаем заглушку.")
        return f"(Заглушка) Вы написали: {user_text}"

    try:
        history = load_history(conversation_id)
        history.append({"role": "user", "content": user_text})
        logging.info(f"[get_chatgpt_response] Добавлено сообщение пользователя: {user_text}")

        # Здесь указываем модель (gpt-4o-mini и т.д.):
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=history,
            temperature=0.7,
        )
        assistant_answer = response["choices"][0]["message"]["content"]
        logging.debug(f"[get_chatgpt_response] Ответ ChatGPT: {assistant_answer}")

        # Добавляем ответ ассистента
        history.append({"role": "assistant", "content": assistant_answer})
        save_history(conversation_id, history)

        return assistant_answer

    except Exception as e:
        logging.error(f"[get_chatgpt_response] Ошибка при запросе к ChatGPT: {e}")
        return "Извините, произошла ошибка при запросе к ИИ."

# ------------------------------------------------------
# Маршрут для приёма вебхуков от Talk-Me (POST)
# ------------------------------------------------------
@app.route('/talkme_webhook', methods=['POST'])
def talkme_webhook():
    try:
        data = request.get_json(force=True)
        logging.info(f"[talkme_webhook] Получен JSON: {json.dumps(data, ensure_ascii=False)}")
    except Exception as e:
        logging.error(f"Невозможно считать JSON: {e}")
        return jsonify({"error": "Bad JSON"}), 400

    # Из JSON берем searchId или token
    search_id = data.get("client", {}).get("searchId")
    if not search_id:
        search_id = data.get("token", "unknown")
    else:
        # Если searchId был числом, делаем строку
        search_id = str(search_id)

    incoming_text = data.get("message", {}).get("text", "")
    talkme_token = data.get("token", "")

    logging.info(f"[talkme_webhook] searchId={search_id}, text={incoming_text}")

    # Формируем ответ через get_chatgpt_response
    reply_text = get_chatgpt_response(incoming_text, search_id)
    logging.info(f"[talkme_webhook] Ответ для Talk-Me: {reply_text}")

    # Отправляем ответ обратно в Talk-Me
    url = "https://lcab.talk-me.ru/json/v1.0/customBot/send"
    body = {"content": {"text": reply_text}}
    headers = {
        "X-Token": talkme_token,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(url, json=body, headers=headers)
        logging.info(f"[talkme_webhook] Ответ Talk-Me: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"[talkme_webhook] Ошибка при отправке ответа: {e}")

    return jsonify({"status": "ok"}), 200

# ------------------------------------------------------
# Корневой маршрут
# ------------------------------------------------------
@app.route('/', methods=['GET'])
def index():
    logging.debug("[index] Вызван корневой маршрут /")
    return "Bot with ChatGPT (с ключом в файле) is running", 200

# ------------------------------------------------------
# Локальный запуск
# ------------------------------------------------------
if __name__ == '__main__':
    logging.info("Запуск Flask-приложения...")
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        logging.error(f"Ошибка при запуске приложения: {e}")

