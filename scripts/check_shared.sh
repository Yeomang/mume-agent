#!/bin/sh
# mume과 VR이 똑같이 써야 하는 파일이 같은지 확인한다 (네 저장소가 같은 폴더 아래에 있어야 함).
# 다르면 1로 끝난다 — 한쪽을 고쳤다면 다른 쪽에도 그대로 복사한 뒤 각각 커밋한다.
# 이 스크립트도 mume-agent·vr-agent·mume-console·vr-console에 같은 내용으로 둔다.
cd "$(dirname "$0")/../.." || exit 1
status=0
check() {
  if [ ! -f "$1" ] || [ ! -f "$2" ]; then
    echo "건너뜀: $1 / $2 (저장소가 없음)"
  elif cmp -s "$1" "$2"; then
    echo "같음: $1 = $2"
  else
    echo "다름: $1 ≠ $2 — 맞춰 주세요"
    status=1
  fi
}
check mume-agent/cert_guard.py vr-agent/cert_guard.py
check mume-agent/hts_popups.py vr-agent/hts_popups.py
check mume-console/static/js/cert-notice.js vr-console/static/cert-notice.js
for r in vr-agent mume-console vr-console; do
  check mume-agent/scripts/check_shared.sh "$r/scripts/check_shared.sh"
done
exit $status
