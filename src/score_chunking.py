# -*- coding: utf-8 -*-
"""
① 자르기 점수.

지표를 두 번 고쳤다. 그 과정 자체가 기록이다.

  1차: 'evidence_span 이 한 조각에 있는가' → 기준선·구조인식 모두 100점. 변별력 없음.
       스팬이 평균 68자라 1000자 조각이면 거의 항상 들어간다.

  2차: '체크리스트 수치가 그 한 조각에 모여 있는가' → 58~62점.
       변별력은 생겼지만 전제가 틀렸다. 확인해 보니 낮은 문항은 전부 extra_evidence 가 있었다.
       (Q-018은 클래스별 보수를 비교하려고 p4/p7/p20/p24 네 곳이 필요하다)
       근거가 여러 곳에 흩어진 건 정상이고, RAG 는 top-k 로 여러 조각을 가져온다.

  3차(현재): 자르기가 실제로 책임지는 것만 잰다.

    A. 스팬 보존율   evidence_span 이 끊기지 않고 한 조각에 있는가        (통과 기준, 상한선)
    B. 근거 커버율   지정된 근거 위치들의 조각을 모으면 체크리스트가 채워지는가
    D. 검색 부담     그 근거를 담는 데 조각이 몇 개나 필요한가 (적을수록 ④찾기가 쉬워진다)
    C. 표 헤더 동반율 표행이 든 조각에 헤더가 함께 있는가 (우리 데이터 특성)
"""
import os, sys, json, re, io
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunker import build_all

ROOT = r"C:\Users\장선우\Desktop\미래에셋"
EX = os.path.join(ROOT, 'extracted')
EVAL = os.path.join(ROOT, 'eval', 'evalset.json')

# 단위가 붙은 수치
NUM_RE = re.compile(r'\d[\d,]*(?:\.\d+)?\s*(?:%|만원|원|시간|일분|일|개월|년|배|점|등급|호)')
# 단위 없는 소수 — 투자설명서 표는 단위(%)가 헤더에만 있고 셀에는 숫자만 있다
BARE_RE = re.compile(r'\d+\.\d{2,}')


def norm(s):
    return re.sub(r'\s+', '', s or '')


def numbers(texts):
    """수치 토큰 집합. 단위는 떼고 숫자만 남겨 비교한다(표 셀에 단위가 없는 경우 대응)."""
    out = set()
    for t in texts:
        for m in NUM_RE.finditer(t):
            tok = norm(m.group(0))
            out.add(tok)
            out.add(re.sub(r'[^\d.]', '', tok))     # 단위 뗀 형태도 함께
        for m in BARE_RE.finditer(t):
            out.add(m.group(0))
    out.discard('')
    return out


def evidence_locs(it):
    """(파일, 페이지) 목록. 페이지 0/None 이면 파일 전체."""
    locs = [(it.get('evidence_file', ''), it.get('evidence_page') or None)]
    extra = it.get('extra_evidence', '') or ''
    for part in re.split(r'[,\n]', extra):
        part = part.strip()
        if not part:
            continue
        m = re.match(r'(\S+?\.txt)\s*[:：]\s*(\d+)', part)
        if m:
            locs.append((m.group(1), int(m.group(2))))
        elif part.endswith('.txt'):
            locs.append((part, None))
    return locs


def table_header_rate(chunks):
    withrow = same = 0
    for c in chunks:
        lines = c['text'].split('\n')
        rows = [i for i, l in enumerate(lines) if '|' in l]
        if not rows:
            continue
        withrow += 1
        head = lines[rows[0]]
        if head.strip().startswith('[표]') or (rows[0] > 0 and lines[rows[0] - 1].strip() != ''):
            same += 1
    return same, withrow


def score(chunks, items, label):
    by_file = {}
    for c in chunks:
        by_file.setdefault(c['file'], []).append(c)

    spanhit = 0
    covers, burdens, weak = [], [], []
    n = 0
    for it in items:
        fn, span = it.get('evidence_file', ''), it.get('evidence_span', '')
        if fn == '(해당없음)' or it.get('trap') == '프롬프트공격':
            continue
        n += 1
        sn = norm(span)
        cand = [c for c in by_file.get(fn, []) if sn in norm(c['text'])]
        if cand:
            spanhit += 1

        # 지정된 근거 위치에 해당하는 조각 모으기
        pool = []
        for f2, p2 in evidence_locs(it):
            for c in by_file.get(f2, []):
                if p2 is None or c['page'] == p2:
                    pool.append(c)
        for c in cand:                      # 스팬을 담은 조각은 무조건 포함
            if c not in pool:
                pool.append(c)

        want = numbers(it.get('answer_checklist', []))
        if not want:
            covers.append(1.0)
        else:
            got = want & numbers([c['text'] for c in pool])
            r = len(got) / len(want)
            covers.append(r)
            if r < 0.6:
                weak.append(f"{it['id']}({len(got)}/{len(want)})")
        burdens.append(len(pool))

    A = spanhit / n * 100 if n else 0
    B = sum(covers) / len(covers) * 100 if covers else 0
    D = sum(burdens) / len(burdens) if burdens else 0
    same, withrow = table_header_rate(chunks)
    C = same / withrow * 100 if withrow else 0

    sizes = [len(c['text']) for c in chunks]
    print(f"\n=== {label} ===")
    print(f"  조각 {len(chunks):,}개 · 평균 {sum(sizes)//len(sizes)}자 · 최대 {max(sizes)}자")
    print(f"  A 스팬 보존율     {A:5.1f}점")
    print(f"  B 근거 커버율     {B:5.1f}점   ← 핵심")
    print(f"  C 표 헤더 동반율  {C:5.1f}점   ({same:,}/{withrow:,})")
    print(f"  D 검색 부담       {D:5.1f}조각/문항  (낮을수록 좋음)")
    if weak:
        print(f"  커버 미달: {', '.join(weak[:10])}")
    return {'label': label, 'chunks': len(chunks),
            'A': round(A, 1), 'B': round(B, 1), 'C': round(C, 1), 'D': round(D, 1)}


def main():
    items = json.load(io.open(EVAL, encoding='utf-8'))['items']
    res = []
    print("자르는 중...")
    for kw, lab in [
        (dict(mode='baseline', size=1000, overlap=100), "기준선  1000자 / 겹침 100자"),
        (dict(mode='baseline', size=1000, overlap=300), "기준선+ 1000자 / 겹침 300자"),
        (dict(mode='structured', size=1200, overlap=300), "구조인식 1200자 / 겹침 300자"),
        (dict(mode='structured', size=1800, overlap=300), "구조인식 1800자 / 겹침 300자"),
        (dict(mode='structured', size=2400, overlap=300), "구조인식 2400자 / 겹침 300자"),
    ]:
        res.append(score(build_all(EX, **kw), items, lab))

    json.dump(res, io.open(os.path.join(ROOT, 'eval', '_chunk_score.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print("\n" + "=" * 72)
    print(f"  {'A스팬':>6} {'B커버':>6} {'C헤더':>6} {'D부담':>6}  {'조각수':>8}  방식")
    for r in res:
        print(f"  {r['A']:6.1f} {r['B']:6.1f} {r['C']:6.1f} {r['D']:6.1f}  {r['chunks']:8,}  {r['label']}")


if __name__ == '__main__':
    main()
