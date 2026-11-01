"""
공동인증서 로그인 보호 — mume-agent와 vr-agent가 **똑같은 파일**을 쓴다 (한쪽을 고치면 다른 쪽에도 그대로 복사).
두 저장소의 scripts/check_shared.sh 로 내용이 같은지 확인한다.

  1) 인증서 선택: 조건(구분·사용자)에 맞는 인증서가 여러 개면(갱신 후 예전 것이 남은 경우) 만료일이 가장 늦은 것.
  2) 로그인 결과 판별: 확인 버튼 뒤 'iMeritz' 메인 창 / 비밀번호 거부 안내창 / 시간 초과.
  3) 비밀번호 거부 기록: 거부된 비밀번호로는 다시 로그인하지 않는다 (반복 시도로 인증서가 잠기지 않게).
     기록은 Credential Manager(meritz_cert_rejected / 사용자) — 같은 서버의 mume·VR이 함께 본다. 값은 파일·로그에 남기지 않는다.
     다른 비밀번호를 저장하면 자동으로 풀리고, 같은 비밀번호가 맞다면 웹 '잠금 풀기'(clear_rejected)로 푼다.
  4) '인증서 만료공지' 팝업: '확인'만 눌러 닫고(절대 '갱신'·Enter 아님 — 로그인이 중단됨) 남은 일수·만료일을
     data/cert_status.json에 남긴 뒤 알림을 보낸다. 웹 콘솔은 이 기록으로 팝업·배너를 띄운다.

알림 보내는 곳은 프로그램마다 다르므로 set_notifier(함수)로 넘겨받는다 (mume: 작업별 봇, VR: VR 알림 설정).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time

MAIN_WINDOW_TITLE = "iMeritz"
CERT_DIALOG_KEYWORD = "인증서 선택"
EXPIRY_NOTICE_TITLE = "인증서 만료공지"
KEYRING_SERVICE_REJECTED = "meritz_cert_rejected"
LEGACY_KEYRING_SERVICES_REJECTED = ("vr_cert_rejected",)   # 예전 VR 기록 — 읽기·삭제만
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cert_status.json")

# 오탐하면 주문일 로그인이 막히므로 좁게: '인증서 선택' 창이 아직 떠 있을 때 새로 뜬 짧은 안내창의 문구만 본다.
# '비밀번호를 확인후 다시 입력하십시오'는 메리츠가 평소 입력 안내에도 쓰는 문구라 넣지 않는다(2026-10-25 VR 서버 로그).
CERT_ERROR_RE = re.compile(r"(비밀번호|암호).{0,30}(틀|일치하지|맞지|올바르지|잘못|오류)"
                           r"|(틀|잘못|올바르지).{0,30}(비밀번호|암호)")
CERT_ERROR_MAX_LEN = 300
DATE_RE = re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})")
BM_CLICK = 0x00F5
NOTICE_GRACE_SEC = 4

_notifier = None


# ───────────────────────────────────────────── 알림
def set_notifier(fn):
    """알림 함수(text -> None) 등록. 없으면 로그만 남긴다."""
    global _notifier
    _notifier = fn


def notify(text: str):
    if not _notifier:
        logging.info(f"[인증서 알림] {text.splitlines()[0]}")
        return
    try:
        _notifier(text)
    except Exception as e:
        logging.error(f"인증서 알림 전송 실패: {e}")


def expiry_message(user: str, days: int | None, expire: str | None) -> str:
    """텔레그램·웹 공통 문구 (마크다운 특수문자 없이 — mume은 Markdown 모드로 보냄)."""
    d = f"D-{days}" if days is not None and days >= 0 else ("만료됨" if days is not None else "")
    until = f" ({expire}까지)" if expire else ""
    return (f"🔐 공동인증서 만료 예정 — {user} {d}{until}\n"
            f"만료되면 자동매매 HTS 로그인이 실패합니다.\n\n"
            f"· 인증서를 발급받은 곳(증권사 등)에서 갱신해 주세요.\n"
            f"· 자동매매 서버 PC에서 갱신하거나, 다른 기기에서 갱신했다면 새 인증서를 서버 PC로 옮겨 주세요.\n"
            f"· 갱신하면서 비밀번호를 바꿨다면 콘솔의 인증서 비밀번호도 바꿔 주세요.")


def rejected_message(user: str, already: bool) -> str:
    """비밀번호 거부 알림 — already=True: 예전에 거부된 비밀번호라 로그인을 시도하지 않음."""
    head = ("이전에 증권사에서 거부된 비밀번호라 HTS에 로그인하지 않았습니다." if already else
            "증권사에서 비밀번호가 거부되어 로그인을 멈췄습니다. 틀린 비밀번호로 반복하면 인증서가 잠길 수 있어, "
            "다시 저장하기 전까지 이 사용자는 로그인하지 않습니다.")
    return (f"⛔ 공동인증서 비밀번호 거부 — {user}\n{head}\n\n"
            f"· 콘솔 비밀번호 관리에서 공동인증서 비밀번호를 다시 저장해 주세요.\n"
            f"· 비밀번호가 맞다면 콘솔에서 '잠금 풀기'를 눌러 주세요.")


# ───────────────────────────────────────────── 비밀번호 거부 기록
def is_rejected(user: str, password: str | None) -> bool:
    if not user or not password:
        return False
    try:
        import keyring
        for service in (KEYRING_SERVICE_REJECTED, *LEGACY_KEYRING_SERVICES_REJECTED):
            if keyring.get_password(service, user) == password:
                return True
    except Exception:
        pass
    return False


def mark_rejected(user: str, password: str):
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE_REJECTED, user, password)
    except Exception as e:
        logging.error(f"거부된 인증서 비밀번호 기록 실패: {e}")


def clear_rejected(user: str):
    try:
        import keyring
    except Exception:
        return
    for service in (KEYRING_SERVICE_REJECTED, *LEGACY_KEYRING_SERVICES_REJECTED):
        try:
            keyring.delete_password(service, user)
        except Exception:
            pass


# ───────────────────────────────────────────── 만료 기록 (웹 콘솔 표시용)
def load_status() -> dict:
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_status(data: dict):
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATUS_FILE)
    except Exception as e:
        logging.error(f"인증서 상태 저장 실패: {e}")


def _record_expiry(user: str, days: int | None, expire: str | None):
    data = load_status()
    data[user] = {"expire": expire, "days_left": days, "seen_at": dt.datetime.now().isoformat(timespec="seconds")}
    _save_status(data)


def _clear_expiry(user: str):
    data = load_status()
    if data.pop(user, None) is not None:
        _save_status(data)
        logging.info(f"인증서 만료공지 없이 로그인 — 만료 예정 기록 삭제 ({user})")


def status_for_display() -> list[dict]:
    """[{user, expire, days_left, seen_at, message}] — 남은 일수는 오늘 기준으로 다시 계산."""
    out = []
    today = dt.date.today()
    for user, s in sorted(load_status().items()):
        days = s.get("days_left")
        try:
            days = (dt.date.fromisoformat(s["expire"]) - today).days
        except Exception:
            pass
        out.append({"user": user, "expire": s.get("expire"), "days_left": days, "seen_at": s.get("seen_at"),
                    "message": expiry_message(user, days, s.get("expire"))})
    return out


# ───────────────────────────────────────────── 창 읽기
def visible_windows() -> set[int]:
    import win32gui
    found = []
    win32gui.EnumWindows(lambda h, acc: acc.append(h) if win32gui.IsWindowVisible(h) else None, found)
    return set(found)


def window_text(hwnd) -> str:
    """창 제목 + 자식 컨트롤 글자 (Edit 칸은 읽지 않음 — 입력값이 섞이지 않게)."""
    import win32gui
    parts = [win32gui.GetWindowText(hwnd)]

    def child(h, _):
        try:
            if "edit" not in win32gui.GetClassName(h).lower():
                parts.append(win32gui.GetWindowText(h))
        except Exception:
            pass
        return True
    try:
        win32gui.EnumChildWindows(hwnd, child, None)
    except Exception:
        pass
    text = " ".join(t for t in parts if t)
    if len(text) < 8:   # 자체 그린 창은 UIA로 한 번 더
        try:
            from pywinauto import Application
            w = Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)
            text += " " + " ".join(d.window_text() for d in w.descendants()[:60]
                                   if d.element_info.control_type != "Edit" and d.window_text())
        except Exception:
            pass
    return " ".join(text.split())


def _cert_dialog_open(exclude: int) -> bool:
    import win32gui
    return any(CERT_DIALOG_KEYWORD in win32gui.GetWindowText(h) for h in visible_windows() if h != exclude)


# ───────────────────────────────────────────── 1) 인증서 선택
def pick_certificate(cert_list, category: str, user: str):
    """조건에 맞는 인증서 항목. 여러 개면 목록에 보이는 날짜(만료일)가 가장 늦은 것, 날짜를 못 읽으면 첫 번째."""
    matches = []
    for item in cert_list.children():
        text = f"{item.window_text()} {' '.join(s.window_text() for s in item.children())}"
        if category in text and user in text:
            dates = []
            for y, m, d in DATE_RE.findall(text):
                try:
                    dates.append(dt.date(int(y), int(m), int(d)))
                except ValueError:
                    pass
            matches.append((max(dates) if dates else None, item, text))
    if not matches:
        logging.error(f"조건에 맞는 인증서 없음: category='{category}', user='{user}'")
        return None
    dated = [m for m in matches if m[0]]
    best = max(dated, key=lambda m: m[0]) if dated else matches[0]
    if len(matches) > 1:
        logging.info(f"조건에 맞는 인증서 {len(matches)}개 — 만료일이 가장 늦은 것 선택: {best[2]}")
    else:
        logging.info(f"조건에 맞는 인증서 발견: {best[2]}")
    return best[1]


# ───────────────────────────────────────────── 4) 만료공지 팝업
def _click_confirm(hwnd) -> bool:
    """'확인' 버튼만 누른다 ('갱신'을 누르면 로그인이 중단되고 갱신 화면으로 넘어감)."""
    import win32gui
    buttons = []

    def child(h, _):
        try:
            if win32gui.GetClassName(h).lower() == "button" and win32gui.GetWindowText(h).replace("&", "").strip() == "확인":
                buttons.append(h)
        except Exception:
            pass
        return True
    try:
        win32gui.EnumChildWindows(hwnd, child, None)
    except Exception:
        pass
    if buttons:
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        win32gui.SendMessage(buttons[0], BM_CLICK, 0, 0)
        time.sleep(1)
        if not win32gui.IsWindowVisible(hwnd):
            return True
    try:   # win32 버튼으로 안 잡히는 창 — UIA로 '확인'을 찾아 클릭
        from pywinauto import Application
        w = Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)
        w.child_window(title="확인", control_type="Button").click_input()
        time.sleep(1)
        return not win32gui.IsWindowVisible(hwnd)
    except Exception as e:
        logging.error(f"인증서 만료공지 '확인' 버튼 클릭 실패: {e}")
        return False


def _handle_expiry_notice(hwnd, user: str) -> bool:
    text = window_text(hwnd)
    m_days = re.search(r"앞으로\s*(\d+)\s*일", text)
    m_date = re.search(r"만료\s*일자\s*:?\s*(20\d{2}-\d{2}-\d{2})", text)
    expire = m_date.group(1) if m_date else None
    days = int(m_days.group(1)) if m_days else None
    if days is None and expire:
        days = (dt.date.fromisoformat(expire) - dt.date.today()).days
    logging.warning(f"인증서 만료공지 ({user}): 남은 일수={days}, 만료일={expire} | {text[:200]}")
    closed = _click_confirm(hwnd)
    _record_expiry(user, days, expire)
    notify(expiry_message(user, days, expire))
    return closed


# ───────────────────────────────────────────── 2) 로그인 결과 판별
def wait_login_result(before: set[int], user: str, timeout=300):
    """확인 버튼 뒤 결과 — ('ok', hwnd) / ('rejected', (hwnd, 문구)) / ('timeout', 그동안 보인 새 창 문구).
    그 사이 '인증서 만료공지'가 뜨면 '확인'으로 닫고 계속 기다린다."""
    import win32gui
    end = time.time() + timeout
    seen: dict[int, str] = {}
    read_at: dict[int, float] = {}
    notice_handled: set[int] = set()
    while time.time() < end:
        hwnd = win32gui.FindWindow(None, MAIN_WINDOW_TITLE)
        if hwnd and win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
            # 메인 창이 먼저 잡히고 공지가 조금 늦게 뜰 수도 있어 잠깐 더 본다 (로그인 직후 ESC가 공지에 가지 않게)
            grace_end = time.time() + NOTICE_GRACE_SEC
            while time.time() < grace_end:
                notice = win32gui.FindWindow(None, EXPIRY_NOTICE_TITLE)
                if notice and win32gui.IsWindowVisible(notice) and notice not in notice_handled:
                    notice_handled.add(notice)
                    _handle_expiry_notice(notice, user)
                time.sleep(0.5)
            if not notice_handled:
                _clear_expiry(user)
            return "ok", hwnd
        notice = win32gui.FindWindow(None, EXPIRY_NOTICE_TITLE)
        if notice and win32gui.IsWindowVisible(notice):
            if notice not in notice_handled:
                notice_handled.add(notice)
                _handle_expiry_notice(notice, user)
            elif time.time() - read_at.get(notice, 0) >= 3:   # 첫 클릭이 안 먹었으면 '확인'만 다시
                read_at[notice] = time.time()
                _click_confirm(notice)
            time.sleep(1)
            continue
        for h in visible_windows() - before:
            if time.time() - read_at.get(h, 0) < 3:   # 창마다 3초에 한 번만 읽음 (UIA 조회가 느림)
                continue
            read_at[h] = time.time()
            try:
                text = window_text(h)
            except Exception:
                continue
            if text and seen.get(h) != text:
                seen[h] = text
                logging.info(f"로그인 중 새 창: {text[:120]}")
            if (text and len(text) <= CERT_ERROR_MAX_LEN and CERT_ERROR_RE.search(text)
                    and _cert_dialog_open(exclude=h)):
                return "rejected", (h, text)
        time.sleep(1)
    return "timeout", " | ".join(t[:80] for t in seen.values())
