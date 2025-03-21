import os
import re
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, redirect, request, session, url_for, render_template, flash
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth import default
from google.oauth2 import service_account

# Настройки приложения
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s') # Улучшенное логирование

# Конфигурация таблиц пользователей
USERS_LIMITED = os.getenv("1pLM5IwUV_uj0zLTBx1-5SLtFRtD9PSiW3N6c1-jeuKA")
USERS_UNLIMITED = os.getenv("1IqpytxzUp_ZM40ZypHB31EngMYCV1Ib4RPoZTYjuoWM")

# OAuth настройки (могут быть не нужны, если используете только сервисный аккаунт)
CLIENT_SECRETS_FILE = "client_secrets.json"  # Устаревший метод, рекомендуется сервисный аккаунт
SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email"  # Добавлен scope для получения email
]

# === Учетные данные Google (Сервисный аккаунт) ===
# Используйте ЭТО, если настроили переменную окружения GOOGLE_APPLICATION_CREDENTIALS
def get_google_credentials():
    """Получение учетных данных Google из переменной окружения GOOGLE_APPLICATION_CREDENTIALS."""
    try:
        credentials, project_id = default(scopes=SCOPES)
        logging.info("Учетные данные Google успешно загружены из GOOGLE_APPLICATION_CREDENTIALS.")
        return credentials
    except Exception as e:
        logging.error(f"Ошибка при загрузке учетных данных из GOOGLE_APPLICATION_CREDENTIALS: {e}")
        logging.info("Попытка загрузки учетных данных из файла service_account.json...")
        try:
            # Попытка загрузить учетные данные из файла (альтернативный вариант)
            credentials = service_account.Credentials.from_service_account_file(
                'service_account.json', scopes=SCOPES
            )
            logging.info("Учетные данные Google успешно загружены из service_account.json.")
            return credentials

        except Exception as e2:
            logging.error(f"Ошибка при загрузке учетных данных из service_account.json: {e2}")
            raise # Поднимаем исключение, если не удалось загрузить учетные данные

# Альтернативный способ, если GOOGLE_APPLICATION_CREDENTIALS не работает
# def get_google_credentials():
#     """Получение учетных данных Google из переменной окружения GOOGLE_CREDENTIALS_JSON."""
#     credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
#     if not credentials_json:
#         raise ValueError("Переменная окружения GOOGLE_CREDENTIALS_JSON не найдена")

#     try:
#         credentials_info = json.loads(credentials_json)
#     except json.JSONDecodeError:
#         raise ValueError("Неверный формат JSON в переменной окружения GOOGLE_CREDENTIALS_JSON")

#     credentials = service_account.Credentials.from_service_account_info(
#         credentials_info, scopes=SCOPES
#     )
#     return credentials



def check_user_access(user_email):
    """Проверка доступа пользователя (лимитный/безлимитный)"""
    try:
        sheets_service = build("sheets", "v4", credentials=get_google_credentials())

        # Проверка безлимитных пользователей
        unlimited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_UNLIMITED,
            range="A:A"
        ).execute().get("values", [])

        if any(user_email == row[0] for row in unlimited_users):
            logging.info(f"Пользователь {user_email} имеет безлимитный доступ.")
            return {"access": "unlimited"}

        # Проверка лимитных пользователей
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:C"
        ).execute().get("values", [])

        for row in limited_users:
            if user_email == row[0]:
                try:
                    last_used = datetime.fromisoformat(row[1])
                    if datetime.now() - last_used < timedelta(hours=24):
                        logging.warning(f"Превышен лимит использования для пользователя {user_email}.")
                        return {"error": "Превышен лимит, попробуйте позже"}
                except ValueError:
                    logging.warning(f"Неверный формат даты в строке: {row}")
                    return {"error": "Неверный формат даты"}
                logging.info(f"Пользователь {user_email} имеет лимитный доступ.")
                return {"access": "limited"}

        logging.warning(f"Пользователь {user_email} не авторизован.")
        return {"error": "Не авторизован"}

    except HttpError as e:
        logging.error(f"Ошибка Google Sheets API: {e}")
        return {"error": str(e)}
    except Exception as e:
        logging.error(f"Неожиданная ошибка: {e}")
        return {"error": "Неожиданная ошибка при проверке доступа"}

