# rebuild trigger
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from solapi import SolapiMessageService
from solapi.model import RequestMessage

KST = ZoneInfo("Asia/Seoul")

SOLAPI_API_KEY = os.environ.get("SOLAPI_API_KEY")
SOLAPI_API_SECRET = os.environ.get("SOLAPI_API_SECRET")
SOLAPI_FROM = os.environ.get("SOLAPI_FROM")

PROGRAM_GUIDE = {
    "주간체험": "출항시간 06:00\n장소 거북섬 마리나\n무료주차 거북섬로 111",
    "야간체험": "출항시간 17:00\n장소 거북섬 마리나\n무료주차 거북섬로 111",
    "선셋체험": "출항시간은 일몰시간에 맞춰 안내드립니다.\n장소 거북섬 마리나\n무료주차 거북섬로 111",
}

def db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 환경변수가 필요합니다.")
    return psycopg.connect(url, row_factory=dict_row)

def send_sms(phone, text):
    if not all([SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_FROM, phone]):
        raise RuntimeError("SOLAPI 환경변수 설정이 누락되었습니다.")

    service = SolapiMessageService(
        api_key=SOLAPI_API_KEY,
        api_secret=SOLAPI_API_SECRET,
    )
    service.send(
        RequestMessage(
            from_=SOLAPI_FROM,
            to=phone.replace("-", "").strip(),
            text=text,
        )
    )

def main():
    tomorrow = (datetime.now(KST).date() + timedelta(days=1)).isoformat()

    con = db()

    # 중복 발송 방지용 컬럼. 처음 실행할 때 자동 생성됩니다.
    con.execute(
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_sent_at TEXT"
    )
    con.commit()

    bookings = con.execute("""
        SELECT id, program, date, people, name, phone
        FROM bookings
        WHERE date=%s
          AND status='예약확정'
          AND reminder_sent_at IS NULL
        ORDER BY id ASC
    """, (tomorrow,)).fetchall()

    print(f"{tomorrow} 전날 안내 대상: {len(bookings)}건")

    for b in bookings:
        guide = PROGRAM_GUIDE.get(
            b["program"],
            "장소 거북섬 마리나\n무료주차 거북섬로 111"
        )

        text = (
            "[헌터호 출항 안내]\n"
            f"{b['name']}님, 내일 {b['date']} {b['program']} 예약입니다.\n"
            f"{guide}\n"
            f"예약인원 {b['people']}명\n"
            "기상 및 현장 상황에 따라 운항시간이 변동될 수 있습니다.\n"
            "안전하게 오세요."
        )

        try:
            send_sms(b["phone"], text)

            con.execute(
                "UPDATE bookings SET reminder_sent_at=%s WHERE id=%s AND reminder_sent_at IS NULL",
                (datetime.now(KST).isoformat(timespec="seconds"), b["id"]),
            )
            con.commit()
            print(f"발송 성공: 예약 #{b['id']} / {b['name']}")

        except Exception as e:
            con.rollback()
            print(f"발송 실패: 예약 #{b['id']} / {b['name']} / {e}")

    con.close()

if __name__ == "__main__":
    main()
