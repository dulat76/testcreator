import os
import json
import pandas as pd
from flask import Flask, redirect, request, session, url_for, render_template
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key")

# Создание client_secrets.json динамически
CLIENT_SECRETS = {
    "web": {
        "client_id": os.getenv("OAUTH_CLIENT_ID"),
        "client_secret": os.getenv("OAUTH_CLIENT_SECRET"),
        "redirect_uris": ["https://your-app.onrender.com/callback"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"
    }
}
CLIENT_SECRETS_FILE = "client_secrets.json"
with open(CLIENT_SECRETS_FILE, "w") as f:
    json.dump(CLIENT_SECRETS, f)

SCOPES = ["https://www.googleapis.com/auth/forms.body", "https://www.googleapis.com/auth/spreadsheets.readonly"]

def get_google_credentials():
    if "credentials" in session:
        return Credentials.from_authorized_user_info(session["credentials"])
    return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login():
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
    flow.redirect_uri = url_for("callback", _external=True)
    auth_url, _ = flow.authorization_url(prompt="consent")
    return redirect(auth_url)

@app.route("/callback")
def callback():
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
    flow.redirect_uri = url_for("callback", _external=True)
    flow.fetch_token(authorization_response=request.url)
    session["credentials"] = json.loads(flow.credentials.to_json())
    return redirect(url_for("index"))

@app.route("/create_form", methods=["POST"])
def create_form():
    credentials = get_google_credentials()
    if not credentials:
        return redirect(url_for("login"))
    
    spreadsheet_url = request.form.get("spreadsheet_url")
    if not spreadsheet_url:
        return "No spreadsheet URL provided", 400
    
    sheet_id = spreadsheet_url.split("/d/")[1].split("/")[0]
    sheets_service = build("sheets", "v4", credentials=credentials)
    form_service = build("forms", "v1", credentials=credentials)
    
    sheet = sheets_service.spreadsheets().values().get(spreadsheetId=sheet_id, range="A:Z").execute()
    rows = sheet.get("values", [])
    
    if not rows:
        return "No data found in the spreadsheet", 400
    
    form_data = {
        "info": {"title": "Автоматический тест"},
        "items": []
    }
    
    for row in rows:
        if len(row) < 2:
            continue
        
        question = row[0]
        answers = row[1:]
        correct_answers = [a[1:] for a in answers if a.startswith("*")]
        options = [{"value": a.lstrip("*")} for a in answers]
        
        form_data["items"].append({
            "title": question,
            "questionItem": {
                "question": {
                    "required": True,
                    "choiceQuestion": {
                        "type": "RADIO",
                        "options": options,
                        "shuffle": True
                    }
                }
            }
        })
    
    form = form_service.forms().create(body=form_data).execute()
    return redirect(form.get("responderUri"))

if __name__ == "__main__":
    app.run(debug=True)
