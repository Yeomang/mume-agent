# hts_agent.py — HTS 자동화 전용 API 서버 (v2026.04.01)
# 웹콘솔(mume-console)에서 프록시로 호출하는 경량 에이전트.
# 실행: uvicorn hts_agent:app --host 0.0.0.0 --port 9000

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Dict, List, Optional
import ssl
from urllib.request import Request as UrlRequest, urlopen

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


# ─────────────────────────────────────
# 배포 (Deploy)
# ─────────────────────────────────────
DEPLOY_INFO_FILE = BASE_DIR / "deploy_info.json"

# 배포 시 덮어쓸 파일 확장자
DEPLOY_EXTENSIONS = {".py", ".bat"}

# 절대 덮어쓰지 않을 파일/디렉터리
DEPLOY_EXCLUDE = {
    ".env", "automation_targets.json", "deploy_info.json",
    "data", "pids", "log.log", ".venv", "__pycache__",
    ".git", ".github", ".claude",
}


def _file_hash(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None


def _check_running_jobs() -> list[str]:
    running = []
    with PROC_LOCK:
        for job in JOB_CONFIG:
            _update_status_from_proc(job)
            if CURRENT_PROC[job] is not None:
                running.append(job)
    return running


def _download_release(release_url: str, github_token: str, dest_path: str) -> None:
    headers = {}
    if github_token:
        # Private repo: GitHub API 경유
        api_url = release_url
        if "api.github.com" not in release_url:
            # 일반 URL -> API URL 변환은 콘솔에서 처리하므로 직접 다운로드 시도
            headers["Authorization"] = f"token {github_token}"
        else:
            headers["Authorization"] = f"token {github_token}"
            headers["Accept"] = "application/octet-stream"
    req = UrlRequest(release_url, headers=headers)
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_ctx = ssl.create_default_context()
    with urlopen(req, timeout=120, context=ssl_ctx) as resp, open(dest_path, "wb") as f:
        shutil.copyfileobj(resp, f)


def _run_pip_install() -> str:
    venv_pip = BASE_DIR / ".venv" / "Scripts" / "pip.exe"
    pip_cmd = str(venv_pip) if venv_pip.exists() else sys.executable + " -m pip"
    try:
        result = subprocess.run(
            [str(venv_pip) if venv_pip.exists() else sys.executable, "-m", "pip",
             "install", "-r", str(BASE_DIR / "requirements.txt"),
             "--quiet", "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=180,
            cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            return f"실패: {result.stderr[:500]}"
        return "성공"
    except Exception as e:
        return f"오류: {e}"


def _execute_deploy(release_url: str, github_token: str = "") -> dict:
    tmp_dir = tempfile.mkdtemp(prefix="mume_deploy_")
    zip_path = os.path.join(tmp_dir, "release.zip")
    extract_dir = os.path.join(tmp_dir, "extracted")

    try:
        # 1) zip 다운로드
        _download_release(release_url, github_token, zip_path)

        # 2) 압축 해제
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # 3) 파일 비교 & 덮어쓰기
        updated_files = []
        requirements_changed = False
        agent_changed = False

        for root, dirs, files in os.walk(extract_dir):
            # 제외 디렉터리 스킵
            dirs[:] = [d for d in dirs if d not in DEPLOY_EXCLUDE]

            rel_root = os.path.relpath(root, extract_dir)
            for fname in files:
                rel_path = os.path.join(rel_root, fname) if rel_root != "." else fname

                # 제외 대상 체크
                top_level = rel_path.split(os.sep)[0]
                if top_level in DEPLOY_EXCLUDE:
                    continue

                # 확장자 또는 특정 파일 체크
                ext = Path(fname).suffix
                is_deployable = ext in DEPLOY_EXTENSIONS or fname == "requirements.txt"
                if not is_deployable:
                    continue

                src = os.path.join(root, fname)
                dst = BASE_DIR / rel_path

                # 내용 비교
                src_hash = _file_hash(src)
                dst_hash = _file_hash(str(dst)) if dst.exists() else None

                if src_hash != dst_hash:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    updated_files.append(rel_path)

                    if fname == "requirements.txt":
                        requirements_changed = True
                    if fname == "hts_agent.py":
                        agent_changed = True

        # 4) requirements.txt 변경 시 pip install
        pip_result = None
        if requirements_changed:
            pip_result = _run_pip_install()

        # 5) 배포 정보 저장
        deploy_info = {
            "deployed_at": dt.datetime.now().isoformat(timespec="seconds"),
            "updated_files": updated_files,
            "release_url": release_url,
        }
        try:
            DEPLOY_INFO_FILE.write_text(
                json.dumps(deploy_info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        # 6) hts_agent.py 변경 시 재시작 예약
        restart_scheduled = False
        if agent_changed and platform.system() == "Windows":
            restart_scheduled = True
            threading.Thread(target=_delayed_restart, daemon=True).start()

        return {
            "updated_files": updated_files,
            "requirements_changed": requirements_changed,
            "pip_result": pip_result,
            "restart_scheduled": restart_scheduled,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _delayed_restart():
    """응답 전송 후 3초 뒤에 프로세스를 종료. hts_agent.bat 래퍼가 자동 재시작."""
    import time
    time.sleep(3)
    write_log("DEPLOY_RESTART", "deploy", "에이전트 재시작 (hts_agent.bat 래퍼가 자동 재실행)")
    os._exit(0)


@app.post("/deploy")
def deploy(payload: dict = Body(...)):
    release_url: str = payload.get("release_url", "")
    sha: str = payload.get("sha", "")
    github_token: str = payload.get("github_token", "")

    if not release_url:
        raise HTTPException(status_code=400, detail="release_url은 필수입니다.")

    # 실행 중인 작업이 있으면 배포 거부
    running_jobs = _check_running_jobs()
    if running_jobs:
        raise HTTPException(
            status_code=409,
            detail=f"실행 중인 작업이 있어 배포할 수 없습니다: {', '.join(running_jobs)}"
        )

    write_log("DEPLOY_START", "deploy", f"sha={sha}, url={release_url}")

    try:
        result = _execute_deploy(release_url, github_token)
        write_log("DEPLOY_SUCCESS", "deploy", json.dumps(result, ensure_ascii=False))
        return {"status": "ok", **result}
    except Exception as e:
        write_log("DEPLOY_ERROR", "deploy", str(e))
        raise HTTPException(status_code=500, detail=f"배포 실패: {e}")


@app.get("/deploy-status")
def deploy_status():
    if not DEPLOY_INFO_FILE.exists():
        return {"deployed_at": None, "message": "배포 이력이 없습니다."}
    try:
        data = json.loads(DEPLOY_INFO_FILE.read_text(encoding="utf-8"))
        return data
    except Exception:
        return {"deployed_at": None, "message": "배포 정보를 읽을 수 없습니다."}
