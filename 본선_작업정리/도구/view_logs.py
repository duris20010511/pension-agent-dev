"""질문 기록(logs/queries-YYYYMMDD.jsonl)을 사람이 읽기 쉬운 화면으로 — 터미널 요약 + HTML 한 장.

  python -X utf8 본선_작업정리/도구/view_logs.py logs/server --html logs/server/질문기록.html
  python -X utf8 본선_작업정리/도구/view_logs.py logs/queries-20261013.jsonl -n 10

서버 기록은 `bash deploy/deploy.sh pull-logs <공인IP>` 가 받아 와서 이 스크립트로 HTML 을 만든다.
HTML 은 바깥 파일·스크립트 없이 한 파일이고, 기록의 글자는 모두 이스케이프한다(답 속 태그가 화면에서 실행되지 않게).
기록에는 고객 질문이 들어 있다 — 만든 HTML 을 공유·게시하지 않는다(logs/ 는 .gitignore).
"""
import argparse
import datetime
import glob
import html
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')


def load(paths):
    files = []
    for p in paths:
        files += sorted(glob.glob(os.path.join(p, 'queries-*.jsonl'))) if os.path.isdir(p) else [p]
    rows = []
    for f in files:
        with open(f, encoding='utf-8') as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                r['_file'] = os.path.basename(f)
                rows.append(r)
    rows.sort(key=lambda r: r.get('ts', ''))
    return rows, files


def to_kst(rows):
    """서버 컨테이너(python:3.11-slim)는 UTC 로 시각을 적는다 — 한국 시각(+9시간)으로 바꿔 보인다."""
    for r in rows:
        try:
            t = datetime.datetime.strptime(r.get('ts', ''), '%Y-%m-%dT%H:%M:%S') + datetime.timedelta(hours=9)
            r['ts'] = t.strftime('%Y-%m-%dT%H:%M:%S')
        except ValueError:
            pass


def channel(r):
    return '시연 /chat' if r.get('question_id') == 'chat' else '대회 규격 /answer'


def terminal(rows, n):
    print(f'기록 {len(rows)}건 (시연 {sum(channel(r).startswith("시연") for r in rows)} · '
          f'대회 규격 {sum(not channel(r).startswith("시연") for r in rows)}) — 최근 {min(n, len(rows))}건')
    for r in rows[-n:]:
        q = (r.get('question') or '').replace('\n', ' ')
        print(f"- {r.get('ts', '')} [{channel(r)}] {r.get('elapsed_s', '')}초 · 답 {r.get('answer_chars', 0)}자"
              + (f" · 오류 {r['error']}" if r.get('error') else '') + f"\n  Q. {q[:120]}")


CSS = """
:root { --bg:#f3f5f8; --card:#fff; --ink:#16202c; --muted:#5a6577; --line:#dde3eb; --navy:#043B72; --orange:#F58220; --bad:#c92a2a; }
@media (prefers-color-scheme: dark) { :root { --bg:#12161c; --card:#1b2129; --ink:#e6eaf0; --muted:#9aa5b5; --line:#2c3440; --navy:#8fb8ea; --orange:#f5a25a; --bad:#ff8a8a; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.6 system-ui,-apple-system,'Malgun Gothic',sans-serif; }
header { position:sticky; top:0; background:var(--card); border-bottom:1px solid var(--line); padding:12px 16px; z-index:1; }
h1 { margin:0 0 6px; font-size:18px; color:var(--navy); }
.sum { color:var(--muted); font-size:13px; }
.bar { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
input, select { font:inherit; padding:6px 10px; border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--ink); }
input { flex:1; min-width:180px; }
main { max-width:980px; margin:0 auto; padding:12px 16px 40px; }
.row { background:var(--card); border:1px solid var(--line); border-radius:12px; margin:10px 0; padding:10px 14px; }
.meta { display:flex; flex-wrap:wrap; gap:6px 12px; font-size:13px; color:var(--muted); }
.tag { border-radius:6px; padding:0 6px; font-weight:700; }
.t-chat { background:#fff3e8; color:#b45309; } .t-ans { background:#e9f0f8; color:#043B72; }
@media (prefers-color-scheme: dark) { .t-chat { background:#3a2a17; color:#f5a25a; } .t-ans { background:#1c2a3b; color:#8fb8ea; } }
.err { color:var(--bad); font-weight:700; }
.q { font-weight:700; margin:6px 0 2px; overflow-wrap:anywhere; }
.sa { color:var(--muted); font-size:13px; overflow-wrap:anywhere; }
details { margin-top:6px; } summary { cursor:pointer; color:var(--navy); font-size:14px; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; background:var(--bg); border-radius:8px; padding:10px; font:14px/1.6 inherit; margin:6px 0 0; }
.kv { font-size:13px; color:var(--muted); overflow-wrap:anywhere; }
.empty { color:var(--muted); text-align:center; padding:40px 0; }
"""

