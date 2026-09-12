from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
import os
import calendar
from datetime import datetime, date
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from solapi import SolapiMessageService
from solapi.model import RequestMessage

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-before-deploy")

ADMIN_ID = os.environ.get("ADMIN_USER", "admin")
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
    con.execute("""
        CREATE TABLE IF NOT EXISTS passengers (
            id BIGSERIAL PRIMARY KEY,
            boarding_date TEXT NOT NULL,
            name TEXT NOT NULL,
            birth_date TEXT NOT NULL,
            phone TEXT NOT NULL,
            emergency_phone TEXT NOT NULL,
            agreed BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TEXT NOT NULL
        )
    """)
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_type TEXT")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_amount INTEGER")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS total_amount INTEGER")
    con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS admin_note TEXT")
    con.execute("ALTER TABLE passengers ADD COLUMN IF NOT EXISTS booking_id BIGINT")
    con.execute("ALTER TABLE passengers ADD COLUMN IF NOT EXISTS submission_id TEXT")
    con.execute("""
        UPDATE schedule
        SET capacity = 6
        WHERE program = '야간체험' AND capacity = 8
    """)
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


def mask_public_name(name):
    name = (name or "").strip()
    if not name:
        return "*"
    if len(name) == 1:
        return "*"
    return name[:-1] + "*"


@app.route("/hunter-main.png")
def hunter_main():
    return send_from_directory(app.root_path, "hunter-main-1.png")


@app.route("/parking.png")
def parking():
    return send_from_directory(app.root_path, "parking.png")


@app.route("/")
def home():
    return render_template(
        "index.html",
        programs=PROGRAMS,
        bank_name=BANK_NAME,
        bank_account=BANK_ACCOUNT,
        bank_holder=BANK_HOLDER
    )


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


@app.route("/api/public-bookings")
def api_public_bookings():
    program = request.args.get("program", "").strip()
    dt = request.args.get("date", "").strip()

    if program not in PROGRAMS or not dt:
        return jsonify({"bookings": []})

    con = db()
    rows = con.execute("""
        SELECT name, people, status
        FROM bookings
        WHERE program = %s
          AND date = %s
          AND status != '취소'
        ORDER BY id ASC
    """, (program, dt)).fetchall()
    con.close()

    result = []
    for row in rows:
        if row["status"] == "예약접수":
            public_status = "입금 전"
        elif row["status"] in ("입금확인", "예약확정"):
            public_status = "예약 완료"
        else:
            continue

        result.append({
            "name": mask_public_name(row["name"]),
            "people": int(row["people"]),
            "status": public_status
        })

    return jsonify({"bookings": result})


@app.route("/reserve", methods=["POST"])
def reserve():
    program = request.form.get("program")
    dt = request.form.get("date")
    people = request.form.get("people", type=int)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").replace("-", "").strip()
    if program not in PROGRAMS or not dt or not people or not name or not phone:
        flash("예약정보를 모두 입력해주세요.")
        return redirect(url_for("home") + "#reserve")
    remaining, state = availability(program, dt)
    if state != "예약가능" or people > remaining:
        flash(f"현재 예약 가능한 인원은 {remaining}명입니다.")
        return redirect(url_for("home") + "#reserve")
    total = total_price(program, people)
    payment_type = "전액 입금"
    payment_amount = total
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
    sms(ADMIN_PHONE, f"[헌터호 새 예약]\n{dt} {program}\n{name} / {people}명\n{phone}\n전액 입금 / {total:,}원")
    sms(phone, f"[헌터호 예약접수]\n{dt} {program}\n{name} / {people}명\n입금금액 {total:,}원\n{BANK_NAME} {BANK_ACCOUNT}\n예금주 {BANK_HOLDER}\n입금 시 날짜+예약자명으로 입금해주세요.\n입금 확인 후 예약확정 안내드립니다.")
    flash(f"예약 접수 완료! {BANK_NAME} {BANK_ACCOUNT} / 예금주 {BANK_HOLDER} / 입금금액 {total:,}원")
    return redirect(url_for("home") + "#reserve")


