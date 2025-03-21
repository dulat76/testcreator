from flask import Flask, redirect, url_for, session, request, render_template, jsonify
from flask_session import Session
import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
import re

app = Flask(__name__)
app.secret_key = "super_secret_key"
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Файл конфигурации OAuth
CLIENT_SECRETS_FILE = "credentials.json"

# Области доступа OAuth
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid"
]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login():
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, SCOPES)
    flow.redirect_uri = url_for("callback", _external=True)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true"
    )

    session["state"] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, SCOPES, state=session["state"]
    )
    flow.redirect_uri = url_for("callback", _external=True)

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)

    session["credentials"] = credentials_to_dict(flow.credentials)
    return redirect(url_for("dashboard"))

def credentials_to_dict(credentials):
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes
    }

@app.route("/dashboard")
def dashboard():
    if "credentials" not in session:
        return redirect(url_for("login"))
    return render_template("dashboard.html")

def extract_spreadsheet_id(sheet_url):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", sheet_url)
    return match.group(1) if match else None

@app.route("/create_test", methods=["POST"])
def create_test():
    if "credentials" not in session:
        return jsonify({"error": "Not logged in"}), 403

    sheet_url = request.form.get("sheet_url")
    if not sheet_url:
        return jsonify({"error": "Введите ссылку на Google Таблицу"}), 400

    spreadsheet_id = extract_spreadsheet_id(sheet_url)
    if not spreadsheet_id:
        return jsonify({"error": "Некорректная ссылка на Google Таблицу"}), 400

    credentials = google.oauth2.credentials.Credentials(**session["credentials"])

    # Подключение к Google Sheets API
    sheets_service = googleapiclient.discovery.build("sheets", "v4", credentials=credentials)
    sheet = sheets_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range="A:Z").execute()
    data = sheet.get("values", [])

    if not data:
        return jsonify({"error": "Нет данных в таблице"}), 400

    # Подключение к Google Forms API
    forms_service = googleapiclient.discovery.build("forms", "v1", credentials=credentials)

    # Создание пустой формы
    form = {"info": {"title": "Тест из Google Таблицы"}}
    form = forms_service.forms().create(body=form).execute()
    form_id = form["formId"]

    requests = []

    for row in data:
        if len(row) < 2:
            continue  # Пропускаем строки без данных

        question = row[0]
        options = row[1:]
        correct_answer = None

        # Определение правильного ответа
        for i, option in enumerate(options):
            if option.startswith("*"):
                correct_answer = option[1:].strip()  # Убираем *
                options[i] = correct_answer  # Убираем * в вариантах

        question_request = {
            "createItem": {
                "item": {
                    "title": question,
                    "questionItem": {
                        "question": {
                            "required": True,
                            "choiceQuestion": {
                                "type": "RADIO",
                                "options": [{"value": opt.strip()} for opt in options],
                                "shuffle": False
                            }
                        }
                    }
                },
                "location": {"index": 0}
            }
        }
        requests.append(question_request)

        # Если есть правильный ответ, добавляем ключ
        if correct_answer:
            answer_key_request = {
                "updateItem": {
                    "item": {
                        "title": question,
                        "questionItem": {
                            "question": {
                                "grading": {
                                    "correctAnswers": {
                                        "answers": [{"value": correct_answer}]
                                    }
                                }
                            }
                        }
                    }
                },
                "updateMask": "questionItem.question.grading"
            }
            requests.append(answer_key_request)

    # Отправка вопросов в Google Форму
    forms_service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()

    return jsonify({"message": "Тест создан", "form_id": form_id, "form_link": f"https://forms.google.com/{form_id}"})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
