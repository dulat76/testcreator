import os
import re
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, redirect, request, session, url_for, render_template, flash, Markup
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Настройки приложения
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key")  # В производственной среде должен быть задан через env
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
        logging.warning("No credentials found in session")
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
                flash("Error refreshing credentials. Please log in again.")
                return None

        return credentials
    except Exception as e:
        logging.error(f"Error creating credentials object: {e}")
        return None

def add_user_to_limited(user_email):
    """Добавление нового пользователя в таблицу лимитных пользователей"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return {"error": "Could not retrieve Google credentials."}

        # Проверяем наличие ID таблицы
        if not USERS_LIMITED:
            logging.error("LIMITED_USERS_SHEET environment variable is not set")
            return {"error": "Sheet ID not configured"}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Получаем текущий список пользователей
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"  # Проверяем только колонку с email
        ).execute().get("values", [])

        # Проверяем, существует ли пользователь уже в таблице
        if limited_users and any(row and user_email == row[0] for row in limited_users):
            return {"message": "User already exists."}

        # Добавляем нового пользователя с текущей датой
        new_user_row = [user_email, datetime.now().isoformat()]
        sheets_service.spreadsheets().values().append(
            spreadsheetId=USERS_LIMITED,
            range="A:B",  # Указываем диапазон для добавления данных в колонки A и B
            valueInputOption="RAW",
            body={"values": [new_user_row]}
        ).execute()

        return {"message": "User added successfully."}

    except HttpError as e:
        logging.error(f"Ошибка Google Sheets API: {e}")
        return {"error": str(e)}
    except Exception as e:
        logging.error(f"Ошибка при добавлении пользователя: {e}")
        return {"error": "Произошла ошибка при добавлении пользователя."}

def check_user_access(user_email):
    """Проверка доступа пользователя (лимитный/безлимитный)"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return {"error": "Could not retrieve Google credentials."}

        # Проверяем наличие ID таблиц
        if not USERS_UNLIMITED or not USERS_LIMITED:
            logging.error("Sheet ID environment variables are not set")
            return {"error": "Sheet IDs not configured"}

        sheets_service = build("sheets", "v4", credentials=credentials)

        # Проверка безлимитных пользователей
        try:
            unlimited_users = sheets_service.spreadsheets().values().get(
                spreadsheetId=USERS_UNLIMITED,
                range="A:A"
            ).execute().get("values", [])

            if unlimited_users and any(row and user_email == row[0] for row in unlimited_users):
                return {"access": "unlimited"}
        except HttpError as e:
            logging.error(f"Error accessing unlimited users sheet: {e}")
            # Продолжаем проверку лимитных пользователей

        # Проверка лимитных пользователей
        try:
            limited_users = sheets_service.spreadsheets().values().get(
                spreadsheetId=USERS_LIMITED,
                range="A:C"
            ).execute().get("values", [])

            for row in limited_users:
                if len(row) >= 2 and user_email == row[0]:
                    try:
                        last_used = datetime.fromisoformat(row[1])
                        if datetime.now() - last_used < timedelta(hours=24):
                            return {"error": "Превышен лимит использования. Вы сможете создать новый тест через 24 часа."}
                    except (ValueError, IndexError):
                        logging.warning(f"Неверный формат даты в строке: {row}")
                        # Если дата некорректна, разрешаем пользователю доступ
                    return {"access": "limited"}
        except HttpError as e:
            logging.error(f"Error accessing limited users sheet: {e}")
            return {"error": f"Error accessing user data: {str(e)}"}

        # Если пользователь не найден ни в одной из таблиц
        return {"error": "Пользователь не авторизован."}

    except Exception as e:
        logging.error(f"Ошибка при проверке доступа: {e}")
        return {"error": f"Произошла ошибка при проверке доступа: {str(e)}"}

