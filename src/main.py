from flask import Flask

app = Flask(__name__)

@app.route('/')
def index():
    return "Hello from test.studentshelper.ru! Flask is running."

if __name__ == '__main__':
    # Локальный запуск: python main.py
    app.run(host='0.0.0.0', port=5000, debug=True)
