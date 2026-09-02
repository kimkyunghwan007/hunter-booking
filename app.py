from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    send_from_directory
)

import os
import calendar
from datetime import datetime, date

import psycopg
from psycopg.rows import dict_row

from solapi import SolapiMessageService
from solapi.model import RequestMessage


app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key-before-deploy"
)


# =========================
# 관리자
# =========================

ADMIN_ID = os.environ.get("ADMIN_ID", "admin")
ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "hunter1234"
)


# =========================
# 문자
# =========================

SOLAPI_API_KEY = os.environ.get("SOLAPI_API_KEY")
SOLAPI_API_SECRET = os.environ.get("SOLAPI_API_SECRET")
SOLAPI_FROM = os.environ.get("SOLAPI_FROM")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE")


# =========================
# 계좌
# =========================

BANK_NAME = "토스뱅크"
BANK_ACCOUNT = "1002-3983-0407"
BANK_HOLDER = "김경환"


# =========================
# 프로그램
# =========================

PROGRAMS = {

    "주간체험": {
        "price": 100000,
        "capacity": 8
    },

    "야간체험": {
        "price": 80000,
        "capacity": 6
    },

    "선셋체험": {
        "price": 250000,
        "capacity": 4
    }

}


# =========================
# DB 연결
# =========================

def db():

    database_url = os.environ.get("DATABASE_URL")

    return psycopg.connect(
        database_url,
        row_factory=dict_row
    )


# =========================
# DB 생성
# =========================

def init_db():

    con = db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS bookings (

            id BIGSERIAL PRIMARY KEY,

            program TEXT NOT NULL,

            date TEXT NOT NULL,

            people INTEGER NOT NULL,

            name TEXT NOT NULL,

            phone TEXT NOT NULL,

            status TEXT DEFAULT '예약접수',

            created_at TEXT NOT NULL

        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS schedule (

            id BIGSERIAL PRIMARY KEY,

            program TEXT NOT NULL,

            date TEXT NOT NULL,

            capacity INTEGER NOT NULL,

            state TEXT NOT NULL,

            UNIQUE(program, date)

        )
    """)

    con.execute("""
        ALTER TABLE bookings
        ADD COLUMN IF NOT EXISTS payment_type TEXT
    """)

    con.execute("""
        ALTER TABLE bookings
        ADD COLUMN IF NOT EXISTS payment_amount INTEGER
    """)

    con.execute("""
        ALTER TABLE bookings
        ADD COLUMN IF NOT EXISTS total_amount INTEGER
    """)

    con.execute("""
        ALTER TABLE bookings
        ADD COLUMN IF NOT EXISTS admin_note TEXT
    """)

    # 예전 야간체험 8명 설정 → 6명
    con.execute("""
        UPDATE schedule
        SET capacity = 6
        WHERE program = '야간체험'
          AND capacity = 8
    """)

    con.commit()
    con.close()


# =========================
# 문자 발송
# =========================

def sms(to, text):

    if not all([
        SOLAPI_API_KEY,
        SOLAPI_API_SECRET,
        SOLAPI_FROM,
        to
    ]):

        print("SOLAPI 문자 설정 누락")
        return

    try:

        service = SolapiMessageService(
            api_key=SOLAPI_API_KEY,
            api_secret=SOLAPI_API_SECRET
        )

        service.send(

            RequestMessage(

                from_=SOLAPI_FROM,

                to=to.replace("-", "").strip(),

                text=text

            )

        )

    except Exception as e:

        print("문자 발송 실패:", e)


# =========================
# 스케줄
# =========================

def get_schedule(program, dt):

    con = db()

    row = con.execute("""
        SELECT *
        FROM schedule
        WHERE program = %s
          AND date = %s
    """, (
        program,
        dt
    )).fetchone()

    con.close()

    return row


# =========================
# 예약 인원
# =========================

def booked_people(program, dt):

    con = db()

    row = con.execute("""
        SELECT COALESCE(
            SUM(people),
            0
