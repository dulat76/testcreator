import os
import re
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, redirect, request, session, url_for, render_template, flash
from google_auth_oauthlib momentous import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Настройки приложения
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key")  # Рекомендуется использовать безопасный ключ
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
    "openid"
]

def add_user_to_limited(user_email):
    """Добавление нового пользователя в таблицу лимитных пользователей"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return {"error": "Could not retrieve Google credentials."}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Получаем текущий список пользователей
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"
        ).execute()
        limited_users = result.get("values", [])

        # Проверяем существование пользователя
        if limited_users and any(user_email == row[0] for row in limited_users):
            return {"message": "User already exists."}

        # Добавляем нового пользователя
        new_user_row = [user_email, datetime.now().isoformat()]
        sheets_service.spreadsheets().values().append(
            spreadsheetId=USERS_LIMITED,
            range="A:B",
            valueInputOption="RAW",
            body={"values": [new_user_row]}
        ).execute()

        return {"message": "User added successfully."}

    except HttpError as e:
        logging.error(f"Google Sheets API error: {e}")
        return {"error": str(e)}
    except Exception as e:
        logging.error(f"Error adding user: {e}")
        return {"error": "An error occurred while adding the user."}

def get_google_credentials():
    """Получение учетных данных Google из сессии"""
    try:
        credentials_json = session.get("credentials")
        if not credentials_json:
            return None  # Изменено с raise Exception на более мягкую обработку

        credentials = Credentials.from_authorized_user_info(json.loads(credentials_json), SCOPES)

        if credentials.expired and credentials.refresh_token:
            logging.info("Refreshing credentials...")
            credentials.refresh(Request())
            session["credentials"] = json.loads(credentials.to_json())
            logging.info("Credentials refreshed successfully.")

        return credentials

    except Exception as e:
        logging.error(f"Error getting credentials: {e}")
        return None

def check_user_access(user_email):
    """Проверка доступа пользователя"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return {"error": "Could not retrieve Google credentials."}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Проверка безлимитных пользователей
        unlimited_result = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_UNLIMITED,
            range="A:A"
        ).execute()
        unlimited_users = unlimited_result.get("values", [])

        if unlimited_users and any(user_email == row[0] for row in unlimited_users):
            return {"access": "unlimited"}

        # Проверка лимитных пользователей
        limited_result = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:C"
        ).execute()
        limited_users = limited_result.get("values", [])

        for row in limited_users:
            if len(row) > 0 and user_email == row[0]:
                if len(row) > 1:
                    try:
                        last_used = datetime.fromisoformat(row[1])
                        if datetime.now() - last_used < timedelta(hours=24):
                            return {"error": "Usage limit exceeded."}
                    except ValueError:
                        logging.warning(f"Invalid date format in row: {row}")
                        return {"error": "Invalid date format."}
                return {"access": "limited"}

        return {"error": "Not authorized."}

    except HttpError as e:
        logging.error(f"Google Sheets API error: {e}")
        return {"error": str(e)}
    except Exception as e:
        logging.error(f"Error checking access: {e}")
        return {"error": "An error occurred while checking access."}