def update_last_used(user_email):
    """Обновление времени последнего использования для лимитных пользователей"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            logging.error("Could not retrieve Google credentials for updating last used time")
            return

        if not USERS_LIMITED:
            logging.error("LIMITED_USERS_SHEET environment variable is not set")
            return

        sheets_service = build("sheets", "v4", credentials=credentials)
        limited_users = sheets_service.spreadsheets().values().get(
            spreadsheetId=USERS_LIMITED,
            range="A:A"
        ).execute().get("values", [])

        if not limited_users:
            logging.warning("No users found in the limited users sheet")
            return

        for i, row in enumerate(limited_users):
            if row and row[0] == user_email:
                current_time = datetime.now().isoformat()
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=USERS_LIMITED,
                    range=f"B{i+1}",  # Индексация в API начинается с 1
                    valueInputOption="RAW",
                    body={"values": [[current_time]]}
                ).execute()
                logging.info(f"Updated last used time for user {user_email}")
                break
        else:
            logging.warning(f"User {user_email} not found in limited users sheet")
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
        # Создаем поток OAuth для аутентификации
        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = url_for("callback", _external=True)
        auth_url, _ = flow.authorization_url(prompt="consent")
        session.clear()  # Очищаем сессию перед новым входом
        return redirect(auth_url)
    except FileNotFoundError:
        logging.error("client_secrets.json not found. Ensure it is in the same directory as the script.")
        flash("client_secrets.json not found. Please check your deployment.")
        return redirect(url_for("home"))
    except Exception as e:
        logging.error(f"Error during login: {e}")
        flash("Произошла ошибка во время входа.")
        return redirect(url_for("home"))

@app.route("/callback")
def callback():
    try:
        # Проверяем наличие ошибки в ответе
        if "error" in request.args:
            error = request.args.get("error")
            logging.error(f"OAuth error: {error}")
            flash(f"Ошибка авторизации: {error}")
            return redirect(url_for("home"))

        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = url_for("callback", _external=True)
        
        # Обрабатываем ответ авторизации
        flow.fetch_token(authorization_response=request.url)
        session["credentials"] = json.loads(flow.credentials.to_json())

        # Получаем информацию о пользователе
        credentials = get_google_credentials()
        if not credentials:
            flash("Не удалось получить учетные данные.")
            return redirect(url_for("home"))
            
        oauth2_service = build("oauth2", "v2", credentials=credentials)
        user_info = oauth2_service.userinfo().get().execute()
        
        if "email" not in user_info:
            logging.error("Email not found in user info")
            flash("Не удалось получить email пользователя.")
            return redirect(url_for("home"))
            
        session["user_email"] = user_info["email"]
        logging.info(f"User {session['user_email']} logged in successfully.")

        # Добавляем пользователя в таблицу лимитных пользователей, если его там нет
        result = add_user_to_limited(session["user_email"])
        if "error" in result:
            logging.warning(f"Could not add user to limited sheet: {result['error']}")
            # Не прерываем вход, просто логируем ошибку

        return redirect(url_for("home"))
    except FileNotFoundError:
        logging.error("client_secrets.json not found. Ensure it is in the same directory as the script.")
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
        spreadsheet_url = request.form.get("spreadsheet_url", "").strip()
        if not spreadsheet_url:
            flash("Пожалуйста, введите ссылку на таблицу Google.")
            return redirect(url_for("home"))

        # Извлечение ID таблицы из URL
        sheet_id_match = re.search(r"/d/([a-zA-Z0-9-_]+)", spreadsheet_url)
        if not sheet_id_match:
            flash("Неверный формат ссылки на таблицу Google. Пожалуйста, используйте корректную ссылку.")
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
            error_message = "Не удалось получить доступ к таблице. "
            if "404" in str(e):
                error_message += "Таблица не найдена или у вас нет доступа к ней."
            else:
                error_message += f"Ошибка: {str(e)}"
            flash(error_message)
            return redirect(url_for("home"))

        if not sheet_data:
            flash("Таблица пуста! Пожалуйста, добавьте вопросы и ответы в таблицу.")
            return redirect(url_for("home"))

        # Проверка формата данных в таблице
        for i, row in enumerate(sheet_data):
            if len(row) < 2:
                flash(f"Ошибка в строке {i+1}: Каждая строка должна содержать вопрос и минимум один вариант ответа.")
                return redirect(url_for("home"))
            
            # Проверка наличия хотя бы одного правильного ответа
            has_correct_answer = any(answer.startswith("*") for answer in row[1:])
            if not has_correct_answer:
                flash(f"Ошибка в строке {i+1}: Не указан правильный ответ. Отметьте правильные ответы символом * в начале.")
                return redirect(url_for("home"))

        # Создание формы - только с заголовком
        form_title = request.form.get("form_title", "Автоматический тест").strip()
        if not form_title:
            form_title = "Автоматический тест"
            
        form_service = build("forms", "v1", credentials=credentials)
        form_data = {
            "info": {"title": form_title}
        }
        
        try:
            form_response = form_service.forms().create(body=form_data).execute()
            form_id = form_response.get("formId")
            if not form_id:
                raise ValueError("FormId not found in response")
        except HttpError as e:
            logging.error(f"Error creating form: {e}")
            flash(f"Ошибка при создании формы: {str(e)}")
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
            flash(f"Ошибка при настройке теста: {str(e)}")
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
                if not answer.strip():  # Пропускаем пустые ответы
                    continue
                    
                is_correct = answer.startswith("*")
                answer_text = answer.lstrip("*").strip()

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

            # Пропускаем вопросы без вариантов ответа
            if not options:
                continue

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
                form_service.forms().batchUpdate(
                    formId=form_id,
                    body={"requests": batch_update_requests}
                ).execute()
            else:
                flash("Не найдено вопросов для добавления в тест.")
                return redirect(url_for("home"))
        except HttpError as e:
            logging.error(f"Error updating form with questions: {e}")
            flash(f"Ошибка при добавлении вопросов: {str(e)}")
            return redirect(url_for("home"))

        # Получим информацию о созданной форме, чтобы узнать ID каждого вопроса
        try:
            form_info = form_service.forms().get(formId=form_id).execute()
        except HttpError as e:
            logging.error(f"Error getting form info: {e}")
            flash(f"Ошибка при получении информации о форме: {str(e)}")
            return redirect(url_for("home"))

        # Готовим запросы для установки правильных ответов и баллов
        grade_requests = []

        # Находим все вопросы, кроме поля ФИО
        question_items = []
        for item in form_info.get('items', []):
            if 'questionItem' in item and 'choiceQuestion' in item.get('questionItem', {}).get('question', {}):
                question_items.append(item)

        # Отображаем вопросы из таблицы на вопросы в форме, пропуская поле ФИО
        sheet_data_index = 0
        for q_idx, item in enumerate(question_items):
            item_id = item.get('itemId')
            
            # Пропускаем, если нет ID или достигли конца данных таблицы
            if not item_id or sheet_data_index >= len(sheet_data):
                continue

            row = sheet_data[sheet_data_index]
            sheet_data_index += 1

            # Определяем правильные ответы
            correct_answers = []
            for answer in row[1:]:
                if answer.startswith("*"):
                    answer_text = answer.lstrip("*").strip()
                    # Преобразование числовых значений в текст
                    try:
                        if answer_text.replace('.', '', 1).isdigit():
                            answer_text = str(answer_text)
                    except:
                        pass
                    correct_answers.append({"value": answer_text})

            # Если есть правильные ответы
            if correct_answers:
                grade_request = {
                    "updateItem": {
                        "item": {
                            "questionItem": {
                                "question": {
                                    "questionId": item_id,
                                    "required": True,
                                    "grading": {
                                        "pointValue": 1,  # 1 балл за вопрос
                                        "correctAnswers": {
                                            "answers": correct_answers
                                        }
                                    }
                                }
                            }
                        },
                        "updateMask": "questionItem.question.grading",
                        "location": {
                            "index": q_idx + 2  # +2 для учета поля ФИО и раздела
                        }
                    }
                }
                grade_requests.append(grade_request)

        # Выполняем запросы на установку правильных ответов
        try:
            if grade_requests:
                form_service.forms().batchUpdate(
                    formId=form_id,
                    body={"requests": grade_requests}
                ).execute()
                logging.info("Successfully updated correct answers and grading for the form.")
            else:
                logging.warning("No grading requests were created")
        except HttpError as e:
            logging.error(f"Error updating correct answers: {e}")
            flash(f"Ошибка при настройке правильных ответов: {str(e)}")
            # Продолжаем, так как форма уже создана

        # Обновляем время последнего использования для лимитных пользователей
        if access_check.get("access") == "limited":
            update_last_used(user_email)

        # Возвращаем ссылку на созданную форму
        form_url = f"https://docs.google.com/forms/d/{form_id}/viewform"
        edit_link = f"https://docs.google.com/forms/d/{form_id}/edit"
        
        # Используем Markup для безопасного добавления HTML в сообщение flash
        flash(Markup(f'Тест успешно создан! <a href="{form_url}" target="_blank">Просмотреть форму</a> &nbsp;|&nbsp; <a href="{edit_link}" target="_blank">Редактировать тест</a>'))
        
        return redirect(url_for("home"))

    except Exception as e:
        logging.error(f"Произошла общая ошибка: {e}")
        flash(f"Произошла ошибка при создании формы: {str(e)}")
        return redirect(url_for("home"))

@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из системы.")
    return redirect(url_for("home"))

if __name__ == "__main__":
    # Проверка наличия всех необходимых переменных окружения
    if not USERS_LIMITED:
        logging.warning("LIMITED_USERS_SHEET environment variable is not set")
    if not USERS_UNLIMITED:
        logging.warning("UNLIMITED_USERS_SHEET environment variable is not set")
    if os.getenv("SECRET_KEY") == "super_secret_key":
        logging.warning("Using default secret key. This is insecure for production environments.")
        
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() in ("true", "1", "t"))