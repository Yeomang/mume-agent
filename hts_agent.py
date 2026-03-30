# hts_agent.py — HTS 자동화 전용 API 서버
# 웹콘솔(mume-console)에서 프록시로 호출하는 경량 에이전트.
# 실행: uvicorn hts_agent:app --host 0.0.0.0 --port 9000

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from config import Config
from job_control import read_job_pids, unregister_job_pid
from secrets_manager import (
    delete_account_password,
    delete_cert_password,
    get_account_password,
    get_cert_password,
    set_account_password,
    set_cert_password,
)
from utils import block_input

# ─────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "log.log"
AGENT_KEY = os.getenv("HTS_AGENT_KEY", "")

if platform.system() == "Windows":
    import win32gui
else:
    win32gui = None

HTS_WINDOW_TITLE = Config.HTS_WINDOW_NAME
HTS_PROCESS_NAMES: List[str] = ["imeritz.exe", "imeritzmain.exe"]

JOB_CONFIG: Dict[str, Dict[str, Path]] = {
    "morning": {"script": BASE_DIR / "main_morning.py"},
    "evening": {"script": BASE_DIR / "main_evening.py"},
    "aftermarket": {"script": BASE_DIR / "main_aftermarket.py"},
    "cancel_orders": {"script": BASE_DIR / "main_cancel_orders.py"},
}

CURRENT_PROC: Dict[str, Optional[subprocess.Popen]] = {
    job: None for job in JOB_CONFIG
}
LAST_STATUS: Dict[str, Dict[str, Optional[str]]] = {
    job: {"status": "never_run", "finished_at": None, "returncode": None}
    for job in JOB_CONFIG
}
PROC_LOCK = threading.Lock()

# ─────────────────────────────────────
# FastAPI 앱
# ─────────────────────────────────────
app = FastAPI(title="HTS Agent")
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def verify_agent_key(request: Request, call_next):
    if AGENT_KEY and request.headers.get("X-Agent-Key") != AGENT_KEY:
        return JSONResponse(status_code=401, content={"detail": "Invalid agent key"})
    return await call_next(request)


# ─────────────────────────────────────
# 로그 유틸
# ─────────────────────────────────────
LOG_LOCK = threading.Lock()


def write_log(event: str, job: str, detail: str = "") -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{job}] [{event}] {detail}\n"
    with LOG_LOCK:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


# ─────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────
def _ensure_valid_job(job: str) -> None:
    if job not in JOB_CONFIG:
        raise HTTPException(status_code=400, detail=f"알 수 없는 작업 유형입니다: {job}")


def _update_status_from_proc(job: str) -> None:
    proc = CURRENT_PROC.get(job)
    if proc is None:
        return
    rc = proc.poll()
    if rc is None:
        LAST_STATUS[job]["status"] = "running"
        LAST_STATUS[job]["returncode"] = None
    else:
        LAST_STATUS[job]["status"] = "success" if rc == 0 else "error"
        LAST_STATUS[job]["returncode"] = str(rc)
        LAST_STATUS[job]["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        write_log("FINISHED", job, f"status={LAST_STATUS[job]['status']}, returncode={rc}")
        CURRENT_PROC[job] = None


def _tasklist_filter(image_name: str) -> List[Dict[str, str]]:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="cp949", errors="ignore",
    )
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    processes: List[Dict[str, str]] = []
    for line in lines:
        if line.startswith("INFO:"):
            return []
        try:
            cols = next(csv.reader([line]))
        except Exception:
            cols = line.strip('"').split('","')
        if len(cols) >= 2:
            processes.append({"ImageName": cols[0].strip('"'), "PID": cols[1].strip('"')})
    return processes


def _is_imeritz_window_exists() -> bool:
    if win32gui is None:
        return False
    hwnd = win32gui.FindWindow(None, HTS_WINDOW_TITLE)
    return bool(hwnd and win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))


def _is_pid_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="cp949", errors="ignore",
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        if any(l.startswith("INFO:") for l in lines):
            return False
        for line in lines:
            try:
                cols = next(csv.reader([line]))
            except Exception:
                cols = line.strip('"').split('","')
            if len(cols) >= 2:
                try:
                    if int(cols[1].strip('"')) == pid:
                        return True
                except (ValueError, IndexError):
                    continue
        return False
    except Exception:
        return False


