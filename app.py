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

ADMIN_ID = os.environ.get("ADMIN_ID", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "hunter1234")
SOLAPI_API_KEY = os.environ.get("SOLAPI_API_KEY")
SOLAPI_API_SECRET = os.environ.get("SOLAPI_API_SECRET")
SOLAPI_FROM = os.environ.get("SOLAPI_FROM")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE")

BANK_NAME = "토스뱅크"
BANK_ACCOUNT = "1002-3983-0407"
BANK_HOLDER = "김경환"

PROGRAMS = {
    "주간체험": {"price": 100000, "capacity": 8},
    "야간체험": {"price": 80000, "capacity": 6},
    "선셋체험": {"price": 250000, "capacity": 4},
}

def db():
    return psycopg.connect(os.environ.get("DATABASE_URL"), row_factory=dict_row)

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
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_type TEXT")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_amount INTEGER")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS total_amount INTEGER")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS admin_note TEXT")
    con.execute("UPDATE schedule SET capacity = 6 WHERE program = '야간체험' AND capacity = 8")
    con.commit()
    con.close()

def sms(to, text):
    if not all([SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_FROM, to]):
        print("SOLAPI 문자 설정 누락")
        return
    try:
        service = SolapiMessageService(api_key=SOLAPI_API_KEY, api_secret=SOLAPI_API_SECRET)
        service.send(RequestMessage(from_=SOLAPI_FROM, to=to.replace("-", "").strip(), text=text))
    except Exception as e:
        print("문자 발송 실패:", e)

def get_schedule(program, dt):
    con = db()
    row = con.execute("SELECT * FROM schedule WHERE program = %s AND date = %s", (program, dt)).fetchone()
    con.close()
    return row

def booked_people(program, dt):
    con = db()
    row = con.execute("""
        SELECT COALESCE(SUM(people), 0) AS total
        FROM bookings
        WHERE program = %s AND date = %s AND status != '취소'
    """, (program, dt)).fetchone()
    con.close()
    return int(row["total"] or 0)

def availability(program, dt):
    if program not in PROGRAMS:
        return 0, "예약불가"
    try:
        target = datetime.strptime(dt, "%Y-%m-%d").date()
    except Exception:
        return 0, "예약불가"
    if target < date.today():
        return 0, "지난날짜"
    schedule = get_schedule(program, dt)
    if schedule:
        state = schedule["state"]
        capacity = int(schedule["capacity"])
    else:
        state = "예약가능"
        capacity = PROGRAMS[program]["capacity"]
    if state != "예약가능":
        return 0, state
    remaining = max(0, capacity - booked_people(program, dt))
    if remaining <= 0:
        return 0, "예약마감"
    return remaining, "예약가능"

def total_price(program, people):
    if program == "선셋체험":
        return 250000
    return PROGRAMS[program]["price"] * people

@app.route("/hunter-main.png")
def hunter_main():
    return send_from_directory("static", "hunter-main.png")

@app.route("/parking.png")
def parking():
    return send_from_directory("static", "parking.png")

@app.route("/")
def home():
    return render_template("index.html", programs=PROGRAMS, bank_name=BANK_NAME, bank_account=BANK_ACCOUNT, bank_holder=BANK_HOLDER)

@app.route("/api/availability")
def api_availability():
    remaining, state = availability(request.args.get("program"), request.args.get("date"))
    return jsonify({"remaining": remaining, "state": state})

@app.route("/api/calendar")
def api_calendar():
    program = request.args.get("program")
    month = request.args.get("month")
    if program not in PROGRAMS:
        return jsonify({"days": []})
    try:
        year, mon = map(int, month.split("-"))
    except Exception:
        today = date.today()
        year, mon = today.year, today.month
    last_day = calendar.monthrange(year, mon)[1]
    days = []
    for day_num in range(1, last_day + 1):
        dt = f"{year:04d}-{mon:02d}-{day_num:02d}"
        remaining, state = availability(program, dt)
        days.append({"day": day_num, "date": dt, "remaining": remaining, "state": state})
    return jsonify({"days": days})

