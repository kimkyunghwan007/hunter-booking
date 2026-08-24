from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
import os
import calendar
from datetime import datetime, date

import psycopg
from psycopg.rows import dict_row
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

BANK_NAME = "카카오페이증권"
BANK_ACCOUNT = "020-04-261519"
BANK_HOLDER = "김경환"

PROGRAMS = {
    "주간체험": {"price": 100000, "capacity": 8},
    "야간체험": {"price": 80000, "capacity": 6},
    "선셋체험": {"price": 250000, "capacity": 4},
}

def sms(to, text):
    if not all([SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_FROM, to]):
        print("SOLAPI 문자 설정 누락")
        return
    try:
        service = SolapiMessageService(api_key=SOLAPI_API_KEY, api_secret=SOLAPI_API_SECRET)
        service.send(RequestMessage(from_=SOLAPI_FROM, to=to.replace("-", "").strip(), text=text))
    except Exception as e:
        print("문자 발송 실패:", e)

def db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 환경변수가 필요합니다.")
    return psycopg.connect(url, row_factory=dict_row)

def init_db():
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS bookings(
        id BIGSERIAL PRIMARY KEY,
        program TEXT NOT NULL,
        date TEXT NOT NULL,
        people INTEGER NOT NULL,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '예약접수',
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS schedule(
        id BIGSERIAL PRIMARY KEY,
        program TEXT NOT NULL,
        date TEXT NOT NULL,
        capacity INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT '예약가능',
        UNIQUE(program,date)
    )""")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_type TEXT")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_amount INTEGER")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS total_amount INTEGER")

    # 기존에 야간체험 정원이 8명으로 저장된 날짜는 6명으로 자동 수정
    con.execute("""
        UPDATE schedule
        SET capacity = 6
        WHERE program = '야간체험'
          AND capacity = 8
    """)

    con.commit()
    con.close()

def get_schedule(program, dt):
    con = db()
    row = con.execute(
        "SELECT * FROM schedule WHERE program=%s AND date=%s",
        (program, dt),
    ).fetchone()
    con.close()
    return dict(row) if row else {
        "program": program,
        "date": dt,
        "capacity": PROGRAMS[program]["capacity"],
        "state": "예약가능",
    }

def booked_people(program, dt):
    con = db()
    row = con.execute("""
        SELECT COALESCE(SUM(people),0) AS total
        FROM bookings
        WHERE program=%s AND date=%s
          AND status IN ('예약접수','입금확인','예약확정')
    """, (program, dt)).fetchone()
    con.close()
    return int(row["total"])

def availability(program, dt):
    sch = get_schedule(program, dt)
    if sch["state"] != "예약가능":
        return 0, sch["state"]
    remaining = max(0, int(sch["capacity"]) - booked_people(program, dt))
    return (remaining, "예약가능") if remaining else (0, "예약마감")

def total_price(program, people):
    return PROGRAMS[program]["price"] if program == "선셋체험" else PROGRAMS[program]["price"] * people

@app.route("/hunter-main.png")
def image():
    return send_from_directory(app.root_path, "hunter-main-1.png")

@app.route("/parking.png")
def parking_image():
    return send_from_directory(app.root_path, "parking.png")

@app.route("/")
def home():
    return render_template(
        "index.html",
        programs=PROGRAMS,
        bank_name=BANK_NAME,
        bank_account=BANK_ACCOUNT,
        bank_holder=BANK_HOLDER,
    )

@app.route("/api/availability")
def api_availability():
    program = request.args.get("program")
    dt = request.args.get("date")
    if program not in PROGRAMS or not dt:
        return jsonify({"ok": False}), 400
    remaining, state = availability(program, dt)
    return jsonify({"ok": True, "remaining": remaining, "state": state})

@app.route("/api/calendar")
def api_calendar():
    program = request.args.get("program")
    month = request.args.get("month", "")
    if program not in PROGRAMS:
        return jsonify({"ok": False}), 400
    try:
        year, mon = map(int, month.split("-"))
        last_day = calendar.monthrange(year, mon)[1]
    except Exception:
        return jsonify({"ok": False}), 400
    days = []
    for day_num in range(1, last_day + 1):
        d = date(year, mon, day_num)
        remaining, state = availability(program, d.isoformat())
        if d < date.today():
            remaining, state = 0, "지난날짜"
        days.append({
            "date": d.isoformat(),
            "day": day_num,
            "remaining": remaining,
            "state": state,
        })
    return jsonify({"ok": True, "days": days})

@app.route("/reserve", methods=["POST"])
def reserve():
    program = request.form.get("program")
    dt = request.form.get("date")
    people = request.form.get("people", type=int)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").replace("-", "").strip()
    payment_type = request.form.get("payment_type")

    if (
        program not in PROGRAMS or not dt or not people or not name or not phone
        or payment_type not in ("5만원 선입금", "전액 입금")
    ):
        flash("예약정보와 입금 방법을 모두 입력해주세요.")
        return redirect(url_for("home") + "#reserve")

    remaining, state = availability(program, dt)
    if state != "예약가능" or people > remaining:
        flash(f"현재 예약 가능한 인원은 {remaining}명입니다.")
        return redirect(url_for("home") + "#reserve")

    total = total_price(program, people)
    payment_amount = 50000 if payment_type == "5만원 선입금" else total

    con = db()
    con.execute("""
        INSERT INTO bookings(
            program,date,people,name,phone,status,created_at,
            payment_type,payment_amount,total_amount
        )
        VALUES(%s,%s,%s,%s,%s,'예약접수',%s,%s,%s,%s)
    """, (
        program, dt, people, name, phone,
        datetime.now().isoformat(timespec="seconds"),
        payment_type, payment_amount, total
    ))
    con.commit()
    con.close()

    sms(
        ADMIN_PHONE,
        f"[헌터호 새 예약]\n{dt} {program}\n{name} / {people}명\n{phone}\n"
        f"{payment_type} {payment_amount:,}원"
    )
    sms(
        phone,
        f"[헌터호 예약접수]\n{dt} {program}\n입금금액 {payment_amount:,}원\n"
        f"{BANK_NAME} {BANK_ACCOUNT}\n예금주 {BANK_HOLDER}\n"
        "입금 시 날짜+예약자명으로 입금해주세요.\n"
        "입금 확인 후 예약확정 안내드립니다."
    )

    flash(
        f"예약 접수 완료! {BANK_NAME} {BANK_ACCOUNT} / "
        f"예금주 {BANK_HOLDER} / 입금금액 {payment_amount:,}원"
    )
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
def logout():
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

    reserved_rows = con.execute("""
        SELECT date, program, COALESCE(SUM(people),0) AS reserved
        FROM bookings
        WHERE status IN ('예약접수','입금확인','예약확정')
        GROUP BY date, program
        ORDER BY date ASC, program ASC
    """).fetchall()
    con.close()

    reserved_counts = {}
    for row in reserved_rows:
        reserved_counts[f"{row['date']}|{row['program']}"] = int(row["reserved"])

    schedule_info = {}
    for s in schedules:
        schedule_info[f"{s['date']}|{s['program']}"] = {
            "capacity": int(s["capacity"]),
            "state": s["state"],
        }

    return render_template(
        "admin.html",
        bookings=bookings,
        schedules=schedules,
        programs=PROGRAMS,
        reserved_counts=reserved_counts,
        schedule_info=schedule_info,
    )

@app.route("/admin/booking/<int:bid>/<status>", methods=["POST"])
def set_status(bid, status):
    if not require_admin():
        return redirect(url_for("admin_login"))
    if status not in ("예약접수", "입금확인", "예약확정", "취소"):
        return "invalid", 400

    con = db()
    booking = con.execute("SELECT * FROM bookings WHERE id=%s", (bid,)).fetchone()
    if not booking:
        con.close()
        return redirect(url_for("admin"))

    old_status = booking["status"]
    con.execute("UPDATE bookings SET status=%s WHERE id=%s", (status, bid))
    con.commit()
    con.close()

    if status != old_status:
        if status == "입금확인":
            sms(
                booking["phone"],
                f"[헌터호 입금확인]\n{booking['date']} {booking['program']}\n입금이 확인되었습니다."
            )
        elif status == "예약확정":
            sms(
                booking["phone"],
                f"[헌터호 예약확정]\n{booking['date']} {booking['program']}\n예약이 확정되었습니다.\n감사합니다."
            )
        elif status == "취소":
            sms(
                booking["phone"],
                f"[헌터호 예약취소]\n{booking['date']} {booking['program']}\n예약이 취소되었습니다."
            )

    return redirect(url_for("admin"))

@app.route("/admin/schedule", methods=["POST"])
def set_schedule():
    if not require_admin():
        return redirect(url_for("admin_login"))

    program = request.form.get("program")
    dt = request.form.get("date")
    capacity = request.form.get("capacity", type=int)
    state = request.form.get("state")

    if (
        program not in PROGRAMS or not dt or capacity is None
        or state not in ("예약가능", "예약마감", "운항없음")
    ):
        flash("운항 설정값을 확인해주세요.")
        return redirect(url_for("admin"))

    con = db()
    con.execute("""
        INSERT INTO schedule(program,date,capacity,state)
        VALUES(%s,%s,%s,%s)
        ON CONFLICT(program,date)
        DO UPDATE SET capacity=excluded.capacity,state=excluded.state
    """, (program, dt, capacity, state))
    con.commit()
    con.close()

    flash("운항 설정이 저장되었습니다.")
    return redirect(url_for("admin"))

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