def _get_process_status() -> Dict:
    imeritz_procs = _tasklist_filter("imeritz.exe")
    imeritzmain_procs = _tasklist_filter("imeritzmain.exe")
    py_procs = _tasklist_filter("python.exe")

    actual_python_pids = set()
    for p in py_procs:
        try:
            actual_python_pids.add(int(p["PID"]))
        except (ValueError, TypeError):
            continue

    jobs_status = {}
    for job in JOB_CONFIG:
        pids = read_job_pids(job)
        alive_pids = [pid for pid in pids if _is_pid_alive(pid) and pid in actual_python_pids]
        for dead_pid in [pid for pid in pids if pid not in alive_pids]:
            unregister_job_pid(job, dead_pid)
        jobs_status[job] = {"running": len(alive_pids) > 0, "pids": alive_pids}

    return {
        "imeritz": {
            "window_exists": _is_imeritz_window_exists(),
            "imeritz_count": len(imeritz_procs),
            "imeritz_pids": [p["PID"] for p in imeritz_procs],
            "imeritzmain_count": len(imeritzmain_procs),
            "imeritzmain_pids": [p["PID"] for p in imeritzmain_procs],
        },
        "python": {
            "process_count": len(py_procs),
            "pids": [p["PID"] for p in py_procs],
        },
        "jobs": jobs_status,
    }


# ─────────────────────────────────────
# API 엔드포인트
# ─────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run_job(
    job: str,
    user_accounts: str | None = None,
    test_mode: bool = False,
    date_from: str | None = None,
    date_to: str | None = None,
):
    _ensure_valid_job(job)
    write_log("START_REQUEST", job,
              f"user_accounts={user_accounts or '-'}, test_mode={test_mode}, "
              f"date_from={date_from or '-'}, date_to={date_to or '-'}")

    # 비밀번호 검증
    if user_accounts:
        try:
            parsed_users = json.loads(user_accounts)
            missing_passwords = []
            for user_name in parsed_users:
                cert_pw = get_cert_password(user_name)
                account_pw = get_account_password(user_name)
                missing = []
                if not cert_pw:
                    missing.append("공동인증서 비밀번호")
                if not account_pw:
                    missing.append("계좌 비밀번호")
                if missing:
                    missing_passwords.append(f"'{user_name}': {', '.join(missing)}")
            if missing_passwords:
                error_msg = ("비밀번호가 설정되지 않아 실행할 수 없습니다.\n\n"
                             + "\n".join(missing_passwords)
                             + "\n\n[비밀번호 관리]에서 비밀번호를 먼저 설정해주세요.")
                write_log("START_REJECTED", job, f"password not set: {missing_passwords}")
                raise HTTPException(status_code=400, detail=error_msg)
        except json.JSONDecodeError:
            pass

    with PROC_LOCK:
        _update_status_from_proc(job)

        if CURRENT_PROC[job] is not None:
            write_log("START_REJECTED", job, "already running")
            raise HTTPException(status_code=409, detail=f"{job} 작업이 이미 실행 중입니다.")

        script_path = JOB_CONFIG[job]["script"]
        if not script_path.exists():
            write_log("ERROR", job, f"script not found: {script_path}")
            raise HTTPException(status_code=500, detail=f"스크립트를 찾을 수 없습니다: {script_path}")

        env = os.environ.copy()
        env["JOB_NAME"] = job
        if user_accounts:
            env["JOB_USER_ACCOUNTS"] = user_accounts
        env["JOB_TEST_MODE"] = "1" if test_mode else "0"
        if date_from:
            env["JOB_DATE_FROM"] = date_from
        if date_to:
            env["JOB_DATE_TO"] = date_to

        CURRENT_PROC[job] = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(BASE_DIR),
            env=env,
        )
        LAST_STATUS[job] = {"status": "running", "finished_at": None, "returncode": None}
        pid = CURRENT_PROC[job].pid if CURRENT_PROC[job] else "-"
        write_log("STARTED", job, f"pid={pid}")

    return {"message": f"{job} 작업을 시작했습니다."}


