# C:\mume_meritz\job_control.py

from pathlib import Path
import os
from typing import List

BASE_DIR = Path(__file__).resolve().parent
PID_DIR = BASE_DIR / "pids"
PID_DIR.mkdir(exist_ok=True)


def _pid_file(job: str) -> Path:
    """각 job(morning/evening/aftermarket) 별 PID 리스트 파일 경로"""
    return PID_DIR / f"{job}.pid"


def register_job_pid(job: str, pid: int | None = None) -> None:
    """
    현재 프로세스(또는 지정 pid)를 해당 job의 PID 목록에 추가.
    스케줄러로 실행되든, 웹UI에서 실행되든 호출만 해주면 됨.
    """
    if pid is None:
        pid = os.getpid()

    path = _pid_file(job)
    existing: List[int] = []

    if path.exists():
        try:
            txt = path.read_text(encoding="utf-8")
            for line in txt.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    existing.append(int(line))
                except ValueError:
                    continue
        except Exception:
            # 파일 깨져 있어도 메인 로직 죽지 않게
            existing = []

    if pid not in existing:
        existing.append(pid)

    try:
        path.write_text("\n".join(str(p) for p in existing), encoding="utf-8")
    except Exception:
        # 기록 실패해도 메인 로직은 계속
        pass


def unregister_job_pid(job: str, pid: int | None = None) -> None:
    """
    해당 job의 PID 목록에서 현재 프로세스(또는 지정 pid)를 제거.
    (정상 종료 시 호출)
    """
    if pid is None:
        pid = os.getpid()

    path = _pid_file(job)
    if not path.exists():
        return

    try:
        txt = path.read_text(encoding="utf-8")
        kept: List[int] = []
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                v = int(line)
            except ValueError:
                continue
            if v != pid:
                kept.append(v)

        if kept:
            path.write_text("\n".join(str(p) for p in kept), encoding="utf-8")
        else:
            # 아무 PID도 안 남으면 파일 삭제
            path.unlink(missing_ok=True)
    except Exception:
        # 실패해도 그냥 무시
        pass


def read_job_pids(job: str) -> List[int]:
    """
    해당 job에 등록된 PID 목록 반환.
    (STOP 시점에서 api_server가 사용)
    """
    path = _pid_file(job)
    if not path.exists():
        return []

    pids: List[int] = []
    try:
        txt = path.read_text(encoding="utf-8")
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pids.append(int(line))
            except ValueError:
                continue
    except Exception:
        return []

    return pids
