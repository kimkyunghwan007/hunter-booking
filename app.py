from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import psycopg
from psycopg.rows import dict_row
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from solapi import SolapiMessageService
from solapi.model import RequestMessage

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-before-deploy")
DB = os.path.join(os.path.dirname(__file__), "hunter.db")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "hunter1234")

SOLAPI_API_KEY = os.environ.get("SOLAPI_API_KEY")
SOLAPI_API_SECRET = os.environ.get("SOLAPI_API_SECRET")
SOLAPI_FROM = os.environ.get("SOLAPI_FROM")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE")

PROGRAMS = {
    "주간체험": {"price": 100000, "capacity": 8},
    "야간체험": {"price": 80000, "capacity": 8},
    "선셋체험": {"price": 250000, "capacity": 4},
}

def send_admin_sms(program, date, people, name, phone):
    if not all([SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_FROM, ADMIN_PHONE]):
        print("SOLAPI 문자 설정이 없습니다.")
        return

    try:
        message_service = SolapiMessageService(
            api_key=SOLAPI_API_KEY,
            api_secret=SOLAPI_API_SECRET
        )

        text = (
            f"[헌터호 새 예약]\n"
            f"{date} {program}\n"
            f"{name} / {people}명\n"
            f"{phone}"
        )

        message = RequestMessage(
            from_=SOLAPI_FROM,
            to=ADMIN_PHONE,
            text=text
        )

        message_service.send(message)
        print("예약 알림 문자 발송 성공")

    except Exception as e:
        print(f"예약 알림 문자 발송 실패: {e}")

def db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 환경변수가 필요합니다.")
    return psycopg.connect(url, row_factory=dict_row)

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings(
        id BIGSERIAL PRIMARY KEY,
        program TEXT NOT NULL,
        date TEXT NOT NULL,
        people INTEGER NOT NULL,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '예약접수',
        created_at TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS schedule(
        id BIGSERIAL PRIMARY KEY,
        program TEXT NOT NULL,
        date TEXT NOT NULL,
        capacity INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT '예약가능',
        UNIQUE(program,
