# 헌터호 Render 배포판 V11

이 버전은 Render Web Service + PostgreSQL용입니다.

## 기능
- 고객 예약 신청
- 날짜/체험별 잔여인원 자동 계산
- 관리자 로그인
- 예약확정/취소
- 날짜별 정원, 예약마감, 운항없음 설정
- PostgreSQL에 예약 영구 저장

## Render 설정
`render.yaml`이 포함되어 있습니다.

필수 비밀값:
- ADMIN_PASSWORD: 관리자 비밀번호
- SECRET_KEY: render.yaml에서 자동 생성
- DATABASE_URL: Render Postgres에서 자동 연결

## 요금
- 주간체험: 1인 100,000원
- 야간체험: 1인 80,000원
- 선셋체험: 4인 기준 250,000원
- 예약금 없음

## 주의
실제 공개 전 개인정보 처리방침, 이용약관, 취소/환불 기준 등을 운영 형태에 맞게 추가하세요.
문자/알림톡은 아직 연결하지 않았습니다.