@app.route("/passenger", methods=["GET", "POST"])
def passenger():
    if request.method == "GET":
        booking_id = request.args.get("booking_id", type=int)
        booking = None
        prefill_date = request.args.get("date", "").strip()

        if booking_id:
            con = db()
            booking = con.execute("""
                SELECT *
                FROM bookings
                WHERE id = %s AND status != '취소'
            """, (booking_id,)).fetchone()
            con.close()

            if booking:
                prefill_date = booking["date"]

        return render_template(
            "passenger.html",
            prefill_date=prefill_date,
            booking=booking,
            form_token=str(uuid4())
        )

    boarding_date = request.form.get("boarding_date", "").strip()
    booking_id = request.form.get("booking_id", type=int)
    submission_token = request.form.get("submission_token", "").strip() or str(uuid4())

    names = request.form.getlist("name[]")
    birth_dates = request.form.getlist("birth_date[]")
    phones = request.form.getlist("phone[]")
    emergency_phones = request.form.getlist("emergency_phone[]")
    agree = request.form.get("agree") == "yes"

    redirect_args = {}
    if booking_id:
        redirect_args["booking_id"] = booking_id
    elif boarding_date:
        redirect_args["date"] = boarding_date

    if not boarding_date or not agree:
        flash("승선일을 선택하고 개인정보 수집에 동의해주세요.")
        return redirect(url_for("passenger", **redirect_args))

    try:
        datetime.strptime(boarding_date, "%Y-%m-%d")
    except Exception:
        flash("승선일을 다시 확인해주세요.")
        return redirect(url_for("passenger", **redirect_args))

    booking = None
    if booking_id:
        con = db()
        booking = con.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s AND status != '취소'
        """, (booking_id,)).fetchone()
        con.close()

        if not booking:
            flash("예약정보를 찾을 수 없습니다. 관리자에게 문의해주세요.")
            return redirect(url_for("passenger"))

        if boarding_date != booking["date"]:
            flash("예약된 승선일과 입력된 승선일이 다릅니다.")
            return redirect(url_for("passenger", booking_id=booking_id))

    rows = []
    max_len = max(len(names), len(birth_dates), len(phones), len(emergency_phones))

    for i in range(max_len):
        name = names[i].strip() if i < len(names) else ""
        birth_date = birth_dates[i].replace("-", "").replace(".", "").strip() if i < len(birth_dates) else ""
        phone = phones[i].replace("-", "").strip() if i < len(phones) else ""
        emergency_phone = emergency_phones[i].replace("-", "").strip() if i < len(emergency_phones) else ""

        if not any([name, birth_date, phone, emergency_phone]):
            continue

        if not all([name, birth_date, phone, emergency_phone]):
            flash(f"{i + 1}번 승선자 정보를 모두 입력해주세요.")
            return redirect(url_for("passenger", **redirect_args))

        if len(birth_date) != 8 or not birth_date.isdigit():
            flash(f"{i + 1}번 승선자 생년월일은 8자리 숫자로 입력해주세요.")
            return redirect(url_for("passenger", **redirect_args))

        if not phone.isdigit() or len(phone) < 10:
            flash(f"{i + 1}번 승선자 연락처를 다시 확인해주세요.")
            return redirect(url_for("passenger", **redirect_args))

        if not emergency_phone.isdigit() or len(emergency_phone) < 10:
            flash(f"{i + 1}번 승선자 비상연락처를 다시 확인해주세요.")
            return redirect(url_for("passenger", **redirect_args))

        rows.append((name, birth_date, phone, emergency_phone))

    if not rows:
        flash("승선자를 1명 이상 입력해주세요.")
        return redirect(url_for("passenger", **redirect_args))

    if booking and len(rows) != int(booking["people"]):
        flash(
            f"예약 인원은 {booking['people']}명입니다. "
            f"현재 {len(rows)}명 입력되어 있습니다. 승선자 전원을 입력해주세요."
        )
        return redirect(url_for("passenger", booking_id=booking_id))

    con = db()
    try:
        already_saved = con.execute("""
            SELECT COUNT(*) AS cnt
            FROM passengers
            WHERE submission_id = %s
        """, (submission_token,)).fetchone()

        if int(already_saved["cnt"] or 0) > 0:
            saved_count = int(already_saved["cnt"])
            con.close()
            return render_template(
                "passenger_done.html",
                name=rows[0][0],
                count=saved_count,
                boarding_date=boarding_date,
                booking=booking
            )

        if booking_id:
            con.execute("DELETE FROM passengers WHERE booking_id = %s", (booking_id,))

        created_at = datetime.now().isoformat(timespec="seconds")

        for name, birth_date, phone, emergency_phone in rows:
            con.execute("""
                INSERT INTO passengers(
                    boarding_date,name,birth_date,phone,emergency_phone,
                    agreed,created_at,booking_id,submission_id
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                boarding_date, name, birth_date, phone, emergency_phone,
                True, created_at, booking_id, submission_token
            ))

        verify = con.execute("""
            SELECT COUNT(*) AS cnt
            FROM passengers
            WHERE submission_id = %s
        """, (submission_token,)).fetchone()

        saved_count = int(verify["cnt"] or 0)

        if saved_count != len(rows):
            raise RuntimeError(
                f"승선명부 저장 검증 실패: 입력 {len(rows)}명 / 저장 {saved_count}명"
            )

        con.commit()

    except Exception as e:
        con.rollback()
        print("승선명부 저장 실패:", e)
        flash("승선명부 저장 중 오류가 발생했습니다. 다시 시도해주세요.")
        return redirect(url_for("passenger", **redirect_args))
    finally:
        if not con.closed:
            con.close()

    return render_template(
        "passenger_done.html",
        name=rows[0][0],
        count=saved_count,
        boarding_date=boarding_date,
        booking=booking
    )