def update_last_used(user_email):
    """Обновление времени последнего использования"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return

        sheets_service = build("sheets", "v4", credentials=credentials)
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"
        ).execute()
        limited_users = result.get("values", [])

        if limited_users:
            for i, row in enumerate(limited_users):
                if row and row[0] == user_email:
                    sheets_service.spreadsheets().values().update(
                        spreadsheetId=USERS_LIMITED,
                        range=f"B{i+2}",
                        valueInputOption="RAW",
                        body={"values": [[datetime.now().isoformat()]]}
                    ).execute()
                    break

    except HttpError as e:
        logging.error(f"Google Sheets API error: {e}")
    except Exception as e:
        logging.error(f"Error updating last used time: {e}")

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
        logging.error("client_secrets.json not found")
        flash("Authentication configuration file not found")
        return redirect(url_for("home"))
    except Exception as e:
        logging.error(f"Login error: {e}")
        flash("An error occurred during login")
        return redirect(url_for("home"))

@app.route("/callback")
def callback():
    try:
        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = url_for("callback", _external=True)
        flow.fetch_token(authorization_response=request.url)
        session["credentials"] = json.loads(flow.credentials.to_json())

        oauth2_service = build("oauth2", "v2", credentials=flow.credentials)
        user_info = oauth2_service.userinfo().get().execute()
        session["user_email"] = user_info["email"]
        logging.info(f"User {session['user_email']} logged in successfully")

        add_user_to_limited(session["user_email"])
        return redirect(url_for("home"))

    except FileNotFoundError:
        logging.error("client_secrets.json not found")
        flash("Authentication configuration file not found")
        return redirect(url_for("home"))
    except Exception as e:
        logging.error(f"Callback error: {e}")
        flash("An error occurred during authentication")
        return redirect(url_for("home"))

@app.route("/create_form", methods=["POST"])
def create_form():
    try:
        user_email = session.get("user_email")
        if not user_email:
            flash("Please log in to create a form")
            return redirect(url_for("login"))

        access_check = check_user_access(user_email)
        if "error" in access_check:
            flash(access_check["error"])
            return redirect(url_for("home"))

        credentials = get_google_credentials()
        if not credentials:
            flash("Unable to retrieve Google credentials")
            return redirect(url_for("login"))

        spreadsheet_url = request.form.get("spreadsheet_url")
        if not spreadsheet_url:
            flash("Invalid spreadsheet URL")
            return redirect(url_for("home"))

        sheet_id_match = re.search(r"/d/([a-zA-Z0-9-_]+)", spreadsheet_url)
        if not sheet_id_match:
            flash("Invalid spreadsheet URL format")
            return redirect(url_for("home"))

        sheet_id = sheet_id_match.group(1)
        sheets_service = build("sheets", "v4", credentials=credentials)
        sheet_data = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range="A:Z"
        ).execute().get("values", [])

        if not sheet_data:
            flash("Spreadsheet is empty")
            return redirect(url_for("home"))

        # Создание формы
        form_service = build("forms", "v1", credentials=credentials)
        form_data = {"info": {"title": "Automated Test"}}
        form_response = form_service.forms().create(body=form_data).execute()
        form_id = form_response["formId"]

        # Преобразование в тест
        form_settings_update = {
            "requests": [{
                "updateSettings": {
                    "settings": {"quizSettings": {"isQuiz": True}},
                    "updateMask": "quizSettings.isQuiz"
                }
            }]
        }
        form_service.forms().batchUpdate(formId=form_id, body=form_settings_update).execute()

        # Подготовка запросов
        batch_update_requests = [
            {
                "createItem": {
                    "item": {
                        "title": "Enter your full name",
                        "questionItem": {
                            "question": {
                                "required": True,
                                "textQuestion": {"paragraph": False}
                            }
                        }
                    },
                    "location": {"index": 0}
                }
            },
            {
                "createItem": {
                    "item": {
                        "title": "Test Questions",
                        "description": "Answer the following test questions:",
                        "pageBreakItem": {}
                    },
                    "location": {"index": 1}
                }
            }
        ]

        question_index = 2
        for row in sheet_data:
            if len(row) < 2:
                continue

            question_text = row[0]
            options = []
            correct_answers = []

            for answer in row[1:]:
                is_correct = answer.startswith("*")
                answer_text = answer.lstrip("*")
                options.append({"value": str(answer_text)})
                if is_correct:
                    correct_answers.append({"value": str(answer_text)})

            question_type = "CHECKBOX" if len(correct_answers) > 1 else "RADIO"
            batch_update_requests.append({
                "createItem": {
                    "item": {
                        "title": question_text,
                        "questionItem": {
                            "question": {
                                "required": True,
                                "choiceQuestion": {
                                    "type": question_type,
                                    "options": options,
                                    "shuffle": True
                                }
                            }
                        }
                    },
                    "location": {"index": question_index}
                }
            })
            question_index += 1

        if batch_update_requests:
            form_service.forms().batchUpdate(
                formId=form_id,
                body={"requests": batch_update_requests}
            ).execute()

        # Установка правильных ответов
        form_info = form_service.forms().get(formId=form_id).execute()
        grade_requests = []
        question_items = [item for item in form_info.get('items', []) 
                         if 'questionItem' in item and 
                         'choiceQuestion' in item.get('questionItem', {}).get('question', {})]

        for q_idx, item in enumerate(question_items):
            item_id = item.get('itemId')
            if not item_id or q_idx >= len(sheet_data):
                continue

            row = sheet_data[q_idx]
            correct_answers = [
                {"value": str(answer.lstrip("*"))}
                for answer in row[1:] if answer.startswith("*")
            ]

            if correct_answers:
                grade_requests.append({
                    "updateItem": {
                        "item": {
                            "questionItem": {
                                "question": {
                                    "questionId": item_id,
                                    "required": True,
                                    "grading": {
                                        "pointValue": 1,
                                        "correctAnswers": {"answers": correct_answers}
                                    }
                                }
                            }
                        },
                        "updateMask": "questionItem.question.grading",
                        "location": {"index": q_idx + 2}
                    }
                })

        if grade_requests:
            form_service.forms().batchUpdate(
                formId=form_id,
                body={"requests": grade_requests}
            ).execute()

        if access_check["access"] == "limited":
            update_last_used(user_email)

        form_url = f"https://docs.google.com/forms/d/{form_id}/viewform"
        edit_link = f"https://docs.google.com/forms/d/{form_id}/edit"
        flash(f'<a href="{form_url}" target="_blank">View Form</a> | <a href="{edit_link}" target="_blank">Edit Test</a>')
        return redirect(url_for("home"))

    except HttpError as e:
        logging.error(f"Google API error: {e}")
        flash(f"API error occurred: {e}")
        return redirect(url_for("home"))
    except Exception as e:
        logging.error(f"General error: {e}")
        flash(f"An error occurred: {e}")
        return redirect(url_for("home"))

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out")
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)