def update_last_used(user_email):
    """Обновление времени последнего использования для лимитных пользователей"""
    try:
        sheets_service = build("sheets", "v4", credentials=get_google_credentials())
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"
        ).execute().get("values", [])

        for i, row in enumerate(limited_users):
            if row[0] == user_email:
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=USERS_LIMITED,
                    range=f"B{i+2}",
                    valueInputOption="RAW",
                    body={"values": [[datetime.now().isoformat()]]}
                ).execute()
                logging.info(f"Время последнего использования обновлено для пользователя {user_email}.")
                break
    except HttpError as e:
        logging.error(f"Ошибка Google Sheets API: {e}")
    except Exception as e:
         logging.error(f"Неожиданная ошибка при обновлении времени последнего использования: {e}")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/login")
def login():
    try:
        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = url_for("callback", _external=True)
        auth_url, _ = flow.authorization_url(prompt="consent")
        return redirect(auth_url)
    except Exception as e:
        logging.error(f"Ошибка во время входа: {e}")
        return "Произошла ошибка во время входа.", 500

@app.route("/callback")
def callback():
    try:
        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = url_for("callback", _external=True)
        flow.fetch_token(authorization_response=request.url)
        session["credentials"] = json.loads(flow.credentials.to_json())
        return redirect(url_for("index"))
    except Exception as e:
        logging.error(f"Ошибка во время обратного вызова: {e}")
        return "Произошла ошибка во время обратного вызова.", 500

@app.route("/create_form", methods=["POST"])
def create_form():
    try:
        credentials = get_google_credentials()
        # Получение информации о пользователе
        oauth2_service = build("oauth2", "v2", credentials=credentials)
        user_info = oauth2_service.userinfo().get().execute()
        user_email = user_info["email"]

        # Проверка доступа пользователя
        access_check = check_user_access(user_email)
        if "error" in access_check:
            flash(access_check["error"])
            return redirect(url_for("index"))

        # Обработка ссылки на таблицу
        spreadsheet_url = request.form.get("spreadsheet_url")
        if not spreadsheet_url:
            flash("Неверная ссылка на таблицу!")
            return redirect(url_for("index"))

        sheet_id_match = re.search(r"/d/([a-zA-Z0-9-_]+)", spreadsheet_url)
        if not sheet_id_match:
            flash("Неверная ссылка на таблицу!")
            return redirect(url_for("index"))

        sheet_id = sheet_id_match.group(1)

        # Чтение данных из таблицы
        sheets_service = build("sheets", "v4", credentials=credentials)
        sheet_data = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range="A:Z"
        ).execute().get("values", [])

        if not sheet_data:
            flash("Таблица пуста!")
            return redirect(url_for("index"))

        # Создание формы
        form_service = build("forms", "v1", credentials=credentials)
        form_data = {
            "info": {"title": "Автоматический тест"},
            "items": []
        }

        for row in sheet_data:
            if len(row) < 2:
                continue

            question_text = row[0]
            answers = [{"value": a.lstrip("*")} for a in row[1:]]
            correct_answers = [a.lstrip("*") for a in row[1:] if a.startswith("*")]

            form_data["items"].append({
                "title": question_text,
                "questionItem": {
                    "question": {
                        "required": True,
                        "choiceQuestion": {
                            "type": "RADIO",
                            "options": answers,
                            "shuffle": True,
                            **({"correctAnswer": {"value": correct_answers[0]}} if correct_answers else {})
                        }
                    }
                }
            })

        form_response = form_service.forms().create(body=form_data).execute()

        # Обновление времени последнего использования для лимитных пользователей
        if access_check["access"] == "limited":
            update_last_used(user_email)

        return redirect(form_response.get("responderUri"))

    except Exception as e:
        logging.error(f"Ошибка при создании формы: {e}")
        flash(f"Ошибка: {e}")
        return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
