from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import psycopg
from psycopg.rows import dict_row
from datetime import datetime
from solapi import SolapiMessageService
from solapi.model import RequestMessage

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-before-deploy")

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
        print("SOLAPI 문자 설정 누락")
        return
    try:
        message_service = SolapiMessageService(
            api_key=SOLAPI_API_KEY,
            api_secret=SOLAPI_API_SECRET,
        )
        text = (
            "[헌터호 새 예약]\n"
            f"{date} {program}\n"
            f"{name} / {people}명\n"
            f"{phone}"
        )
        message = RequestMessage(
            from_=SOLAPI_FROM,
            to=ADMIN_PHONE,
            text=text,
        )
        response = message_service.send(message)
        print("예약 알림 문자 발송 성공")
        try:
            print(f"SOLAPI Group ID: {response.group_info.group_id}")
        except Exception:
            pass
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
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedule(
            id BIGSERIAL PRIMARY KEY,
            program TEXT NOT NULL,
            date TEXT NOT NULL,
            capacity INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT '예약가능',
            UNIQUE(program, date)
        )
    """)

    con.commit()
    con.close()

def get_schedule(program, date):
    con = db()
    row = con.execute(
        "SELECT * FROM schedule WHERE program=%s AND date=%s",
        (program, date),
    ).fetchone()
    con.close()
    if row:
        return dict(row)
    return {
        "program": program,
        "date": date,
        "capacity": PROGRAMS[program]["capacity"],
        "state": "예약가능",
    }

def booked_people(program, date):
    con = db()
    row = con.execute("""
        SELECT COALESCE(SUM(people), 0) AS total
        FROM bookings
        WHERE program=%s
          AND date=%s
          AND status IN ('예약접수', '예약확정')
    """, (program, date)).fetchone()
    con.close()
    return int(row["total"])

def availability(program, date):
    sch = get_schedule(program, date)
    if sch["state"] != "예약가능":
        return 0, sch["state"]
    remaining = max(0, int(sch["capacity"]) - booked_people(program, date))
    if remaining == 0:
        return 0, "예약마감"
    return remaining, "예약가능"

@app.route("/")
def home():
    return render_template("index.html", programs=PROGRAMS)

@app.route("/api/availability")
def api_availability():
    program = request.args.get("program")
    date = request.args.get("date")
    if program not in PROGRAMS or not date:
        return jsonify({"ok": False, "message": "잘못된 요청입니다."}), 400
    remaining, state = availability(program, date)
    return jsonify({"ok": True, "remaining": remaining, "state": state})

@app.route("/reserve", methods=["POST"])
def reserve():
    program = request.form.get("program")
    date = request.form.get("date")
    people = request.form.get("people", type=int)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip().replace("-", "")

    if program not in PROGRAMS or not date or not people or not name or not phone:
        flash("예약정보를 모두 입력해주세요.")
        return redirect(url_for("home") + "#reserve")

    remaining, state = availability(program, date)
    if state != "예약가능" or people > remaining:
        flash(f"현재 예약 가능한 인원은 {remaining}명입니다.")
        return redirect(url_for("home") + "#reserve")

    con = db()
    con.execute("""
        INSERT INTO bookings(program, date, people, name, phone, status, created_at)
        VALUES(%s, %s, %s, %s, %s, '예약접수', %s)
    """, (
        program,
        date,
        people,
        name,
        phone,
        datetime.now().isoformat(timespec="seconds"),
    ))
    con.commit()
    con.close()

    send_admin_sms(program, date, people, name, phone)

    flash("예약 신청이 접수되었습니다. 관리자 확인 후 확정됩니다.")
    return redirect(url_for("home") + "#reserve")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("username") == ADMIN_USER and request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("아이디 또는 비밀번호가 올바르지 않습니다.")
    return render_template("login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

def require_admin():
    return session.get("admin") is True

@app.route("/admin")
def admin():
    if not require_admin():
        return redirect(url_for("admin_login"))
    con = db()
    bookings = con.execute(
        "SELECT * FROM bookings ORDER BY date ASC, created_at DESC"
    ).fetchall()
    schedules = con.execute(
        "SELECT * FROM schedule ORDER BY date ASC, program ASC"
    ).fetchall()
    con.close()
    return render_template(
        "admin.html",
        bookings=bookings,
        schedules=schedules,
        programs=PROGRAMS,
    )

@app.route("/admin/booking/<int:bid>/<status>", methods=["POST"])
def set_booking_status(bid, status):
    if not require_admin():
        return redirect(url_for("admin_login"))
    if status not in ("예약접수", "예약확정", "취소"):
        return "invalid", 400
    con = db()
    con.execute(
        "UPDATE bookings SET status=%s WHERE id=%s",
        (status, bid),
    )
    con.commit()
    con.close()
    return redirect(url_for("admin"))

@app.route("/admin/schedule", methods=["POST"])
def set_schedule():
    if not require_admin():
        return redirect(url_for("admin_login"))
    program = request.form.get("program")
    date = request.form.get("date")
    capacity = request.form.get("capacity", type=int)
    state = request.form.get("state")

    if (
        program not in PROGRAMS
        or not date
        or capacity is None
        or state not in ("예약가능", "예약마감", "운항없음")
    ):
        flash("운항 설정값을 확인해주세요.")
        return redirect(url_for("admin"))

    con = db()
    con.execute("""
        INSERT INTO schedule(program, date, capacity, state)
        VALUES(%s, %s, %s, %s)
        ON CONFLICT(program, date)
        DO UPDATE SET capacity=excluded.capacity, state=excluded.state
    """, (program, date, capacity, state))
    con.commit()
    con.close()

    flash("운항 설정이 저장되었습니다.")
    return redirect(url_for("admin"))

init_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
    )
