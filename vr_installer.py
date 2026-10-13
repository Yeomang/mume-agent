"""
VR 예약주문 에이전트(vr-agent) 원격 설치 — VR 웹 콘솔(mume주소/vr → 설정 → '서버에 자동 설치')이
이 서버의 mume 에이전트에 설치를 요청하면, 사용자가 서버를 직접 만지지 않고 설치·실행까지 끝낸다.

  POST /vr/install          설치 시작 (백그라운드) — 이미 설치돼 있으면 코드만 갱신하고 설정·데이터는 유지
  GET  /vr/install-status   단계별 진행 상황
  POST /vr/configure        설치 후 알림(텔레그램)만 바꾸기 (재설치 없이)
  GET  /vr/configure        현재 알림 방식 (값은 돌려주지 않음)
  GET  /vr/passwords        VR 비밀번호 등록 상태 (사용자별 인증서·계좌별 계좌 비밀번호 — 설정 여부만)
  POST /vr/passwords        VR 전용 비밀번호 저장/초기화 (무매와 다를 때만 — 없으면 무매 것을 씀)

무한매수 쪽에서 이미 가진 것을 그대로 쓴다 (사용자가 다시 입력하지 않게):
  - 공동인증서·계좌 비밀번호: mume이 저장한 Credential Manager 항목 (vr-agent가 직접 읽음)
  - HTS 경로, 텔레그램 봇·채팅: 이 에이전트의 Config (콘솔 agent_settings에서 로드된 값)
새로 받는 것은 팬딩 이메일/비밀번호와 웹 연결 키뿐이다. HTS 사용자·계좌는 웹 '계좌 관리'의 계좌마다 정해져
플랜에 실려 오므로 설치 때 받지 않는다.

보안: 코드는 VR 웹 콘솔(mume-console.vercel.app) 주소에서만, 웹 연결 키로 인증해 받는다
(인증키가 새더라도 임의 주소의 코드를 설치시킬 수 없게 — /deploy의 GitHub 화이트리스트와 같은 취지).
기존 mume 작업·파일은 건드리지 않는다 (설치 위치: mume-agent 옆 vr-agent 폴더).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Body, HTTPException

from config import Config

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
VR_DIR = BASE_DIR.parent / "vr-agent"
STATUS_FILE = BASE_DIR / "vr_install_status.json"
ALLOWED_CONSOLE_HOSTS = {"mume-console.vercel.app"}
TASK_NAME = "VR_Agent_Startup"
STEPS = [
    ("download", "VR 에이전트 코드 내려받기"),
    ("venv", "파이썬 환경 만들기"),
    ("packages", "패키지 설치 (처음엔 몇 분 걸려요)"),
    ("fanding", "팬딩 로그인 확인"),
    ("secrets", "연결 키·팬딩 비밀번호 저장"),
    ("config", "설정 파일 만들기"),
    ("autostart", "서버 켤 때 자동 실행 등록"),
    ("start", "VR 에이전트 시작"),
]
_LOCK = threading.Lock()
_RUNNING = {"on": False}
_STATUS: dict = {}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _save():
    try:
        STATUS_FILE.write_text(json.dumps(_STATUS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _step(key: str, state: str, message: str = ""):
    """state: running | done | skipped | error"""
    for s in _STATUS["steps"]:
        if s["key"] == key:
            s["state"], s["message"] = state, message
    _STATUS["updated_at"] = _now()
    _save()


def _venv_python() -> Path:
    return VR_DIR / ".venv" / "Scripts" / ("python.exe" if os.name == "nt" else "python")


def _run(cmd: list, timeout: int, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(cwd) if cwd else None,
                          encoding="utf-8", errors="replace")


def _vr_running() -> bool:
    """VR 에이전트(main_vr.py)가 이미 실행 중인지."""
    if os.name != "nt":
        return False
    r = _run(["powershell", "-NoProfile", "-Command",
              "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Where-Object { $_.CommandLine -like '*main_vr.py*' } | Measure-Object).Count"], timeout=30)
    return r.stdout.strip() not in ("", "0")


def _apply_options(p: dict, cfg: dict) -> list[str]:
    """알림·계좌 비밀번호 옵션 적용. p['telegram'] = {'mode': 'mume'|'own'|'off', 'token', 'chat_id'},
    p['account_password'] = VR 계좌 공통 비밀번호(무매와 다를 때만, 빈 값이면 그대로). 반환: 적용 내용."""
    import keyring
    notes = []
    tg = p.get("telegram") or {}
    mode = tg.get("mode")
    if mode == "mume":
        if Config.TELEGRAM_BOT_TOKEN_ORDER and Config.TELEGRAM_CHAT_ID:
            # 무한매수 주문 알림 봇으로 VR 알림도 받는다 (무한매수 쪽은 보내기만 해서 충돌 없음)
            keyring.set_password("vr_telegram", "bot_token", Config.TELEGRAM_BOT_TOKEN_ORDER)
            cfg["telegram_chat_id"] = str(Config.TELEGRAM_CHAT_ID)
            notes.append("알림: 무한매수 주문 알림 봇")
        else:
            cfg["telegram_chat_id"] = ""
            notes.append("알림: 무한매수 봇 설정이 없어 웹 알림만")
        cfg["telegram_mode"] = "mume"
    elif mode == "own":
        if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", tg.get("token") or "") or not re.fullmatch(r"-?\d{5,}", str(tg.get("chat_id") or "")):
            raise RuntimeError("VR 전용 텔레그램 봇 토큰·채팅 ID 형식이 올바르지 않아요")
        keyring.set_password("vr_telegram", "bot_token", tg["token"])
        cfg["telegram_chat_id"] = str(tg["chat_id"])
        cfg["telegram_mode"] = "own"
        notes.append("알림: VR 전용 봇")
    elif mode == "off":
        cfg["telegram_chat_id"] = ""
        cfg["telegram_mode"] = "off"
        notes.append("알림: 텔레그램 끔(웹만)")
    if p.get("account_password"):
        keyring.set_password("vr_meritz_account", "default", p["account_password"])
        notes.append("VR 계좌 비밀번호 저장")
    return notes


def _install(p: dict):
    try:
        # 1) 코드 내려받기 — 최상위 파일만 (vr_config.json·data·.venv 는 그대로)
        _step("download", "running")
        r = requests.get(f"{p['console_url']}/vr/agent/update/zip/{p['sha']}",
                         headers={"x-vr-agent-key": p["agent_key"]}, timeout=120)
        r.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        root = zf.namelist()[0].split("/")[0] + "/"
        VR_DIR.mkdir(parents=True, exist_ok=True)
        n = 0
        for name in zf.namelist():
            rel = name[len(root):]
            if not rel or rel.endswith("/") or "/" in rel or rel == "vr_config.json":
                continue
            (VR_DIR / rel).write_bytes(zf.read(name))
            n += 1
        (VR_DIR / "data").mkdir(exist_ok=True)
        (VR_DIR / "data" / "version.txt").write_text(p["sha"], encoding="utf-8")
        _step("download", "done", f"{n}개 파일 · 버전 {p['sha'][:7]} · {VR_DIR}")

        # 2) 가상환경
        _step("venv", "running")
        if _venv_python().exists():
            _step("venv", "skipped", "이미 있음")
        else:
            res = _run([sys.executable, "-m", "venv", str(VR_DIR / ".venv")], timeout=300)
            if res.returncode != 0:
                raise RuntimeError(f"가상환경 생성 실패: {res.stderr[-300:]}")
            _step("venv", "done")

        # 3) 패키지
        _step("packages", "running")
        env = dict(os.environ, PYTHONUTF8="1")
        res = _run([str(_venv_python()), "-m", "pip", "install", "-q", "-r", str(VR_DIR / "requirements.txt")],
                   timeout=1800, env=env)
        if res.returncode != 0:
            raise RuntimeError(f"패키지 설치 실패: {res.stderr[-300:]}")
        _step("packages", "done")

        # 4) 팬딩 로그인 확인 (비밀번호는 환경 변수로만 전달 — 명령줄·로그에 남기지 않음)
        _step("fanding", "running")
        check = ("import os, sys; sys.path.insert(0, os.getcwd()); from fanding_client import FandingClient; "
                 "FandingClient(os.environ['VR_FANDING_EMAIL'], os.environ['VR_FANDING_PW']).login(); print('ok')")
        res = _run([str(_venv_python()), "-c", check], timeout=120, cwd=VR_DIR,
                   env=dict(env, VR_FANDING_EMAIL=p["fanding_email"], VR_FANDING_PW=p["fanding_password"]))
        if res.returncode != 0 or "ok" not in res.stdout:
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"팬딩 로그인 실패 — 이메일·비밀번호를 확인해 주세요 ({tail[0][-160:]})")
        _step("fanding", "done", p["fanding_email"])

        # 5) 비밀값 (이 PC의 Windows 자격 증명 관리자)
        _step("secrets", "running")
        import keyring
        keyring.set_password("vr_fanding", p["fanding_email"], p["fanding_password"])
        keyring.set_password("vr_console", "agent_key", p["agent_key"])
        cfg_path = VR_DIR / "vr_config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig")) if cfg_path.exists() else {}
        if not p.get("telegram") and not cfg.get("telegram_mode"):
            # 처음 설치: 무매 주문 알림 봇이 있으면 같이 쓰고, 없으면 웹 알림만 (나중에 웹 초기 설정에서 변경)
            p["telegram"] = {"mode": "mume" if Config.TELEGRAM_BOT_TOKEN_ORDER and Config.TELEGRAM_CHAT_ID else "off"}
        notes = _apply_options(p, cfg)
        _step("secrets", "done", ", ".join(notes))

        # 6) 설정 — 있으면 필요한 값만 갱신 (플랜·모드 등 기존 값 유지)
        _step("config", "running")
        if p["hts_user"]:  # 예전 콘솔 호환 — 지금은 플랜(계좌)마다 사용자가 정해짐
            cfg["hts_user"] = p["hts_user"]
        cfg.update({
            "hts_exe_path": cfg.get("hts_exe_path") or Config.HTS_EXE_PATH or r"C:\메리츠증권\iMERITZ XII\Main\imeritz.exe",
            "mume_agent_dir": str(BASE_DIR),
            "fanding_email": p["fanding_email"],
            "console_url": p["console_url"],
        })
        cfg.setdefault("dry_run", True)      # 처음엔 테스트 모드 — 웹 설정에서 실주문으로
        cfg.setdefault("plans", [])          # 플랜은 웹 '내 플랜'에서
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        _step("config", "done", "테스트 모드로 시작" if cfg["dry_run"] else "실주문 모드 유지")

        # 7) 자동 실행 (무한매수 에이전트처럼 로그온 시, 관리자 권한, 데스크톱 세션)
        _step("autostart", "running")
        if os.name == "nt":
            res = _run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", f'"{VR_DIR / "run_vr_agent.bat"}"',
                        "/sc", "onlogon", "/rl", "highest", "/it", "/f"], timeout=60)
            _step("autostart", "done" if res.returncode == 0 else "error",
                  "" if res.returncode == 0 else f"등록 실패(설치는 계속): {res.stderr.strip()[-200:]}")
        else:
            _step("autostart", "skipped", "Windows 전용")

        # 8) 시작 — 이미 돌고 있으면 그 에이전트가 새 연결 키·코드를 스스로 반영(최대 10분)
        _step("start", "running")
        if _vr_running():
            _step("start", "skipped", "이미 실행 중 — 새 연결 키로 곧 다시 연결돼요")
        elif os.name == "nt":
            res = _run(["schtasks", "/run", "/tn", TASK_NAME], timeout=60)
            if res.returncode != 0:
                raise RuntimeError(f"VR 에이전트 시작 실패: {res.stderr.strip()[-200:]}")
            _step("start", "done", "웹 상단이 '에이전트 연결됨'으로 바뀌면 완료")
        else:
            _step("start", "skipped", "Windows 전용")
        _STATUS["state"] = "done"
    except Exception as e:
        cur = next((s for s in _STATUS["steps"] if s["state"] == "running"), None)
        if cur:
            _step(cur["key"], "error", str(e)[:400])
        _STATUS["state"], _STATUS["error"] = "error", str(e)[:400]
    finally:
        _STATUS["finished_at"] = _now()
        _save()
        _RUNNING["on"] = False


@router.post("/vr/install")
def vr_install(payload: dict = Body(...)):
    p = {k: (payload.get(k) or "").strip() if isinstance(payload.get(k), str) else payload.get(k)
         for k in ("console_url", "agent_key", "sha", "fanding_email", "fanding_password", "hts_user",
                   "account_password", "telegram")}
    p["hts_user"] = p["hts_user"] or ""
    u = urlparse(p["console_url"] or "")
    if u.scheme != "https" or (u.hostname or "") not in ALLOWED_CONSOLE_HOSTS:
        raise HTTPException(status_code=403, detail="허용되지 않은 콘솔 주소입니다.")
    p["console_url"] = f"https://{u.hostname}"
    if not re.fullmatch(r"vrk_[0-9a-f]{48}", p["agent_key"] or ""):
        raise HTTPException(status_code=400, detail="연결 키 형식이 올바르지 않습니다.")
    if not re.fullmatch(r"[0-9a-f]{7,40}", p["sha"] or ""):
        raise HTTPException(status_code=400, detail="버전 형식이 올바르지 않습니다.")
    if not p["fanding_email"] or not p["fanding_password"]:
        raise HTTPException(status_code=400, detail="팬딩 이메일·비밀번호를 입력해 주세요.")
    with _LOCK:
        if _RUNNING["on"]:
            raise HTTPException(status_code=409, detail="이미 설치가 진행 중이에요.")
        _RUNNING["on"] = True
    _STATUS.clear()
    _STATUS.update(state="running", started_at=_now(), hts_user=p["hts_user"], install_dir=str(VR_DIR),
                   steps=[{"key": k, "label": l, "state": "pending", "message": ""} for k, l in STEPS])
    _save()
    threading.Thread(target=_install, args=(p,), daemon=True, name="vr-install").start()
    return {"status": "started", "install_dir": str(VR_DIR)}


@router.get("/vr/install-status")
def vr_install_status():
    if _STATUS:
        return _STATUS
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"state": "none"}


@router.get("/vr/configure")
def vr_configure_get():
    import keyring
    cfg_path = VR_DIR / "vr_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig")) if cfg_path.exists() else {}
    mode = cfg.get("telegram_mode")
    if not mode:  # 예전 설치(모드 기록 없음) — 저장된 봇 토큰이 무한매수 봇과 같은지로 판단
        token = keyring.get_password("vr_telegram", "bot_token")
        mode = "off" if not (cfg.get("telegram_chat_id") and token) else (
            "mume" if token == Config.TELEGRAM_BOT_TOKEN_ORDER else "own")
    return {"installed": cfg_path.exists(), "telegram_mode": mode,
            "vr_account_password": bool(keyring.get_password("vr_meritz_account", "default")),
            "mume_telegram": bool(Config.TELEGRAM_BOT_TOKEN_ORDER and Config.TELEGRAM_CHAT_ID), "hts_user": cfg.get("hts_user", "")}


@router.post("/vr/configure")
def vr_configure(payload: dict = Body(...)):
    """설치 후 알림 방식·VR 계좌 비밀번호만 변경 — 적용은 VR 에이전트 재시작 때(웹이 재시작 명령을 함께 보냄)."""
    cfg_path = VR_DIR / "vr_config.json"
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail="VR 에이전트가 아직 설치돼 있지 않아요.")
    p = {"telegram": payload.get("telegram"), "account_password": (payload.get("account_password") or "").strip()}
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    try:
        notes = _apply_options(p, cfg)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "applied": notes}


# ───────────────────────────── VR 비밀번호 등록 (무매 '⑦ HTS 비밀번호 등록'과 같은 방식)
# VR 에이전트가 찾는 순서 — 인증서: vr_meritz_cert/<사용자> → 무매 mume_meritz_cert/<사용자>
#                           계좌: vr_meritz_account/<계좌번호> → vr_meritz_account/default(예전 공통) → 무매 mume_meritz_account/<사용자>
# 여기서는 VR 전용 항목만 쓰고 지운다 (무매 비밀번호는 무매 화면에서만 바뀜).
_PW_KEY = {"cert": ("vr_meritz_cert", re.compile(r"^[^\s=,]{1,30}$")),
           "account": ("vr_meritz_account", re.compile(r"^\d{4}-\d{4}-\d{2}$"))}


@router.get("/vr/passwords")
def vr_passwords_status(users: str = "", accounts: str = ""):
    import keyring
    from secrets_manager import SERVICE_ACCOUNT, SERVICE_CERT
    has = lambda svc, key: bool(key and keyring.get_password(svc, key))
    names = [u.strip() for u in users.split(",") if u.strip()][:30]
    accs = [a.strip() for a in accounts.split(",") if a.strip()][:30]
    return {
        "users": [{"name": n, "cert_vr": has("vr_meritz_cert", n), "cert_mume": has(SERVICE_CERT, n),
                   "account_mume": has(SERVICE_ACCOUNT, n)} for n in names],
        "accounts": [{"account_no": a, "vr": has("vr_meritz_account", a)} for a in accs],
        "vr_default": has("vr_meritz_account", "default"),
    }


@router.post("/vr/passwords")
def vr_passwords_update(payload: dict = Body(...)):
    """{"kind": "cert"|"account", "key": 사용자 이름|계좌번호, "password": 새 값} — password가 없으면 VR 전용 항목 삭제.
    kind=account, key=default 삭제는 예전 '공통 VR 계좌 비밀번호' 정리용."""
    import keyring
    from keyring.errors import PasswordDeleteError
    kind, key = payload.get("kind"), str(payload.get("key") or "").strip()
    if kind not in _PW_KEY:
        raise HTTPException(status_code=400, detail="kind는 cert 또는 account")
    service, pattern = _PW_KEY[kind]
    password = str(payload.get("password") or "")
    legacy_default = kind == "account" and key == "default" and not password
    if not legacy_default and not pattern.match(key):
        raise HTTPException(status_code=400, detail="사용자 이름 또는 계좌번호 형식이 올바르지 않아요.")
    if password:
        if len(password) > 64:
            raise HTTPException(status_code=400, detail="비밀번호가 너무 길어요.")
        keyring.set_password(service, key, password)
        return {"status": "saved"}
    try:
        keyring.delete_password(service, key)
    except PasswordDeleteError:
        pass
    return {"status": "deleted"}