JS = """
const rows=[...document.querySelectorAll('.row')], q=document.getElementById('q'), ch=document.getElementById('ch'), day=document.getElementById('day'), cnt=document.getElementById('cnt');
function apply(){const s=q.value.trim().toLowerCase();let n=0;for(const r of rows){const ok=(!s||r.dataset.text.includes(s))&&(!ch.value||r.dataset.ch===ch.value)&&(!day.value||r.dataset.day===day.value);r.hidden=!ok;if(ok)n++;}cnt.textContent=n+'건 표시';}
q.addEventListener('input',apply);ch.addEventListener('change',apply);day.addEventListener('change',apply);apply();
"""


def build_html(rows, files):
    e = html.escape
    days = sorted({r.get('ts', '')[:10] for r in rows if r.get('ts')})
    n_chat = sum(channel(r).startswith('시연') for r in rows)
    items = []
    for r in reversed(rows):                       # 최신이 위
        ch = 'chat' if channel(r).startswith('시연') else 'answer'
        text = ' '.join(str(r.get(k) or '') for k in ('question', 'standalone', 'answer')).lower()
        kv = [('가드', r.get('guard')), ('유형', r.get('type')), ('대상 필터', r.get('policy_filter')),
              ('상위 근거', r.get('top')), ('세션', r.get('session')), ('파일', r.get('_file'))]
        items.append(
            f'<div class="row" data-ch="{ch}" data-day="{e(r.get("ts", "")[:10])}" data-text="{e(text)}">'
            f'<div class="meta"><span>{e(r.get("ts", ""))}</span>'
            f'<span class="tag {"t-chat" if ch == "chat" else "t-ans"}">{e(channel(r))}</span>'
            f'<span>{e(str(r.get("elapsed_s", "")))}초</span><span>답 {e(str(r.get("answer_chars", 0)))}자</span>'
            + (f'<span class="err">오류 {e(str(r["error"]))}</span>' if r.get('error') else '') + '</div>'
            f'<div class="q">{e(r.get("question") or "(빈 질문)")}</div>'
            + (f'<div class="sa">재작성: {e(r["standalone"])}</div>' if r.get('standalone') else '')
            + '<details><summary>답 전문과 처리 정보</summary>'
            f'<pre>{e(r.get("answer") or "")}</pre>'
            + ''.join(f'<div class="kv">{e(k)}: {e(str(v))}</div>' for k, v in kv if v)
            + '</details></div>')
    body = ''.join(items) or '<p class="empty">기록이 없어요.</p>'
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>질문 기록</title>'
            f'<style>{CSS}</style></head><body><header><h1>질문 기록</h1>'
            f'<div class="sum">{len(rows)}건(한국 시각) · 시연 {n_chat} · 대회 규격 {len(rows) - n_chat} · 파일 {len(files)}개 '
            '· 주민번호·전화번호·계좌번호 꼴은 서버에서 가린 값 · 고객 질문이 들어 있으니 공유하지 마세요</div>'
            '<div class="bar"><input id="q" placeholder="질문·답에서 찾기">'
            '<select id="ch"><option value="">전체 경로</option><option value="chat">시연 /chat</option>'
            '<option value="answer">대회 규격 /answer</option></select>'
            '<select id="day"><option value="">전체 날짜</option>'
            + ''.join(f'<option>{e(d)}</option>' for d in days)
            + '</select><span id="cnt" class="sum"></span></div></header>'
            f'<main>{body}</main><script>{JS}</script></body></html>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+', help='queries-*.jsonl 파일이나 그 파일들이 있는 폴더')
    ap.add_argument('-n', type=int, default=15, help='터미널에 보일 최근 건수')
    ap.add_argument('--html', help='HTML 로 쓸 경로')
    ap.add_argument('--utc', action='store_true', help='기록 시각이 UTC(본선 서버)면 한국 시각으로 바꿔 보임')
    a = ap.parse_args()
    rows, files = load(a.paths)
    if a.utc:
        to_kst(rows)
    terminal(rows, a.n)
    if a.html:
        with open(a.html, 'w', encoding='utf-8') as fh:
            fh.write(build_html(rows, files))
        print(f'\nHTML: {a.html}')


if __name__ == '__main__':
    main()
