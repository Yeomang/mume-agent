"""
HTS 팝업 감시 — mume-agent와 vr-agent가 **똑같은 파일**을 쓴다 (한쪽을 고치면 다른 쪽에도 그대로 복사, scripts/check_shared.sh).

  1) 알려진 팝업 자동 닫기: 로그인 뒤 백그라운드에서 0.5초마다 KNOWN_POPUPS의 '정확한 제목' 창을 찾아 정해진 버튼만 누른다.
     처음 보는 창은 절대 누르지 않는다 (주문 확인창 등을 잘못 처리하지 않게). 새 팝업은 KNOWN_POPUPS에 한 줄 추가.
  2) 실패 때 떠 있던 창 보고: 작업이 실패하면 HTS 프로세스의 창 제목·문구를 로그와 알림으로 남긴다
     (모르는 팝업이 막았는지 보고 KNOWN_POPUPS에 추가하기 위해).

알림 보내는 곳은 프로그램마다 다르므로 set_notifier(함수)로 넘겨받는다 (cert_guard와 같은 방식).
버튼은 마우스를 움직이지 않는 방식(BM_CLICK·UIA Invoke)으로 눌러 진행 중인 자동화와 부딪히지 않게 한다.
"""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import threading
import time

# title: 창 제목(정확히 일치) / button: 누를 버튼 글자 / allow_close: 버튼을 못 누르면 창 닫기(X)로 대신해도 되는지
# notice: 알림 문구 ({user} 자리에 로그인 사용자) — 없으면 알림 없이 로그만
KNOWN_POPUPS = [
    {
        "title": "CDD 재이행 도래 고객 안내 팝업",
        "button": "확인",
        "allow_close": True,
        "notice": ("🪪 고객확인(CDD) 재등록 안내 — {user}\n"
                   "메리츠 HTS에 고객확인 재등록 안내가 떠서 자동으로 닫았습니다. "
                   "재등록하지 않으면 추가 계좌개설·대출약정 등 일부 업무가 제한될 수 있습니다.\n\n"
                   "· 메리츠 SMART 앱 → 고객정보 → 고객확인등록(CDD/EDD)에서 재등록해 주세요."),
    },
]
MAIN_WINDOW_TITLE = "iMeritz"
HTS_IMAGE_PREFIX = "imeritz"
WATCH_INTERVAL_SEC = 0.5
BM_CLICK = 0x00F5
WM_CLOSE = 0x0010

_notifier = None
_user = ""
_watcher: threading.Thread | None = None
_notified: set[tuple[str, str, str]] = set()   # (사용자, 제목, 날짜) — 같은 날 같은 안내는 한 번만


def set_notifier(fn):
    """알림 함수(text -> None) 등록. 없으면 로그만 남긴다."""
    global _notifier
    _notifier = fn


def notify(text: str):
    if not _notifier:
        logging.info(f"[HTS 팝업 알림] {text.splitlines()[0]}")
        return
    try:
        _notifier(text)
    except Exception as e:
        logging.error(f"HTS 팝업 알림 전송 실패: {e}")


def _plain(text: str) -> str:
    """알림에 넣을 HTS 문구 — 텔레그램 Markdown이 깨지지 않게 특수문자 제거."""
    return "".join(" " if c in "*_[]`" else c for c in text)


# ───────────────────────────────────────────── 버튼 누르기
def click_button(hwnd, text: str, allow_close: bool = False) -> bool:
    """창의 버튼(글자 text)을 누른다 — win32 BM_CLICK → UIA Invoke → (허용 시) 창 닫기. 창이 사라지면 True."""
    import win32gui

    def gone(wait=0.7):
        time.sleep(wait)
        return not (win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))

    buttons = []

    def child(h, _):
        try:
            if win32gui.GetClassName(h).lower() == "button" and win32gui.GetWindowText(h).replace("&", "").strip() == text:
                buttons.append(h)
        except Exception:
            pass
        return True
    try:
        win32gui.EnumChildWindows(hwnd, child, None)
    except Exception:
        pass
    if buttons:
        win32gui.SendMessage(buttons[0], BM_CLICK, 0, 0)
        if gone():
            return True
        try:   # 비활성 대화상자는 BM_CLICK이 안 먹을 수 있어 앞으로 가져와 한 번 더
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        win32gui.SendMessage(buttons[0], BM_CLICK, 0, 0)
        if gone():
            return True
    try:   # 자체 그린 버튼 — UIA Invoke (마우스 이동 없음)
        try:
            import pythoncom
            pythoncom.CoInitialize()   # 감시 스레드에서 UIA를 쓰려면 필요
        except Exception:
            pass
        from pywinauto import Application
        w = Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)
        w.child_window(title=text, control_type="Button").invoke()
        if gone():
            return True
    except Exception as e:
        logging.warning(f"'{win32gui.GetWindowText(hwnd)}' 창의 '{text}' 버튼 UIA 클릭 실패: {e}")
    if allow_close:
        win32gui.PostMessage(hwnd, WM_CLOSE, 0, 0)
        if gone():
            logging.info(f"'{text}' 버튼 대신 창 닫기로 처리")
            return True
    logging.error(f"'{win32gui.GetWindowText(hwnd)}' 창의 '{text}' 버튼을 누르지 못했습니다.")
    return False


