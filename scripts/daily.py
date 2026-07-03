#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
매일 오후 9시(KST) 실행되는 학습 발송 스크립트.
1) 오늘의 표현 선정 (영어 5 + 중국어 5, 레벨별 순차 진행)
2) 음성(MP3) 생성 — edge-tts (실패해도 발송은 계속, 웹에서 브라우저 음성으로 대체)
3) 웹 아카이브(docs/data/archive.json)와 진도(state.json) 갱신
4) 텔레그램 요약 메시지 + 버튼 2개(웹에서 보기 / Claude 회화 연습) 발송
"""
import json
import os
import subprocess
import sys
import datetime
import urllib.request
import urllib.parse
import pathlib

KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
TODAY = NOW.date().isoformat()

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WEB_DATA = ROOT / "docs" / "data"
AUDIO_DIR = ROOT / "docs" / "audio"

PER_DAY = 5  # 언어별 하루 새 표현 개수
LEVEL_NAMES = {1: "초급", 2: "중급", 3: "고급"}
LANG_LABEL = {"en": "영어", "zh": "중국어"}
LANG_FLAG = {"en": "\U0001F1FA\U0001F1F8", "zh": "\U0001F1E8\U0001F1F3"}
VOICES = {"en": "en-US-JennyNeural", "zh": "zh-CN-XiaoxiaoNeural"}

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FORCE = os.environ.get("FORCE_RESEND", "false").lower() == "true"

# GitHub Pages 주소 자동 계산 (yune77/daily-lang -> https://yune77.github.io/daily-lang)
repo = os.environ.get("GITHUB_REPOSITORY", "")
if "/" in repo:
    owner, name = repo.split("/", 1)
    SITE = f"https://{owner}.github.io/{name}"
else:
    SITE = os.environ.get("SITE_URL", "").rstrip("/")


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def load_level(lang, level):
    """레벨 파일이 없으면 None (해당 레벨 없음)"""
    p = DATA / f"{lang}_{level}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def pick_today(lang, state):
    """오늘 배울 표현을 뽑고 진도를 갱신. (items, levelup, all_done) 반환"""
    st = state[lang]
    if st.get("done"):
        return [], False, True
    items = load_level(lang, st["level"])
    if items is None:
        st["done"] = True
        return [], False, True
    picked = items[st["index"]: st["index"] + PER_DAY]
    st["index"] += len(picked)
    st["learned"] = st.get("learned", 0) + len(picked)
    st["total"] = len(items)
    levelup = False
    if st["index"] >= len(items):
        if load_level(lang, st["level"] + 1) is not None:
            st["level"] += 1
            st["index"] = 0
            st["total"] = len(load_level(lang, st["level"]))
            levelup = True
        else:
            st["done"] = True
    return picked, levelup, st.get("done", False)


def make_audio(lang, items):
    """표현/예문 MP3 생성. 실패해도 전체 발송은 막지 않는다."""
    outdir = AUDIO_DIR / lang
    outdir.mkdir(parents=True, exist_ok=True)
    voice = VOICES[lang]
    for it in items:
        for suffix, text in (("", it["expression"]), ("x", it["example"])):
            out = outdir / f"{it['id']}{suffix}.mp3"
            if out.exists():
                continue
            try:
                subprocess.run(
                    ["edge-tts", "--voice", voice, "--text", text,
                     "--write-media", str(out)],
                    check=True, timeout=60,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                print(f"[audio] {out.name} 생성 실패: {e}", file=sys.stderr)
                if out.exists():
                    out.unlink()


def telegram(method, payload):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def build_message(entry):
    d = datetime.date.fromisoformat(entry["date"])
    lines = [f"\U0001F4DA <b>{d.month}월 {d.day}일 오늘의 표현</b>", ""]
    for lang in ("en", "zh"):
        sec = entry[lang]
        if not sec["items"]:
            lines.append(f"{LANG_FLAG[lang]} {LANG_LABEL[lang]}: 모든 레벨 완주! \U0001F3C6")
            lines.append("")
            continue
        lines.append(f"{LANG_FLAG[lang]} <b>{LANG_LABEL[lang]} · {sec['level_name']}</b>"
                     f" ({sec['progress']}/{sec['total']})")
        for it in sec["items"]:
            if lang == "zh":
                lines.append(f"• {it['expression']} ({it['pinyin']}) — {it['meaning']}")
            else:
                lines.append(f"• {it['expression']} — {it['meaning']}")
        if sec.get("levelup"):
            lines.append(f"\U0001F389 <b>미션 컴플리트! {sec['next_level_name']}으로 레벨업!</b>")
        lines.append("")
    lines.append("\U0001F50A 발음 듣기 · 예문 · 복습 퀴즈는 웹에서!")
    return "\n".join(lines)


def build_claude_url(entry):
    en_list = ", ".join(i["expression"] for i in entry["en"]["items"]) or "없음"
    zh_list = ", ".join(i["expression"] for i in entry["zh"]["items"]) or "없음"
    prompt = (
        f"오늘 배운 표현으로 회화 연습을 하고 싶어. "
        f"영어 표현: {en_list}. 중국어 표현: {zh_list}. "
        f"네가 원어민 친구 역할을 맡아 자연스러운 상황극을 만들어 주고, "
        f"내가 이 표현들을 직접 써 볼 수 있게 대화를 이끌어줘. "
        f"내 문장에 어색한 부분이 있으면 그때그때 자연스럽게 교정해줘. "
        f"영어부터 시작하고, 끝나면 중국어(병음 포함)로 넘어가자."
    )
    return "https://claude.ai/new?q=" + urllib.parse.quote(prompt)


def send(entry):
    keyboard = {"inline_keyboard": [
        [{"text": "\U0001F4D6 웹에서 보기", "url": f"{SITE}/?d={entry['date']}"}],
        [{"text": "\U0001F4AC Claude로 회화 연습", "url": build_claude_url(entry)}],
    ]}
    if DRY_RUN:
        print("=== DRY RUN: 발송할 메시지 ===")
        print(build_message(entry))
        print("버튼1:", keyboard["inline_keyboard"][0][0]["url"])
        print("버튼2:", keyboard["inline_keyboard"][1][0]["url"][:120], "...")
        return
    telegram("sendMessage", {
        "chat_id": CHAT_ID,
        "text": build_message(entry),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": keyboard,
    })


def main():
    state = read_json(WEB_DATA / "state.json", None)
    if state is None:
        print("state.json이 없습니다.", file=sys.stderr)
        sys.exit(1)
    archive = read_json(WEB_DATA / "archive.json", [])

    # 오늘 이미 발송했으면: 기본은 건너뛰기, force면 같은 내용 재발송(진도 중복 진행 없음)
    existing = next((e for e in archive if e["date"] == TODAY), None)
    if existing:
        if FORCE:
            print("오늘 항목 재발송(force).")
            send(existing)
        else:
            print("오늘은 이미 발송했습니다. 건너뜁니다.")
        return

    entry = {"date": TODAY}
    for lang in ("en", "zh"):
        before_level = state[lang]["level"]
        items, levelup, done = pick_today(lang, state)
        make_audio(lang, items)
        entry[lang] = {
            "level": before_level,
            "level_name": LEVEL_NAMES.get(before_level, str(before_level)),
            "items": items,
            "levelup": levelup,
            "next_level_name": LEVEL_NAMES.get(state[lang]["level"], ""),
            "progress": state[lang]["index"],
            "total": len(load_level(lang, before_level) or []),
            "done": done,
        }
        # 레벨업/완주 시에는 그 레벨을 전부 끝낸 것이므로 전체 완료로 표기
        if levelup or done:
            entry[lang]["progress"] = entry[lang]["total"]

    archive.insert(0, entry)  # 최신이 앞
    state["last_sent"] = TODAY
    state["days"] = state.get("days", 0) + 1

    write_json(WEB_DATA / "archive.json", archive)
    write_json(WEB_DATA / "state.json", state)

    send(entry)
    print(f"{TODAY} 발송 완료: 영어 {len(entry['en']['items'])}개, "
          f"중국어 {len(entry['zh']['items'])}개")


if __name__ == "__main__":
    main()
