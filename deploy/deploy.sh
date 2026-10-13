#!/usr/bin/env bash
# 본선 서버 배포 — 로컬(Git Bash)에서 실행한다. 예선 배포가이드.md 와 같은 방식(Docker, 80번 포트)이고,
# 수정할 때마다 'push' 한 번으로 묶기 → 전송 → 빌드 → 교체 → 확인까지 한다. 확인에 실패하면 이전 판으로 되돌린다.
#
#   bash deploy/deploy.sh setup  <공인IP>   처음 한 번: 서버에 Docker 설치, 폴더 만들기
#   bash deploy/deploy.sh push   <공인IP>   지금 코드로 배포
#   bash deploy/deploy.sh status <공인IP>   돌고 있는 판과 /health
#   bash deploy/deploy.sh logs   <공인IP>   서버 로그 마지막 100줄
#   bash deploy/deploy.sh pull-logs <공인IP> 질문 기록을 받아 logs/server/질문기록.html 로 만들기
#   bash deploy/deploy.sh pack              묶기만(전송 없음) — 묶음 확인용
#
# 서버 준비(사람이 한다): /root/.env 에 CLOVA 키(로컬 .env 와 같은 내용), /root/.ssh/authorized_keys 에 배포 키의 공개키.
# 접속 키: 기본 ~/.ssh/pension_deploy (DEPLOY_KEY 로 바꿀 수 있음), 사용자: root (DEPLOY_USER).
set -euo pipefail

cmd=${1:-}
host=${2:-}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
KEY=${DEPLOY_KEY:-$HOME/.ssh/pension_deploy}
USR=${DEPLOY_USER:-root}
# known_hosts 경로를 직접 준다 — 한글 사용자 폴더면 ssh 가 기본 경로를 깨뜨려 저장하지 못한다(10-13)
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o "UserKnownHostsFile=$HOME/.ssh/known_hosts")

need_host() { [ -n "$host" ] || { echo "공인 IP 를 주세요: bash deploy/deploy.sh $cmd <공인IP>"; exit 2; }; }
remote() { ssh "${SSH_OPTS[@]}" "$USR@$host" "$@"; }

pack() {
  # 판 이름 = 커밋 + (커밋 안 된 변경이 있으면 -dirty) + 시각 — 어떤 코드가 올라갔는지 /health 의 version 으로 확인한다
  local sha dirty
  sha=$(git -C "$ROOT" rev-parse --short HEAD)
  dirty=$([ -n "$(git -C "$ROOT" status --porcelain -- src data extracted Dockerfile requirements.txt)" ] && echo -dirty || true)
  VER="${sha}${dirty}-$(date +%m%d%H%M)"
  # 색인·임베딩 캐시는 지금 코드의 지문에 맞는 두 파일만(.cache 전체는 426MB, 필요한 것은 약 70MB)
  local fp
  fp=$(cd "$ROOT/src" && python -X utf8 -c "import answer; print(answer._fingerprint())" 2>/dev/null | tail -1)
  for f in "index_${fp}.pkl" "emb_${fp}.npy"; do
    [ -f "$ROOT/.cache/$f" ] || { echo "캐시 $f 가 없습니다 — 로컬에서 서버를 한 번 띄워 색인을 만든 뒤 다시 하세요"; exit 1; }
  done
  echo "$VER" > "$ROOT/VERSION"
  PKG="$ROOT/deploy/pension-$VER.tar.gz"
  tar --exclude='__pycache__' --exclude='*.pyc' -czf "$PKG" -C "$ROOT" \
      src extracted data Dockerfile requirements.txt .dockerignore VERSION \
      ".cache/index_${fp}.pkl" ".cache/emb_${fp}.npy"
  rm -f "$ROOT/VERSION"
  echo "묶음: $(basename "$PKG") ($(du -h "$PKG" | cut -f1)), 지문 $fp"
}

case "$cmd" in
  pack)
    pack
    ;;
  setup)
    need_host
    remote 'set -e
      if ! command -v docker >/dev/null; then
        apt-get update -y && apt-get install -y docker.io curl || curl -fsSL https://get.docker.com | sh
      fi
      systemctl enable --now docker
      mkdir -p /root/deploy /root/logs
      docker --version
      [ -f /root/.env ] && echo ".env 있음" || echo "주의: /root/.env 가 없습니다 — CLOVA 키를 넣어 주세요"'
    ;;
  push)
    need_host
    pack
    remote 'test -f /root/.env' || { echo "서버에 /root/.env 가 없습니다 — 키 파일을 먼저 올려 주세요"; exit 1; }
    scp "${SSH_OPTS[@]}" "$PKG" "$USR@$host:/root/deploy/"
    remote "set -e
      V='$VER'
      mkdir -p /root/app-\$V && tar -xzf /root/deploy/pension-\$V.tar.gz -C /root/app-\$V
      cd /root/app-\$V && docker build -q -t pension-agent:\$V . >/dev/null
      PREV=\$(docker inspect -f '{{.Config.Image}}' pension 2>/dev/null || true)
      docker rm -f pension >/dev/null 2>&1 || true
      docker run -d --name pension --restart unless-stopped -p 80:80 --env-file /root/.env \
        -e AGENT_VERSION=\$V -v /root/logs:/app/logs pension-agent:\$V >/dev/null
      for i in \$(seq 1 90); do
        if curl -sf localhost/health 2>/dev/null | grep -q '\"embeddings\": *\\['; then
          echo \"배포 완료: \$V (\$((i*2))초)\"; curl -s localhost/health; echo; exit 0
        fi
        sleep 2
      done
      echo \"새 판이 3분 안에 뜨지 않음 — 로그:\"; docker logs --tail 40 pension || true
      if [ -n \"\$PREV\" ]; then
        docker rm -f pension >/dev/null 2>&1 || true
        docker run -d --name pension --restart unless-stopped -p 80:80 --env-file /root/.env -v /root/logs:/app/logs \$PREV >/dev/null
        echo \"이전 판(\$PREV)으로 되돌림\"
      fi
      exit 1"
    echo "밖에서 확인: http://$host/health · 시연 화면 http://$host/demo"
    curl -s -m 20 "http://$host/health" | head -c 400 || echo "(밖에서 /health 가 안 열림 — ACG 80번 규칙 확인)"
    echo
    ;;
  status)
    need_host
    remote "docker ps --filter name=pension --format '{{.Image}} {{.Status}}'; curl -s localhost/health; echo"
    ;;
  logs)
    need_host
    remote 'docker logs --tail 100 pension'
    ;;
  pull-logs)
    # 질문 기록(/root/logs/queries-*.jsonl)을 이 PC 의 logs/server/ 로 받아 읽기 쉬운 화면(logs/server/질문기록.html)으로 만든다.
    # logs/ 는 .gitignore — 저장소에 들어가지 않는다
    need_host
    mkdir -p "$ROOT/logs/server"
    scp "${SSH_OPTS[@]}" "$USR@$host:/root/logs/queries-*.jsonl" "$ROOT/logs/server/"
    python -X utf8 "$ROOT/본선_작업정리/도구/view_logs.py" "$ROOT/logs/server" --utc --html "$ROOT/logs/server/질문기록.html"
    ;;
  *)
    sed -n '2,13p' "$0"
    exit 2
    ;;
esac