# ───────────────────────────────────────────── 1) 알려진 팝업 감시
def dismiss_known_popups() -> int:
    """지금 떠 있는 알려진 팝업을 닫는다. 닫은 개수."""
    import win32gui
    closed = 0
    for p in KNOWN_POPUPS:
        hwnd = win32gui.FindWindow(None, p["title"])
        if not (hwnd and win32gui.IsWindowVisible(hwnd)):
            continue
        logging.warning(f"알려진 HTS 팝업 감지: '{p['title']}' → '{p['button']}' 클릭")
        if click_button(hwnd, p["button"], p.get("allow_close", False)):
            closed += 1
        key = (_user, p["title"], dt.date.today().isoformat())
        if p.get("notice") and key not in _notified:
            _notified.add(key)
            notify(p["notice"].format(user=_user or "HTS 사용자"))
    return closed


def _watch():
    while True:
        try:
            dismiss_known_popups()
        except Exception as e:
            logging.debug(f"HTS 팝업 감시 오류: {e}")
        time.sleep(WATCH_INTERVAL_SEC)


def start_watcher(user: str = ""):
    """로그인 성공 뒤 호출 — 감시 스레드를 (한 번만) 시작하고 현재 사용자를 기록한다."""
    global _watcher, _user
    _user = user or _user
    if _watcher and _watcher.is_alive():
        return
    _watcher = threading.Thread(target=_watch, name="hts-popup-watcher", daemon=True)
    _watcher.start()
    logging.info("HTS 팝업 감시 시작")


# ───────────────────────────────────────────── 2) 실패 때 떠 있던 창 보고
def _hts_pids() -> set[int]:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {HTS_IMAGE_PREFIX}*"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower().startswith(HTS_IMAGE_PREFIX) and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def open_hts_windows() -> list[tuple[str, str]]:
    """HTS 프로세스의 보이는 최상위 창 [(제목, 문구)] — 메인 창 제외."""
    import win32gui
    import win32process
    from cert_guard import window_text
    pids = _hts_pids()
    found = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            title = win32gui.GetWindowText(h)
            if title == MAIN_WINDOW_TITLE:
                return True
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pids and pid in pids:
                found.append(h)
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cb, None)
    out = []
    for h in found:
        try:
            out.append((win32gui.GetWindowText(h), window_text(h)))
        except Exception:
            pass
    return out


def report_open_windows(context: str):
    """작업 실패 때 호출 — 떠 있던 HTS 창을 로그·알림으로 남긴다 (모르는 팝업 확인용). 실패해도 예외를 내지 않는다."""
    try:
        wins = open_hts_windows()
    except Exception as e:
        logging.warning(f"HTS 창 목록 조회 실패: {e}")
        return
    if not wins:
        logging.info(f"[{context}] 실패 시점에 메인 창 외 HTS 창 없음")
        return
    for title, text in wins:
        logging.warning(f"[{context}] 실패 시점 HTS 창: '{title}' | {text[:300]}")
    lines = [f"· {_plain(title) or '(제목 없음)'} — {_plain(text)[:120]}" for title, text in wins[:5]]
    notify(f"⚠️ HTS 작업 실패 — {_plain(context)}\n"
           f"실패 시점에 아래 창이 떠 있었습니다. 처음 보는 팝업이 막았다면 알려진 팝업 목록에 추가해 주세요.\n\n"
           + "\n".join(lines))