@app.route("/admin/passengers")
def admin_passengers():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    selected_date = request.args.get("date", "").strip()
    con = db()

    base_sql = """
        SELECT
            p.*,
            b.name AS booking_name,
            b.program AS booking_program,
            b.people AS booking_people
        FROM passengers p
        LEFT JOIN bookings b ON b.id = p.booking_id
    """

    if selected_date:
        passengers = con.execute(
            base_sql + """
            WHERE p.boarding_date = %s
            ORDER BY p.id ASC
            """,
            (selected_date,)
        ).fetchall()
    else:
        passengers = con.execute(
            base_sql + """
            ORDER BY p.boarding_date DESC, p.id ASC
            """
        ).fetchall()

    con.close()

    return render_template(
        "admin_passengers.html",
        passengers=passengers,
        selected_date=selected_date
    )


@app.route("/admin/passengers/<int:pid>/delete", methods=["POST"])
def delete_passenger(pid):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    selected_date = request.form.get("date", "").strip()
    con = db()
    con.execute("DELETE FROM passengers WHERE id = %s", (pid,))
    con.commit()
    con.close()
    flash("승선명부가 삭제되었습니다.")
    if selected_date:
        return redirect(url_for("admin_passengers", date=selected_date))
    return redirect(url_for("admin_passengers"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user_id = (request.form.get("id") or request.form.get("username") or request.form.get("user_id") or "").strip()
        password = (request.form.get("password") or request.form.get("pw") or "").strip()
        if user_id == ADMIN_ID and password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("아이디 또는 비밀번호가 틀렸습니다.")
    return render_template("login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin/manual-booking", methods=["POST"])
def manual_booking():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    program = request.form.get("program", "").strip()
    dt = request.form.get("date", "").strip()
    people = request.form.get("people", type=int)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").replace("-", "").strip()
    payment_type = request.form.get("payment_type", "전액 입금").strip()
    send_message = request.form.get("send_message") == "yes"
    admin_note = request.form.get("admin_note", "").strip()
    if program not in PROGRAMS or not dt or not people or people < 1 or not name or not phone:
        flash("수동 예약 정보를 모두 확인해주세요.")
        return redirect(url_for("admin") + "#manualBooking")
    if not phone.isdigit() or len(phone) < 10:
        flash("예약자 전화번호를 다시 확인해주세요.")
        return redirect(url_for("admin") + "#manualBooking")
    if payment_type not in ("전액 입금", "현장결제"):
        payment_type = "전액 입금"
    remaining, state = availability(program, dt)
    if state != "예약가능":
        flash(f"{dt} {program}은 현재 {state} 상태입니다.")
        return redirect(url_for("admin") + "#manualBooking")
    if people > remaining:
        flash(f"수동 예약 등록 불가: 현재 잔여 인원은 {remaining}명입니다.")
        return redirect(url_for("admin") + "#manualBooking")
    total = total_price(program, people)
    payment_amount = 0 if payment_type == "현장결제" else total
    con = db()
    try:
        lock_key = f"manual|{program}|{dt}|{people}|{name}|{phone}"
        con.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (lock_key,))
        schedule = con.execute("SELECT * FROM schedule WHERE program = %s AND date = %s", (program, dt)).fetchone()
        if schedule:
            if schedule["state"] != "예약가능":
                con.rollback()
                flash(f"{dt} {program}은 현재 {schedule['state']} 상태입니다.")
                return redirect(url_for("admin") + "#manualBooking")
            capacity = int(schedule["capacity"])
        else:
            capacity = int(PROGRAMS[program]["capacity"])
        booked = con.execute("SELECT COALESCE(SUM(people), 0) AS total FROM bookings WHERE program = %s AND date = %s AND status != '취소'", (program, dt)).fetchone()
        remaining_locked = max(0, capacity - int(booked["total"] or 0))
        if people > remaining_locked:
            con.rollback()
            flash(f"수동 예약 등록 불가: 현재 잔여 인원은 {remaining_locked}명입니다.")
            return redirect(url_for("admin") + "#manualBooking")
        con.execute("""
            INSERT INTO bookings(program,date,people,name,phone,status,created_at,payment_type,payment_amount,total_amount,admin_note)
            VALUES(%s,%s,%s,%s,%s,'예약접수',%s,%s,%s,%s,%s)
        """, (program, dt, people, name, phone, datetime.now().isoformat(timespec="seconds"), payment_type, payment_amount, total, admin_note))
        con.commit()
    except Exception as e:
        con.rollback()
        print("수동 예약 저장 실패:", e)
        flash("수동 예약 저장 중 오류가 발생했습니다.")
        return redirect(url_for("admin") + "#manualBooking")
    finally:
        con.close()
    if send_message:
        if payment_type == "현장결제":
            sms(phone, f"[헌터호 예약접수]\n{dt} {program}\n{name} / {people}명\n총 이용금액 {total:,}원\n결제방법 현장결제\n예약확정 후 승선명부 작성 안내드립니다.")
        else:
            sms(phone, f"[헌터호 예약접수]\n{dt} {program}\n{name} / {people}명\n입금금액 {total:,}원\n{BANK_NAME} {BANK_ACCOUNT}\n예금주 {BANK_HOLDER}\n입금 시 날짜+예약자명으로 입금해주세요.\n입금 확인 후 예약확정 안내드립니다.")
    flash(f"수동 예약 등록 완료: {dt} {program} / {name} / {people}명")
    return redirect(url_for("admin"))


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    con = db()
    bookings = con.execute("SELECT * FROM bookings WHERE status != '취소' ORDER BY date ASC, id DESC").fetchall()
    schedules = con.execute("SELECT * FROM schedule ORDER BY date ASC").fetchall()
    reserved_rows = con.execute("""
        SELECT date, program, COALESCE(SUM(people), 0) AS total
        FROM bookings WHERE status != '취소'
        GROUP BY date, program
    """).fetchall()

    passenger_count_rows = con.execute("""
        SELECT booking_id, COUNT(*) AS total
        FROM passengers
        WHERE booking_id IS NOT NULL
        GROUP BY booking_id
    """).fetchall()

    con.close()
    reserved_counts = {}
    for row in reserved_rows:
        reserved_counts[f"{row['date']}|{row['program']}"] = int(row["total"] or 0)
    schedule_info = {}
    for row in schedules:
        schedule_info[f"{row['date']}|{row['program']}"] = {
            "capacity": int(row["capacity"]),
            "state": row["state"]
        }

    passenger_counts = {
        int(row["booking_id"]): int(row["total"] or 0)
        for row in passenger_count_rows
    }

    return render_template(
        "admin.html",
        bookings=bookings,
        schedules=schedules,
        programs=PROGRAMS,
        reserved_counts=reserved_counts,
        schedule_info=schedule_info,
        passenger_counts=passenger_counts
    )


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
        phone = booking["phone"]
        dt = booking["date"]
        program = booking["program"]
        if status == "입금확인":
            sms(phone, f"[헌터호 입금확인]\n{dt} {program}\n입금이 확인되었습니다.")
        elif status == "예약확정":
            manifest_url = f"https://hunter-booking.onrender.com/passenger?booking_id={bid}"
            sms(phone, f"[헌터호 예약확정]\n{dt} {program}\n예약이 확정되었습니다.\n아래 링크에서 승선자 전원의 승선명부를 작성해주세요.\n{manifest_url}\n감사합니다.")
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
        INSERT INTO schedule(program,date,capacity,state)
        VALUES(%s,%s,%s,%s)
        ON CONFLICT(program,date)
        DO UPDATE SET capacity = EXCLUDED.capacity, state = EXCLUDED.state
    """, (program, dt, capacity, state))
    con.commit()
    con.close()
    flash("운항 일정이 저장되었습니다.")
    return redirect(url_for("admin"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