@app.post("/stop")
def stop_job(job: str):
    _ensure_valid_job(job)
    write_log("STOP_REQUEST", job, "requested via API")

    with PROC_LOCK:
        # 1) API가 띄운 프로세스 종료
        proc = CURRENT_PROC[job]
        if proc is not None:
            try:
                write_log("STOP_TRY", job, f"pid={proc.pid}")
                proc.terminate()
            except Exception as e:
                write_log("ERROR", job, f"terminate failed: {e}")
            CURRENT_PROC[job] = None

        # 2) job_control PID 종료
        try:
            pids = read_job_pids(job)
        except Exception:
            pids = []
        for pid in pids:
            kill_success = False
            try:
                r = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, text=True, check=False,
                )
                if r.returncode == 0:
                    write_log("TASKKILL_JOBPID", job, f"pid={pid} killed")
                    kill_success = True
                else:
                    output_lower = (r.stdout + r.stderr).lower()
                    if "not found" in output_lower or "찾을 수 없습니다" in output_lower:
                        kill_success = True
                    elif not _is_pid_alive(pid):
                        kill_success = True
            except Exception:
                if not _is_pid_alive(pid):
                    kill_success = True
            if kill_success:
                try:
                    unregister_job_pid(job, pid)
                except Exception:
                    pass

        # 3) HTS 프로세스 종료
        for name in HTS_PROCESS_NAMES:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", name],
                capture_output=True, text=True, check=False,
            )
            if r.returncode == 0:
                write_log("TASKKILL", job, f"{name} killed")

        # 4) 입력 잠금 해제
        try:
            block_input(False)
        except Exception as e:
            write_log("ERROR", job, f"block_input(False) failed: {e}")

        LAST_STATUS[job]["status"] = "stopped"
        LAST_STATUS[job]["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        LAST_STATUS[job]["returncode"] = None
        write_log("STOPPED", job, "HTS closed, input unlocked")

    return {"message": f"{job} 작업을 중지했습니다."}


@app.get("/status")
def get_status(job: str):
    _ensure_valid_job(job)
    _update_status_from_proc(job)
    return LAST_STATUS[job]


@app.get("/logs")
def get_logs(max_lines: int = 300):
    if not LOG_FILE.exists():
        return {"text": "(로그 파일 없음)"}
    try:
        max_bytes = 200_000
        with LOG_FILE.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
        try:
            text = data.decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            text = data.decode("cp949", errors="ignore")
        lines = text.splitlines()
        tail = "\n".join(lines[-max_lines:])
        return {"text": tail or "(로그 없음)"}
    except Exception as e:
        return {"text": f"(로그 읽기 오류: {e})"}


@app.get("/processes")
def processes():
    return _get_process_status()


# ─────────────────────────────────────
# 비밀번호 관리
# ─────────────────────────────────────

@app.get("/password-status")
def get_password_status(users: str | None = None):
    if not users:
        return {"users": [], "error": "users 파라미터가 필요합니다."}
    user_list = [u.strip() for u in users.split(",") if u.strip()]
    result = []
    for user_name in user_list:
        cert_pw = get_cert_password(user_name)
        account_pw = get_account_password(user_name)
        result.append({
            "name": user_name,
            "cert_set": cert_pw is not None and cert_pw != "",
            "account_set": account_pw is not None and account_pw != "",
        })
    return {"users": result}


@app.post("/update-passwords")
def update_passwords(payload: dict = Body(...)):
    user = (payload.get("user") or "").strip()
    cert_pw = payload.get("cert_password")
    account_pw = payload.get("account_password")

    if not user:
        raise HTTPException(status_code=400, detail="user는 필수입니다.")

    changes = []
    if isinstance(cert_pw, str) and cert_pw != "":
        set_cert_password(user, cert_pw)
        changes.append("cert")
    if isinstance(account_pw, str) and account_pw != "":
        set_account_password(user, account_pw)
        changes.append("account")

    if not changes:
        raise HTTPException(status_code=400, detail="변경할 비밀번호가 없습니다.")

    write_log("UPDATE_PASSWORD", "config", f"user={user}, changed={','.join(changes)}")
    return {"message": "비밀번호가 저장되었습니다.", "changed": changes}


@app.post("/delete-passwords")
def delete_passwords(payload: dict = Body(...)):
    user = (payload.get("user") or "").strip()
    delete_type = (payload.get("type") or "").strip()

    if not user:
        raise HTTPException(status_code=400, detail="user는 필수입니다.")
    if delete_type not in ("cert", "account", "all"):
        raise HTTPException(status_code=400, detail="type은 'cert', 'account', 'all' 중 하나여야 합니다.")

    deleted = []
    if delete_type in ("cert", "all"):
        if delete_cert_password(user):
            deleted.append("cert")
    if delete_type in ("account", "all"):
        if delete_account_password(user):
            deleted.append("account")

    write_log("DELETE_PASSWORD", "config", f"user={user}, deleted={','.join(deleted) or 'none'}")
    return {"message": "비밀번호가 삭제되었습니다.", "deleted": deleted}
