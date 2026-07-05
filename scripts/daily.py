#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
매일 오후 9시(KST) 실행되는 학습 발송 스크립트. (회화 대화 버전)
1) 오늘의 상황별 대화 선정 (언어당 1개, 레벨별 순차 진행)
2) 음성(MP3) 생성 — edge-tts, 대사 줄마다·화자마다 다른 목소리
3) 웹 아카이브(docs/data/archive.json)와 진도(state.json) 갱신
4) 텔레그램에 대화 전문 발송 + 버튼 2개(웹에서 보기 / Claude 회화 연습)
"""
import json
import os
import re
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

PER_DAY = 1  # 언어당 하루 새 대화 개수 (대화 1개 = 여러 문장·표현 포함)
LEVEL_NAMES = {1: "초급", 2: "중급", 3: "고급"}
LANG_LABEL = {"en": "영어", "zh": "중국어"}
LANG_FLAG = {"en": "\U0001F1FA\U0001F1F8", "zh": "\U0001F1E8\U0001F1F3"}
# 화자 A/B에 각각 다른 목소리를 배정 (실제 대화처럼 들리게)
VOICES = {
    "en": {"A": "en-US-JennyNeural", "B": "en-US-GuyNeural"},
    "zh": {"A": "zh-CN-XiaoxiaoNeural", "B": "zh-CN-YunxiNeural"},
}

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


def ensure_state_shape(state):
    """state.json 필드 보정 (회화 버전 스키마: level/pos/done/learned/tg_offset/kb_sent)"""
    for lang in ("en", "zh"):
        st = state.setdefault(lang, {})
        st.setdefault("level", 1)
        st.setdefault("pos", {"1": 0, "2": 0, "3": 0})
        st.setdefault("done", False)
        st.setdefault("learned", 0)
    state.setdefault("tg_offset", 0)
    state.setdefault("days", 0)
    state.setdefault("last_sent", None)


def process_commands(state):
    """봇 채팅으로 받은 레벨 변경 명령 처리. 예: '영어 고급', '중국어 초급'"""
    if DRY_RUN or not BOT_TOKEN:
        return
    try:
        r = telegram("getUpdates", {"offset": state.get("tg_offset", 0) + 1,
                                    "timeout": 0})
    except Exception as e:
        print(f"[commands] getUpdates 실패: {e}", file=sys.stderr)
        return
    LV = {"초급": 1, "중급": 2, "고급": 3}
    replies = {}
    for up in r.get("result", []):
        state["tg_offset"] = max(state.get("tg_offset", 0), up["update_id"])
        msg = up.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(CHAT_ID):
            continue
        m = re.search(r"(영어|중국어)\s*(초급|중급|고급)", msg.get("text") or "")
        if not m:
            continue
        lang = "en" if m.group(1) == "영어" else "zh"
        level = LV[m.group(2)]
        st = state[lang]
        st["level"] = level
        st["done"] = False
        total = len(load_level(lang, level) or [])
        if st["pos"].get(str(level), 0) >= total:
            st["pos"][str(level)] = 0  # 이미 끝낸 레벨은 처음부터 다시
        replies[lang] = f"✅ 오늘부터 {m.group(1)}는 {m.group(2)}으로 나갑니다!"
    for text in replies.values():
        try:
            telegram("sendMessage", {"chat_id": CHAT_ID, "text": text})
        except Exception:
            pass


def ensure_keyboard(state):
    """레벨 변경용 상주 버튼판을 채팅방에 1회 설치"""
    if DRY_RUN or not BOT_TOKEN or state.get("kb_sent"):
        return
    kb = {"keyboard": [
        [{"text": "영어 초급"}, {"text": "영어 중급"}, {"text": "영어 고급"}],
        [{"text": "중국어 초급"}, {"text": "중국어 중급"}, {"text": "중국어 고급"}],
    ], "resize_keyboard": True, "is_persistent": True}
    try:
        telegram("sendMessage", {
            "chat_id": CHAT_ID,
            "text": "⚙️ 입력창 아래 버튼으로 언제든 레벨을 바꿀 수 있어요.\n"
                    "누르면 다음 발송(매일 밤 9시)부터 적용됩니다.",
            "reply_markup": kb})
        state["kb_sent"] = True
    except Exception as e:
        print(f"[keyboard] 설치 실패: {e}", file=sys.stderr)


def pick_today(lang, state):
    """오늘 배울 대화를 뽑고 진도를 갱신. (items, levelup, all_done) 반환"""
    st = state[lang]
    if st.get("done"):
        return [], False, True
    items = load_level(lang, st["level"])
    if items is None:
        st["done"] = True
        return [], False, True
    key = str(st["level"])
    idx = st["pos"].get(key, 0)
    picked = items[idx: idx + PER_DAY]
    st["pos"][key] = idx + len(picked)
    st["learned"] = st.get("learned", 0) + len(picked)
    levelup = False
    if st["pos"][key] >= len(items):
        nxt = st["level"] + 1
        nxt_items = load_level(lang, nxt)
        if nxt_items is not None and st["pos"].get(str(nxt), 0) < len(nxt_items):
            st["level"] = nxt
            levelup = True
        else:
            st["done"] = True
    return picked, levelup, st.get("done", False)


def make_audio(lang, dialogues):
    """대화의 각 줄을 화자별 목소리로 MP3 생성. 실패해도 전체 발송은 막지 않는다."""
    outdir = AUDIO_DIR / lang
    outdir.mkdir(parents=True, exist_ok=True)
    for d in dialogues:
        for i, line in enumerate(d["lines"]):
            voice = VOICES[lang].get(line.get("speaker", "A"), VOICES[lang]["A"])
            out = outdir / f"{d['id']}-L{i}.mp3"
            if out.exists():
                continue
            try:
                subprocess.run(
                    ["edge-tts", "--voice", voice, "--text", line["text"],
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
    lines = [f"\U0001F4DA <b>{d.month}월 {d.day}일 오늘의 회화</b>", ""]
    for lang in ("en", "zh"):
        sec = entry[lang]
        if not sec["items"]:
            lines.append(f"{LANG_FLAG[lang]} {LANG_LABEL[lang]}: 모든 레벨 완주! \U0001F3C6")
            lines.append("")
            continue
        dlg = sec["items"][0]
        lines.append(f"{LANG_FLAG[lang]} <b>{LANG_LABEL[lang]} · {sec['level_name']}</b>"
                     f" ({sec['progress']}/{sec['total']}) — {dlg['situation']}")
        for ln in dlg["lines"]:
            tag = "A" if ln["speaker"] == "A" else "B"
            lines.append(f"<b>{tag}</b> {ln['text']}")
            lines.append(f"    {ln['ko']}")
        if sec.get("levelup"):
            lines.append(f"\U0001F389 <b>미션 컴플리트! {sec['next_level_name']}으로 레벨업!</b>")
        lines.append("")
    lines.append("\U0001F50A 대사별 발음 듣기 · 쉐도잉은 웹에서!")
    lines.append("<i>레벨 변경: 이 채팅에 '영어 고급'처럼 보내면 다음 발송부터 적용</i>")
    return "\n".join(lines)


def build_claude_url(entry):
    parts = []
    for lang in ("en", "zh"):
        items = entry[lang]["items"]
        if not items:
            continue
        d = items[0]
        label = LANG_LABEL[lang]
        exprs = ", ".join(d.get("key_expressions", [])) or "없음"
        parts.append(f"{label} 상황: {d['situation']} (핵심 표현: {exprs})")
    situations = " / ".join(parts) or "자유 주제"
    prompt = (
        f"오늘 배운 대화 상황으로 회화 연습을 하고 싶어. {situations}. "
        f"네가 원어민 친구 역할을 맡아서 이 상황을 자연스러운 롤플레이로 이어가 줘. "
        f"내가 직접 대사를 말해보게 유도하고, 어색한 부분은 그때그때 자연스럽게 교정해줘. "
        f"영어 상황부터 시작하고, 끝나면 중국어(병음 포함)로 넘어가자."
    )
    return
