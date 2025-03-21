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

# Настройки приложения
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Конфигурация таблиц пользователей
USERS_LIMITED = os.getenv("LIMITED_USERS_SHEET")
USERS_UNLIMITED = os.getenv("UNLIMITED_USERS_SHEET")

# OAuth настройки
CLIENT_SECRETS_FILE = "client_secrets.json"
SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.file",
    "openid"  # Добавлен scope openid
]

def get_google_credentials():
    """Получение учетных данных Google из сессии."""
    credentials_json = session.get("credentials")
    if not credentials_json:
        raise Exception("No credentials found in session. User must log in.")

    credentials = Credentials.from_authorized_user_info(credentials_json, SCOPES)

    # Проверка и обновление токена, если он просрочен
    if credentials.expired and credentials.refresh_token:
        logging.info("Refreshing credentials...")
        try:
            credentials.refresh(Request())
            session["credentials"] = json.loads(credentials.to_json())
            logging.info("Credentials refreshed successfully.")
        except Exception as e:
            logging.error(f"Error refreshing credentials: {e}")
            flash("Error refreshing credentials. Please log in again.")
            return None  # Или другое действие по обработке ошибки

    return credentials

def check_user_access(user_email):
    """Проверка доступа пользователя (лимитный/безлимитный)"""
    try:
        credentials = get_google_credentials()
        if not credentials:  # Обработка случая, когда не удалось получить учетные данные
            return {"error": "Could not retrieve Google credentials."}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Проверка безлимитных пользователей
        unlimited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_UNLIMITED,
            range="A:A"
        ).execute().get("values", [])

        if any(user_email == row[0] for row in unlimited_users):
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
                        return {"error": "Превышен лимит использования."}
                except ValueError:
                    logging.warning(f"Неверный формат даты в строке: {row}")
                    return {"error": "Неверный формат даты."}
                return {"access": "limited"}

        return {"error": "Не авторизован."}

    except HttpError as e:
        logging.error(f"Ошибка Google Sheets API: {e}")
        return {"error": str(e)}
    except Exception as e:
        logging.error(f"Ошибка при проверке доступа: {e}")
        return {"error": "Произошла ошибка при проверке доступа."}

def update_last_used(user_email):
    """Обновление времени последнего использования для лимитных пользователей"""
    try:
        credentials = get_google_credentials()
        if not credentials:  # Обработка случая, когда не удалось получить учетные данные
            return

        sheets_service = build("sheets", "v4", credentials=credentials)
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
                break
    except HttpError as e:
        logging.error(f"Ошибка Google Sheets API: {e}")
    except Exception as e:
        logging.error(f"Ошибка при обновлении времени последнего использования: {e}")

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
    except FileNotFoundError:
        logging.error(f"client_secrets.json not found. Ensure it is in the same directory as the script.")
        flash("client_secrets.json not found. Please check your deployment.")
        return redirect(url_for("home"))
    except Exception as e:
        logging.error(f"Error during login: {e}")
        flash("Произошла ошибка во время входа.")
        return redirect(url_for("home"))

@app.route("/callback")
def callback():
    try:
        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = url_for("callback", _external=True)
        flow.fetch_token(authorization_response=request.url)
        session["credentials"] = json.loads(flow.credentials.to_json())

        # Получаем информацию о пользователе и сохраняем email в сессии
        oauth2_service = build("oauth2", "v2", credentials=get_google_credentials())
        user_info = oauth2_service.userinfo().get().execute()
        session["user_email"] = user_info["email"]
        logging.info(f"User {session['user_email']} logged in successfully.")

        return redirect(url_for("home"))
    except FileNotFoundError:
        logging.error(f"client_secrets.json not found. Ensure it is in the same directory as the script.")
        flash("client_secrets.json not found. Please check your deployment.")
        return redirect(url_for("home"))
    except Exception as e:
        logging.error(f"Error during callback: {e}")
        flash("Произошла ошибка во время обратного вызова.")
        return redirect(url_for("home"))

@app.route("/create_form", methods=["POST"])
def create_form():
    try:
        # Получение информации о пользователе
        user_email = session.get("user_email")
        if not user_email:
            flash("Пожалуйста, войдите в систему, чтобы создать форму.")
            return redirect(url_for("login"))

        # Проверка доступа пользователя
        access_check = check_user_access(user_email)
        if "error" in access_check:
            flash(access_check["error"])
            return redirect(url_for("home"))

        # Получение учетных данных пользователя
        credentials = get_google_credentials()
        if not credentials:
            flash("Не удалось получить учетные данные Google. Пожалуйста, войдите в систему еще раз.")
            return redirect(url_for("login"))

        # Обработка ссылки на таблицу
        spreadsheet_url = request.form.get("spreadsheet_url")
        if not spreadsheet_url:
            flash("Неверная ссылка на таблицу!")
            return redirect(url_for("home"))

        sheet_id_match = re.search(r"/d/([a-zA-Z0-9-_]+)", spreadsheet_url)
        if not sheet_id_match:
            flash("Неверная ссылка на таблицу!")
            return redirect(url_for("home"))

        sheet_id = sheet_id_match.group(1)

        # Чтение данных из таблицы
        sheets_service = build("sheets", "v4", credentials=credentials)
        sheet_data = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range="A:Z"
        ).execute().get("values", [])

        if not sheet_data:
            flash("Таблица пуста!")
            return redirect(url_for("home"))

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
            
            # Создаем вопрос без указания правильного ответа
            question_item = {
                "title": question_text,
                "questionItem": {
                    "question": {
                        "required": True,
                        "choiceQuestion": {
                            "type": "RADIO",
                            "options": answers,
                            "shuffle": True
                        }
                    }
                }
            }
            
            form_data["items"].append(question_item)

        # Создаем форму
        form_response = form_service.forms().create(body=form_data).execute()
        form_id = form_response.get("formId")
        
        # Обновление времени последнего использования для лимитных пользователей
        if access_check["access"] == "limited":
            update_last_used(user_email)

        # Если нужно превратить в тест с оценкой, потребуется дополнительный API вызов
        # Например:
        # Следующий код, который нужно реализовать, если необходимо установить правильные ответы:
        # 1. Сначала создаем форму (уже сделано выше)
        # 2. Конвертируем её в тест
        # 3. Для каждого вопроса устанавливаем правильный ответ

        flash(f"Форма успешно создана: {form_response.get('responderUri')}")
        return redirect(form_response.get("responderUri"))

    except Exception as e:
        logging.error(f"Ошибка при создании формы: {e}")
        flash(f"Ошибка: {e}")
        return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)