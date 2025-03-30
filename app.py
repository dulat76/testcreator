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
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key")  # Используйте переменные окружения
logging.basicConfig(
    filename='app.log',  # Логирование в файл
    level=logging.DEBUG,  # Уровень логирования DEBUG для отладки
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Конфигурация таблиц пользователей
USERS_LIMITED = os.getenv("LIMITED_USERS_SHEET")
USERS_UNLIMITED = os.getenv("UNLIMITED_USERS_SHEET")

# OAuth настройки
CLIENT_SECRETS_FILE = "client_secrets.json"
SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/spreadsheets",
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
        try:
            limited_users = sheets_service.spreadsheets().values().get(
                spreadsheetId=USERS_LIMITED,
                range="A:A"  # Проверяем только колонку с email
            ).execute().get("values", [])
        except HttpError as e:
            logging.error(f"Ошибка при получении списка лимитных пользователей: {e}")
            return {"error": "Ошибка при получении списка лимитных пользователей."}

        # Проверяем, существует ли пользователь уже в таблице
        if limited_users and any(user_email == row[0] for row in limited_users if row):
            return {"message": "User already exists."}

        # Добавляем нового пользователя с текущей датой
        new_user_row = [user_email, datetime.now().isoformat()]
        try:
            result = sheets_service.spreadsheets().values().append(
                spreadsheetId=USERS_LIMITED,
                range="A:B",  # Указываем диапазон для добавления данных в колонки A и B
                valueInputOption="RAW",
                body={"values": [new_user_row]}
            ).execute()

            logging.info(f"User add result: {result}")
            return {"message": "User added successfully."}
        except HttpError as e:
            logging.error(f"Ошибка при добавлении пользователя в таблицу: {e}")
            return {"error": "Ошибка при добавлении пользователя в таблицу."}

    except Exception as e:
        logging.error(f"Ошибка при добавлении пользователя: {e}")
        return {"error": f"Произошла ошибка при добавлении пользователя: {str(e)}"}

def add_user_to_unlimited(user_email):
    """Добавление нового пользователя в таблицу безлимитных пользователей"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return {"error": "Could not retrieve Google credentials."}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Получаем текущий список пользователей
        try:
            unlimited_users = sheets_service.spreadsheets().values().get(
                spreadsheetId=USERS_UNLIMITED,
                range="A:A"  # Проверяем только колонку с email
            ).execute().get("values", [])
        except HttpError as e:
            logging.error(f"Ошибка при получении списка безлимитных пользователей: {e}")
            return {"error": "Ошибка при получении списка безлимитных пользователей."}

        # Проверяем, существует ли пользователь уже в таблице
        if unlimited_users and any(user_email == row[0] for row in unlimited_users if row):
            return {"message": "User already exists in unlimited users."}

        # Добавляем нового пользователя
        new_user_row = [user_email, datetime.now().isoformat()]
        try:
            result = sheets_service.spreadsheets().values().append(
                spreadsheetId=USERS_UNLIMITED,
                range="A:B",
                valueInputOption="RAW",
                body={"values": [new_user_row]}
            ).execute()

            logging.info(f"User add to unlimited result: {result}")
            return {"message": "User added to unlimited successfully."}
        except HttpError as e:
            logging.error(f"Ошибка при добавлении пользователя в таблицу безлимитных пользователей: {e}")
            return {"error": "Ошибка при добавлении пользователя в таблицу безлимитных пользователей."}

    except Exception as e:
        logging.error(f"Ошибка при добавлении безлимитного пользователя: {e}")
        return {"error": f"Произошла ошибка при добавлении безлимитного пользователя: {str(e)}"}

def get_google_credentials():
    """Получение учетных данных Google из сессии."""
    credentials_json = session.get("credentials")
    if not credentials_json:
        flash("Пожалуйста, войдите в систему.")
        return None

    try:
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
                flash("Ошибка обновления учетных данных. Пожалуйста, войдите снова.")
                return None

        return credentials
    except Exception as e:
        logging.error(f"Ошибка при создании учетных данных: {e}")
        flash("Ошибка при создании учетных данных. Пожалуйста, войдите снова.")
        return None

def check_user_access(user_email):
    """Проверка доступа пользователя (лимитный/безлимитный)"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return {"error": "Could not retrieve Google credentials."}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Проверка безлимитных пользователей
        try:
            unlimited_users = sheets_service.spreadsheets().values().get(
                spreadsheetId=USERS_UNLIMITED,
                range="A:A"
            ).execute().get("values", [])
        except HttpError as e:
            logging.error(f"Ошибка при получении списка безлимитных пользователей: {e}")
            return {"error": "Ошибка при получении списка безлимитных пользователей."}

        if unlimited_users and any(user_email == row[0] for row in unlimited_users if row):
            return {"access": "unlimited"}

        # Проверка лимитных пользователей
        try:
            limited_users = sheets_service.spreadsheets().values().get(
                spreadsheetId=USERS_LIMITED,
                range="A:C"
            ).execute().get("values", [])
        except HttpError as e:
            logging.error(f"Ошибка при получении списка лимитных пользователей: {e}")
            return {"error": "Ошибка при получении списка лимитных пользователей."}

        for row in limited_users:
            if len(row) > 0 and user_email == row[0]:
                if len(row) > 1:
                    try:
                        last_used = datetime.fromisoformat(row[1])
                        if datetime.now() - last_used < timedelta(hours=24):
                            return {"error": "Превышен лимит использования."}
                    except ValueError:
                        logging.warning(f"Неверный формат даты в строке: {row}")
                        return {"error": "Неверный формат даты."}
                return {"access": "limited"}

        # Если пользователь не найден, добавляем его в лимитные
        add_result = add_user_to_limited(user_email)
        if "error" in add_result:
            return {"error": add_result["error"]}
        return {"access": "limited"}

    except Exception as e:
        logging.error(f"Ошибка при проверке доступа: {e}")
        return {"error": "Произошла ошибка при проверке доступа."}

def update_last_used(user_email):
    """Обновление времени последнего использования для лимитных пользователей"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return

        sheets_service = build("sheets", "v4", credentials=credentials)
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"
        ).execute().get("values", [])

        for i, row in enumerate(limited_users):
            if row and row[0] == user_email:
                try:
                    sheets_service.spreadsheets().values().update(
                        spreadsheetId=USERS_LIMITED,
                        range=f"B{i+1}",  # Индексация начинается с 1 в Google Sheets
                        valueInputOption="RAW",
                        body={"values": [[datetime.now().isoformat()]]}
                    ).execute()
                    logging.info(f"Updated last_used time for user {user_email}")
                    break
                except HttpError as e:
                    logging.error(f"Ошибка при обновлении времени последнего использования в таблице: {e}")
                    flash("Ошибка при обновлении времени последнего использования.")
                    break  # Важно выйти из цикла, если произошла ошибка
    except Exception as e:
        logging.error(f"Ошибка при обновлении времени последнего использования: {e}")
        flash("Ошибка при обновлении времени последнего использования.")

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

        # Добавляем пользователя в безлимитные (в соответствии с комментарием)
        result = add_user_to_unlimited(session["user_email"])
        logging.info(f"Add user to unlimited result: {result}")

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
        try:
            sheet_data = sheets_service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range="A:Z"
            ).execute().get("values", [])
        except HttpError as e:
            logging.error(f"Ошибка при чтении данных из таблицы: {e}")
            flash("Ошибка при чтении данных из таблицы.")
            return redirect(url_for("home"))

        if not sheet_data:
            flash("Таблица пуста!")
            return redirect(url_for("home"))

        # Создание формы - только с заголовком
        form_service = build("forms", "v1", credentials=credentials)
        form_data = {
            "info": {"title": "Автоматический тест"}
        }
        try:
            form_response = form_service.forms().create(body=form_data).execute()
            form_id = form_response.get("formId")
        except HttpError as e:
            logging.error(f"Error creating form: {e}")
            flash(f"Error creating form: {e}")
            return redirect(url_for("home"))

        # Превращаем форму в тест (Quiz)
        form_settings_update = {
            "requests": [
                {
                    "updateSettings": {
                        "settings": {
                            "quizSettings": {
                                "isQuiz": True
                            }
                        },
                        "updateMask": "quizSettings.isQuiz"
                    }
                }
            ]
        }
        try:
            form_service.forms().batchUpdate(formId=form_id, body=form_settings_update).execute()
        except HttpError as e:
            logging.error(f"Error converting form to quiz: {e}")
            flash(f"Error converting form to quiz: {e}")
            return redirect(url_for("home"))

        # Подготавливаем начальные запросы с полем для ввода ФИО и разделом
        batch_update_requests = []

        # Добавляем поле для ввода ФИО
        create_name_field = {
            "createItem": {
                "item": {
                    "title": "Введите ваше ФИО",
                    "questionItem": {
                        "question": {
                            "required": True,
                            "textQuestion": {
                                "paragraph": False
                            }
                        }
                    }
                },
                "location": {
                    "index": 0
                }
            }
        }
        batch_update_requests.append(create_name_field)

        # Добавляем раздел после поля ФИО
        create_section = {
            "createItem": {
                "item": {
                    "title": "Тестовые вопросы",
                    "description": "Ответьте на следующие вопросы теста:",
                    "pageBreakItem": {}
                },
                "location": {
                    "index": 1
                }
            }
        }
        batch_update_requests.append(create_section)

        # Подготавливаем запросы для создания вопросов, начиная с индекса 2 (после поля ФИО и раздела)
        question_index = 2

        for row in sheet_data:
            if len(row) < 2:
                continue

            question_text = row[0]

            # Определение правильных ответов (отмеченных звездочкой)
            options = []
            correct_answers = []
            correct_indices = []

            for i, answer in enumerate(row[1:]):
                if answer.endswith("*"):
                    options.append(answer[:-1])
                    correct_answers.append(answer[:-1])
                    correct_indices.append(i)
                else:
                    options.append(answer)

            if not options:
                continue

            # Создаем вопрос типа "multiple choice"
            new_question = {
                "createItem": {
                    "item": {
                        "title": question_text,
                        "questionItem": {
                            "question": {
                                "required": True,
                                "choiceQuestion": {
                                    "type": "RADIO",
                                    "options": [{"value": option} for option in options],
                                    "shuffleOptions": True
                                }
                            }
                        }
                    },
                    "location": {
                        "index": question_index
                    }
                }
            }
            batch_update_requests.append(new_question)

            # Создаем ключ ответа на вопрос
            correct_answer_object = {
                "updateItemFeedback": {
                    "itemId": new_question["createItem"]["item"]["questionItem"]["question"]["questionId"],
                    "feedback": {
                        "correctAnswers": {
                            "answers": [{"value": answer} for answer in correct_answers]
                        }
                    }
                }
            }

            # Обновляем ключ ответа для формы
            update_answer_key = {
                "updateQuestion": {
                    "question": {
                        "questionId": new_question["createItem"]["item"]["questionItem"]["question"]["questionId"],
                        "correctAnswers": {
                            "answers": [{"value": answer} for answer in correct_answers]
                        }
                    },
                    "updateMask": "correctAnswers"
                }
            }

            batch_update_requests.append(update_answer_key)

            question_index += 1

        # Отправляем batch update с запросами
        body = {"requests": batch_update_requests}
        try:
            update_response = form_service.forms().batchUpdate(formId=form_id, body=body).execute()
        except HttpError as e:
            logging.error(f"Error during batch update: {e}")
            flash(f"Error during batch update: {e}")
            return redirect(url_for("home"))

        # Обновляем время последнего использования
        update_last_used(user_email)

        # Перенаправляем пользователя на созданную форму
        form_url = f"https://docs.google.com/forms/d/{form_id}/viewform"
        return redirect(form_url)

    except Exception as e:
        logging.error(f"Общая ошибка при создании формы: {e}")
        flash("Произошла общая ошибка при создании формы.")
        return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