@app.route("/reserve", methods=["POST"])
def reserve():
    program = request.form.get("program")
    dt = request.form.get("date")
    people = request.form.get("people", type=int)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").replace("-", "").strip()
    payment_type = request.form.get("payment_type")

    if program not in PROGRAMS or not dt or not people or not name or not phone or payment_type not in ("현장결제", "전액 입금"):
        flash("예약정보와 결제 방법을 모두 입력해주세요.")
        return redirect(url_for("home") + "#reserve")

    remaining, state = availability(program, dt)
    if state != "예약가능" or people > remaining:
        flash(f"현재 예약 가능한 인원은 {remaining}명입니다.")
        return redirect(url_for("home") + "#reserve")

    total = total_price(program, people)
    payment_amount = 0 if payment_type == "현장결제" else total
    con = db()
    try:
        lock_key = f"{program}|{dt}|{people}|{name}|{phone}"
        con.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (lock_key,))
        duplicate = con.execute("""
            SELECT id FROM bookings
            WHERE program = %s AND date = %s AND people = %s AND name = %s AND phone = %s
              AND status IN ('예약접수','입금확인','예약확정')
              AND created_at::timestamp >= NOW() - INTERVAL '30 seconds'
            ORDER BY id DESC LIMIT 1
        """, (program, dt, people, name, phone)).fetchone()
        if duplicate:
            con.rollback()
            flash("이미 예약이 접수되었습니다.")
            return redirect(url_for("home") + "#reserve")
        con.execute("""
            INSERT INTO bookings(program,date,people,name,phone,status,created_at,payment_type,payment_amount,total_amount)
            VALUES(%s,%s,%s,%s,%s,'예약접수',%s,%s,%s,%s)
        """, (program, dt, people, name, phone, datetime.now().isoformat(timespec="seconds"), payment_type, payment_amount, total))
        con.commit()
    except Exception as e:
        con.rollback()
        print("예약 저장 실패:", e)
        flash("예약 처리 중 오류가 발생했습니다. 다시 시도해주세요.")
        return redirect(url_for("home") + "#reserve")
    finally:
        con.close()

    admin_payment = f"현장결제 / {total:,}원" if payment_type == "현장결제" else f"전액 입금 / {total:,}원"
    sms(ADMIN_PHONE, f"[헌터호 새 예약]\n{dt} {program}\n{name} / {people}명\n{phone}\n{admin_payment}")

    if payment_type == "현장결제":
        sms(phone, f"[헌터호 예약접수]\n{dt} {program}\n{name} / {people}명\n결제방법: 현장결제\n이용금액: {total:,}원\n예약이 정상 접수되었습니다.\n예약확정 안내를 기다려주세요.")
        flash(f"예약 접수 완료! 현장결제 {total:,}원")
    else:
        sms(phone, f"[헌터호 예약접수]\n{dt} {program}\n입금금액 {total:,}원\n{BANK_NAME} {BANK_ACCOUNT}\n예금주 {BANK_HOLDER}\n입금 시 날짜+예약자명으로 입금해주세요.\n입금 확인 후 예약확정 안내드립니다.")
        flash(f"예약 접수 완료! {BANK_NAME} {BANK_ACCOUNT} / 예금주 {BANK_HOLDER} / 입금금액 {total:,}원")
    return redirect(url_for("home") + "#reserve")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("id") == ADMIN_ID and request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("아이디 또는 비밀번호가 틀렸습니다.")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    con = db()
    bookings = con.execute("SELECT * FROM bookings WHERE status != '취소' ORDER BY date ASC, id DESC").fetchall()
    schedules = con.execute("SELECT * FROM schedule ORDER BY date ASC").fetchall()
    con.close()
    return render_template("admin.html", bookings=bookings, schedules=schedules, programs=PROGRAMS)

@app.route("/admin/booking/<int:bid>/<status>", methods=["POST"])
def set_status(bid, status):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    if status not in ("예약접수", "입금확인", "예약확정", "취소"):
        return redirect(url_for("admin"))
    con = db()
    booking = con.execute("SELECT * FROM bookings WHERE id = %s", (bid,)).fetchone()
    if not booking:
        con.close()
        return redirect(url_for("admin"))
    old_status = booking["status"]
    con.execute("UPDATE bookings SET status = %s WHERE id = %s", (status, bid))
    con.commit()
    con.close()
    if status != old_status:
        phone, dt, program = booking["phone"], booking["date"], booking["program"]
        if status == "입금확인":
            sms(phone, f"[헌터호 입금확인]\n{dt} {program}\n입금이 확인되었습니다.")
        elif status == "예약확정":
            sms(phone, f"[헌터호 예약확정]\n{dt} {program}\n예약이 확정되었습니다.\n감사합니다.")
        elif status == "취소":
            sms(phone, f"[헌터호 예약취소]\n{dt} {program}\n예약이 취소되었습니다.")
    return redirect(url_for("admin"))

@app.route("/admin/booking/<int:bid>/note", methods=["POST"])
def booking_note(bid):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    con = db()
    con.execute("UPDATE bookings SET admin_note = %s WHERE id = %s", (request.form.get("admin_note", ""), bid))
    con.commit()
    con.close()
    return redirect(url_for("admin"))

@app.route("/admin/schedule", methods=["POST"])
def admin_schedule():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    program = request.form.get("program")
    dt = request.form.get("date")
    capacity = request.form.get("capacity", type=int)
    state = request.form.get("state")
    if program not in PROGRAMS or not dt or not capacity or state not in ("예약가능", "예약마감", "운항없음"):
        flash("운항 정보를 확인해주세요.")
        return redirect(url_for("admin"))
    con = db()
    con.execute("""
        INSERT INTO schedule(program,date,capacity,state) VALUES(%s,%s,%s,%s)
        ON CONFLICT(program,date) DO UPDATE SET capacity=EXCLUDED.capacity, state=EXCLUDED.state
    """, (program, dt, capacity, state))
    con.commit()
    con.close()
    flash("운항 일정이 저장되었습니다.")
    return redirect(url_for("admin"))

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
