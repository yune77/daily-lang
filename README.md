# 매일 외국어 학습봇 (daily-lang)

매일 오후 9시(한국시간), 미드·중드 스타일 관용표현을 텔레그램으로 보내주는 1인용 학습 시스템입니다.

- **영어**: 구어체·관용구 중심 (OPIc IH 이상 목표), 하루 5개
- **중국어**: HSK 3~4급 회화 표현, 병음 포함, 하루 5개
- **레벨**: 초급 → 중급 → 고급, 각 레벨을 다 배우면 자동 레벨업
- **웹 학습 페이지**: 발음 듣기(원어민급 TTS), 쉐도잉 모드(느린 재생·반복), 간격 반복 복습 퀴즈(1·3·7·21일), 과거 학습 아카이브, 진도율
- **완전 무료**: GitHub Actions(발송 스케줄) + GitHub Pages(웹) + Telegram Bot API

## 구조

| 경로 | 역할 |
|---|---|
| `.github/workflows/daily.yml` | 매일 12:00 UTC(21:00 KST) 자동 실행 |
| `scripts/daily.py` | 표현 선정, 음성 생성, 아카이브 갱신, 텔레그램 발송 |
| `data/en_1~3.json`, `data/zh_1~3.json` | 레벨별 표현 DB |
| `docs/` | GitHub Pages 학습 웹페이지 |
| `docs/data/state.json` | 진도 상태 |
| `docs/data/archive.json` | 날짜별 학습 아카이브 |
| `docs/audio/` | 자동 생성된 발음 MP3 |

## 필요한 Secrets (Settings → Secrets and variables → Actions)

- `TELEGRAM_BOT_TOKEN` — BotFather가 발급한 봇 토큰
- `TELEGRAM_CHAT_ID` — 내 텔레그램 chat id

## 수동 실행(테스트)

Actions 탭 → Daily Lesson → Run workflow
