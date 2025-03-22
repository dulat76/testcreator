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
from markupsafe import Markup  # Import Markup

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
    "openid"
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
            return None

    return credentials

def check_user_access(user_email):
    """Проверка доступа пользователя (лимитный/безлимитный)"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return {"error": "Could not retrieve Google credentials."}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Проверка безлимитных пользователей
        unlimited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_UNLIMITED,
            range="A:A"
        ).execute().get("values", [])

        if unlimited_users and any(user_email == row[0] for row in unlimited_users if row):
            return {"access": "unlimited"}

        # Проверка лимитных пользователей
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:C"
        ).execute().get("values", [])

        for row in limited_users:
            if not row or len(row) < 1:  # Проверяем только наличие email
                continue

            if user_email == row[0]:
                if len(row) >= 2:  # Проверяем, есть ли дата последнего использования
                    try:
                        last_used = datetime.fromisoformat(row[1])
                        if datetime.now() - last_used < timedelta(hours=24):
                            return {"error": "Превышен лимит использования."}
                    except (ValueError, IndexError):
                        logging.warning(f"Неверный формат даты в строке: {row}")
                return {"access": "limited"}

        # Если пользователь не найден в обеих таблицах
        return {"access": "not_found"}  # Изменили статус на более точный

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
        if not credentials:
            return

        sheets_service = build("sheets", "v4", credentials=credentials)
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"
        ).execute().get("values", [])

        for i, row in enumerate(limited_users):
            if row and row[0] == user_email:
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=USERS_LIMITED,
                    range=f"B{i+1}",
                    valueInputOption="RAW",
                    body={"values": [[datetime.now().isoformat()]]}
                ).execute()
                break
    except HttpError as e:
        logging.error(f"Ошибка Google Sheets API: {e}")
    except Exception as e:
        logging.error(f"Ошибка при обновлении времени последнего использования: {e}")

def add_limited_user(user_email):
    """Добавление нового пользователя в таблицу USERS_LIMITED."""
    try:
        credentials = get_google_credentials()
        if not credentials:
            logging.error("Не удалось получить учетные данные Google.")
            return False

        sheets_service = build("sheets", "v4", credentials=credentials)
        
        # Сначала проверим, существует ли пользователь уже в таблице
        # чтобы избежать дублирования
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"
        ).execute().get("values", [])
        
        # Если пользователь уже существует, возвращаем True
        if limited_users and any(user_email == row[0] for row in limited_users if row):
            logging.info(f"Пользователь {user_email} уже существует в USERS_LIMITED.")
            return True
            
        # Подготавливаем данные для добавления
        values = [
            [user_email, datetime.now().isoformat(), ""]  # Email, Timestamp, Usage Count
        ]
        body = {
            'values': values
        }
        
        # Добавляем пользователя
        result = sheets_service.spreadsheets().values().append(
            spreadsheetId=USERS_LIMITED,
            range="A:C",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()
        
        logging.info(f"Пользователь {user_email} добавлен в USERS_LIMITED.")
        return True
        
    except HttpError as e:
        logging.error(f"Ошибка Google Sheets API при добавлении пользователя: {e}")
        flash(f"Ошибка при добавлении пользователя: {e}")
        return False
    except Exception as e:
        logging.error(f"Ошибка при добавлении пользователя: {e}")
        flash(f"Ошибка при добавлении пользователя: {e}")
        return False

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

        # Проверяем, есть ли пользователь в таблицах доступа
        access_status = check_user_access(session["user_email"])
        
        # Если пользователя нет в таблицах, добавляем его в USERS_LIMITED
        if access_status.get("access") == "not_found":
            if add_limited_user(session["user_email"]):
                flash("Вы успешно зарегистрированы. Теперь вы можете создавать тесты.")
                logging.info(f"Пользователь {session['user_email']} успешно добавлен как лимитный.")
            else:
                flash("Ошибка при регистрации. Пожалуйста, свяжитесь с администратором.")
                logging.error(f"Не удалось добавить пользователя {session['user_email']} в таблицу.")

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

        form_url = form_response.get("responderUri")
        edit_link = f"https://docs.google.com/forms/d/{form_id}/edit"  # Ссылка на редактирование формы
        flash(Markup(f'Форма успешно создана! <a href="{form_url}" target="_blank">Просмотреть форму</a> &nbsp;|&nbsp; <a href="{edit_link}" target="_blank">Редактировать тест</a>'))

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
                is_correct = answer.startswith("*")
                answer_text = answer.lstrip("*")

                # Преобразование числового значения в текст, если это число
                try:
                    if answer_text.replace('.', '', 1).isdigit():
                        answer_text = str(answer_text)  # Гарантируем текстовый формат
                except:
                    pass  # Если это не число, оставляем как есть

                options.append({"value": answer_text})

                # Если ответ был отмечен звездочкой, добавляем его в правильные
                if is_correct:
                    correct_answers.append({"value": answer_text})
                    correct_indices.append(i)

            # Определяем тип вопроса в зависимости от количества правильных ответов
            question_type = "CHECKBOX" if len(correct_answers) > 1 else "RADIO"

            # Создаем запрос на добавление вопроса
            create_item_request = {
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
                    "location": {
                        "index": question_index
                    }
                }
            }

            batch_update_requests.append(create_item_request)
            question_index += 1

        # Выполняем batchUpdate для добавления всех элементов (поле ФИО, раздел и вопросы)
        try:
            if batch_update_requests:
                batch_response = form_service.forms().batchUpdate(
                    formId=form_id,
                    body={"requests": batch_update_requests}
                ).execute()
        except HttpError as e:
            logging.error(f"Error updating form with questions: {e}")
            flash(f"Error updating form with questions: {e}")
            return redirect(url_for("home"))

        # Получим информацию о созданной форме, чтобы узнать ID каждого вопроса
        try:
            form_info = form_service.forms().get(formId=form_id).execute()
        except HttpError as e:
            logging.error(f"Error getting form info: {e}")
            flash(f"Error getting form info: {e}")
            return redirect(url_for("home"))

        # Готовим запросы для установки правильных ответов и баллов
        grade_requests = []

        # Находим все вопросы, кроме поля ФИО
        question_items = []
        for item in form_info.get('items', []):
            if 'questionItem' in item and 'choiceQuestion' in item.get('questionItem', {}).get('question', {}):
                question_items.append(item)

        # Устанавливаем правильные ответы и баллы для каждого вопроса
        for q_idx, item in enumerate(question_items):
            item_id = item.get('itemId')
            # Пропускаем, если нет ID
            if not item_id:
                continue

            # Получаем данные вопроса из исходных данных таблицы
            if q_idx < len(sheet_data):
                row = sheet_data[q_idx]

                # Определяем правильные ответы
                correct_answers = []
                for i, answer in enumerate(row[1:]):
                    if answer.startswith("*"):
                        answer_text = answer.lstrip("*")
                        # Преобразование числовых значений в текст
                        try:
                            if answer_text.replace('.', '', 1).isdigit():
                                answer_text = str(answer_text)
                        except:
                            pass
                        correct_answers.append({"value": answer_text})

                # Создаем запрос для установки правильных ответов и баллов за вопрос
                update_grade_request = {
                    "updateItemFeedback": {
                        "itemId": item_id,
                        "feedback": {
                            "correctAnswers": correct_answers,
                            "generalFeedback": {
                                "text": "Спасибо за ваш ответ!"
                            }
                        }
                    }
                }
                grade_requests.append(update_grade_request)

        # Отправляем запросы для установки правильных ответов и баллов за вопросы
        try:
            if grade_requests:
                grade_response = form_service.forms().batchUpdate(
                    formId=form_id,
                    body={"requests": grade_requests}
                ).execute()
        except HttpError as e:
            logging.error(f"Error updating correct answers: {e}")
            flash(f"Error updating correct answers: {e}")
            return redirect(url_for("home"))

        if access_check["access"] == "limited":
            update_last_used(user_email)

        return redirect(url_for("home"))

    except Exception as e:
        logging.error(f"Error creating form: {e}")
        flash(f"Произошла ошибка при создании формы: {e}")
        return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)