"""
수동 코드 업데이트 스크립트 (git 없이 사용 가능)

사용법: python update.py
또는:   .venv\Scripts\python.exe update.py

콘솔 서버 프록시 또는 GitHub에서 최신 코드를 다운받아 덮어씁니다.
"""

import os
import sys
import zipfile
import tempfile
import shutil
import ssl
from pathlib import Path
from urllib.request import urlopen, Request

BASE_DIR = Path(__file__).resolve().parent
RELEASE_URL = "https://github.com/Yeomang/mume-agent/releases/download/latest/mume-agent.zip"

# 덮어쓰지 않을 파일/폴더
EXCLUDE = {".env", ".venv", "data", "__pycache__", ".git", ".claude", "update.py"}
# 배포 대상 확장자
EXTENSIONS = {".py", ".bat", ".txt"}


def download(url, dest):
    print(f"  다운로드 중: {url}")
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    req = Request(url, headers={"User-Agent": "mume-agent-updater"})
    with urlopen(req, timeout=120, context=ctx) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)
    print(f"  다운로드 완료: {os.path.getsize(dest)} bytes")


def main():
    print("=" * 50)
    print("  mume-agent 수동 업데이트")
    print("=" * 50)
    print()

    tmp_dir = tempfile.mkdtemp(prefix="mume_update_")
    zip_path = os.path.join(tmp_dir, "release.zip")
    extract_dir = os.path.join(tmp_dir, "extracted")

    try:
        # 1) 다운로드
        download(RELEASE_URL, zip_path)

        # 2) 압축 해제
        print("  압축 해제 중...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # 3) 파일 비교 & 덮어쓰기
        updated = []
        for root, dirs, files in os.walk(extract_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE]
            rel_root = os.path.relpath(root, extract_dir)
            for fname in files:
                rel_path = os.path.join(rel_root, fname) if rel_root != "." else fname
                if rel_path.split(os.sep)[0] in EXCLUDE:
                    continue
                ext = Path(fname).suffix
                if ext not in EXTENSIONS and fname != "requirements.txt":
                    continue

                src = os.path.join(root, fname)
                dst = BASE_DIR / rel_path

                # 내용 비교
                if dst.exists():
                    with open(src, "rb") as a, open(dst, "rb") as b:
                        if a.read() == b.read():
                            continue

                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                updated.append(rel_path)

        if updated:
            print(f"\n  업데이트된 파일 ({len(updated)}개):")
            for f in updated:
                print(f"    - {f}")
        else:
            print("\n  이미 최신 버전입니다.")

        print("\n  완료!")

    except Exception as e:
        print(f"\n  오류: {e}")
        sys.exit(1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
