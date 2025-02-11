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
# 1) Настройка прокси (раскомментируйте, если действительно нужно)
# ------------------------------------------------------
proxy_host = "213.225.237.177"
proxy_port = "9239"
proxy_user = "user27099"
proxy_pass = "qf08ja"

proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
os.environ['http_proxy'] = proxy_url
os.environ['https_proxy'] = proxy_url

# ------------------------------------------------------
# 2) Настройка OpenAI API
# ------------------------------------------------------
OPENAI_API_KEY = ""  # Здесь лучше оставить пустым, если временно хотите проверить без реального ключа

if openai:
    openai.api_key = OPENAI_API_KEY
else:
    print("Внимание! Модуль openai не импортирован. ChatGPT-запросы работать не будут.")

# ------------------------------------------------------
# 3) Логирование в файл с уровнем DEBUG
# ------------------------------------------------------
BASE_DIR = os.getcwd()  # вместо os.path.dirname(os.path.abspath(__file__))
LOGFILE_PATH = os.path.join(BASE_DIR, 'bot2.log')

logging.basicConfig(
    filename=LOGFILE_PATH,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# ------------------------------------------------------
# Создаем Flask-приложение
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
# Фоновая задача: каждые 10 секунд записывать в лог, 
# чтобы было видно, что бот "жив"
# ------------------------------------------------------
def periodic_logger():
    while True:
        logging.info("Periodic log message: the bot4 is running")
        time.sleep(10)

thread = threading.Thread(target=periodic_logger, daemon=True)
thread.start()

# ------------------------------------------------------
# Функции для хранения/загрузки диалога
# (не меняются, но при желании можно добавить логов)
# ------------------------------------------------------
def get_conversation_file_path(conversation_id: str) -> str:
    safe_id = conversation_id.replace('/', '_').replace('\\', '_')
    return os.path.join(CONVERSATIONS_DIR, f"conversation_{safe_id}.json")

def get_default_system_history() -> list:
    system_message = {
        "role": "system",
        "content": (
            "Ты — ассистент, который помогает студентам (платно) и старается узнать "
            "все детали об их заказе: тип работы (курсовая, диплом, реферат и т.д.), "
            "срок выполнения, методические материалы, предмет (или специальность), "
            "тему работы, проверку на антиплагиат и требуемый процент оригинальности. "
            "Будь вежливым, дружелюбным, отвечай кратко и по делу, при этом старайся "
            "задавать уточняющие вопросы, чтобы собрать полную информацию о заказе."
        )
    }
    return [system_message]

def load_history(conversation_id: str) -> list:
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

def save_history(conversation_id: str, history: list):
    filepath = get_conversation_file_path(conversation_id)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logging.debug(f"История сохранена в файл {filepath}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении истории {filepath}: {e}")

# ------------------------------------------------------
# Функция для вызова ChatGPT (или заглушки), учитывая историю
# ------------------------------------------------------
def get_chatgpt_response(user_text: str, conversation_id: str) -> str:
    """
    1) Загружаем текущую историю
    2) Добавляем новое сообщение 'user'
    3) Если openai не импортирован или ключ пуст, вернуть заглушку
    4) Иначе отправить запрос к ChatCompletion
    5) Сохранить ответ
    """
    # Если openai не импортирован или ключ не установлен, вернем «заглушку»
    if not openai or not OPENAI_API_KEY:
        logging.warning("OpenAI не доступен, возвращаем заглушку.")
        return f"(Заглушка) Вы написали: {user_text}"

    try:
        conversation_history = load_history(conversation_id)
        conversation_history.append({"role": "user", "content": user_text})
        logging.info(f"[get_chatgpt_response] Добавлено сообщение пользователя: {user_text}")

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=conversation_history,
            temperature=0.7,
        )
        assistant_answer = response["choices"][0]["message"]["content"]
        logging.debug(f"[get_chatgpt_response] Ответ ChatGPT: {assistant_answer}")

        # Добавляем ответ ассистента в историю
        conversation_history.append({"role": "assistant", "content": assistant_answer})
        save_history(conversation_id, conversation_history)

        return assistant_answer

    except Exception as e:
        logging.error(f"[get_chatgpt_response] Ошибка при запросе к ChatGPT: {e}")
        return "Извините, произошла ошибка при запросе к ИИ."

# ------------------------------------------------------
# Маршрут для приёма вебхуков от Talk-Me (POST)
# ------------------------------------------------------
@app.route('/talkme_webhook', methods=['POST'])
def talkme_webhook():
    """Обработчик входящих сообщений от Talk-Me."""
    try:
        data = request.get_json(force=True)
        logging.info(f"[talkme_webhook] Получен JSON: {json.dumps(data, ensure_ascii=False)}")
    except Exception as e:
        logging.error(f"Невозможно считать JSON: {e}")
        return jsonify({"error": "Bad JSON"}), 400

    # Из JSON берем searchId (если он не пуст), иначе fallback — token или 'unknown'
    search_id = data.get("client", {}).get("searchId")
    if not search_id:
        search_id = data.get("token", "unknown")

    # Текст сообщения пользователя
    incoming_text = data.get("message", {}).get("text", "")
    # Talk-Me передаёт свой токен
    talkme_token = data.get("token", "")

    logging.info(f"[talkme_webhook] searchId={search_id}, text={incoming_text}")

    # Вызываем функцию ответа (ChatGPT или заглушка)
    reply_text = get_chatgpt_response(incoming_text, search_id)

    # Логируем, какой ответ сформирован
    logging.info(f"[talkme_webhook] Формируем ответ для Talk-Me: {reply_text}")

    # Отправляем обратно в Talk-Me
    url = "https://lcab.talk-me.ru/json/v1.0/customBot/send"
    body = {
        "content": {
            "text": reply_text
        }
    }
    headers = {
        "X-Token": talkme_token,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=body, headers=headers)
        logging.info(f"[talkme_webhook] Ответ Talk-Me: {response.status_code} {response.text}")
    except Exception as e:
        logging.error(f"[talkme_webhook] Ошибка при отправке ответа в Talk-Me: {e}")

    return jsonify({"status": "ok"}), 200

# ------------------------------------------------------
# Маршрут проверки: GET / 
# ------------------------------------------------------
@app.route('/', methods=['GET'])
def index():
    logging.debug("[index] Вызван корневой маршрут /")
    return "Bot with ChatGPT (с памятью) is running", 200

# ------------------------------------------------------
# Локальный запуск (при разработке)
# ------------------------------------------------------
if __name__ == '__main__':
    logging.info("Запуск Flask-приложения...")
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        logging.error(f"Ошибка при запуске приложения: {e}")

