# -*- coding: utf-8 -*-
"""
온톨로지(ontology.py · data/ontology.json) 검사 — API 없이 돈다.  python -X utf8 src/test_ontology.py

무엇을 고정하나
  1. 규칙 노드 = taxcalc.RULEBOOK (번호·이름). 한쪽만 바뀌면 시연 화면의 규칙표와 그래프가 어긋난다.
  2. 07_연금온톨로지.md 와 일치 — 클래스(§2), 관계(§3), 규칙 근거 문서(§5), 재원 × 인출 표 9칸(§6).
     '부득이 × 퇴직금'은 07 이 '문서 확인 필요'로 남긴 칸이라 세목을 지어내면 안 된다.
  3. graph() 노드 60개 이하, 엣지 양끝이 모두 노드.
  4. path_for — 장면 2 질문에서 빠진 속성(평가액·연차·퇴직소득세)을 짚고, 값이 다 있으면
     taxcalc 공용 함수로 손계산과 같은 금액을 낸다.
  5. 문서 꼬리표(doc_meta)가 있으면 독자·기준일이 붙고, 없어도 동작한다.
  6. (검토 추가) path_for 금액 = 답변 계산 블록(taxcalc.compute) 금액. 인출 순서가 안 정해지면 금액을 내지 않는다.
     55세 전 해지에 ① 금액만 알 때·퇴직금만 말하고 더 뺄 때·ISA 전환·50대·0년차·이상한 입력.
  7. (검토 추가) 검사가 실제로 잡는지 — 규칙 이름·표 칸을 일부러 바꾼 사본에서 차이가 나와야 한다(변이 시험).
  8. (검토 추가) 근거 인용(quote·evidence)은 문서에 글자 그대로, 경로 문구에 판단 표현·HTML 태그 없음,
     ontology.py 에 세법 숫자(5,500만·16.5%·11년차 …)를 다시 적지 않음(taxcalc 공용 함수로만).
"""
import ast
import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ontology  # noqa: E402
import taxcalc  # noqa: E402

ROOT = ontology.ROOT
MD07 = os.path.join(ROOT, '현서_2차', '현서_2차', '07_연금온톨로지.md')
SCENE2 = '만 62세, IRP에 퇴직금이랑 공제받은 돈이 섞였는데 올해 1,800만 원 빼면 세금은?'
SCENE2_FULL = ('만 62세, IRP 평가액 1억 원(퇴직금 6,000만 원, 공제받은 돈 4,000만 원), 퇴직소득세 실효세율 4%, '
               '연금수령 1년차, 올해 1,800만 원 빼면 세금은?')
BANNED = re.compile(r'추천|최적|유리|가장 좋은')

results = []          # (이름, 통과 여부, 설명)


def check(name, ok, why=''):
    results.append((name, bool(ok), why))


def _norm(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def _docs(s):
    """'1, 5, 14, 46~50, 55' → [1, 5, 14, 46, 47, 48, 49, 50, 55]."""
    out = []
    for part in re.split(r'\s*,\s*', s.strip()):
        m = re.match(r'^(\d+)\s*~\s*(\d+)$', part)
        if m:
            out += list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def _section(md, head):
    """'## 3.' 같은 머리말부터 다음 '## ' 직전까지."""
    i = md.find(head)
    if i < 0:
        return ''
    j = md.find('\n## ', i + len(head))
    return md[i:j if j >= 0 else len(md)]


def _rows(block):
    """마크다운 표 행 → 칸 목록(머리·구분 행 제외)."""
    out = []
    for ln in block.splitlines():
        if not ln.startswith('|') or re.match(r'^\|\s*-', ln):
            continue
        out.append([c.strip() for c in ln.strip().strip('|').split('|')])
    return out


# ---------------------------------------------------------------------------
# 1. RULEBOOK 과 규칙 노드
# ---------------------------------------------------------------------------
def rulebook_mismatch(o):
    """온톨로지 규칙 노드와 RULEBOOK 의 (번호, 이름) 차이 — 변이 시험에서도 쓴다."""
    book = [(rid, name) for rid, name, _, _, _ in taxcalc.RULEBOOK]
    mine = [(r['id'], r['name']) for r in o['rules']]
    return [x for x in book if x not in mine] + [x for x in mine if x not in book]


def test_rulebook():
    o = ontology.load()
    book = [(rid, name) for rid, name, _, _, _ in taxcalc.RULEBOOK]
    mine = [(r['id'], r['name']) for r in o['rules']]
    check('규칙 (번호, 이름) 차이 없음', not rulebook_mismatch(o), str(rulebook_mismatch(o)))
    check('규칙 번호 목록 = RULEBOOK', [r for r, _ in mine] == [r for r, _ in book],
          f'온톨로지 {[r for r, _ in mine]} / RULEBOOK {[r for r, _ in book]}')
    names = dict(book)
    for rid, name in mine:
        check(f'{rid} 이름 = RULEBOOK', names.get(rid) == name, f'온톨로지 {name!r} / RULEBOOK {names.get(rid)!r}')
    # 규칙 원문 문서가 taxcalc.RULES 의 파일과 같다(원문 대조된 문서만 quoted)
    for rid, _, keys, _, _ in taxcalc.RULEBOOK:
        want = list(dict.fromkeys(taxcalc.RULES[k][0] for k in keys))
        got = [d['file'] for d in ontology.rule_docs(rid) if d['quoted']]
        check(f'{rid} 원문 문서 = RULES 파일', got == want, f'{got} ≠ {want}')


# ---------------------------------------------------------------------------
# 2. 07 문서와 일치
# ---------------------------------------------------------------------------
CELL_07 = {   # 07 §6 표 — 07 파일이 없을 때도 이 값으로 검사한다(2026-10-08 판)
    ('free', '연금수령'): ('비과세', []), ('free', '부득이'): ('비과세', []), ('free', '연금외수령'): ('비과세', []),
    ('retire', '연금수령'): ('퇴직소득세', ['R-09']), ('retire', '부득이'): ('문서 확인 필요', []),
    ('retire', '연금외수령'): ('퇴직소득세', ['R-10']),
    ('credit', '연금수령'): ('연금소득세', ['R-07', 'R-08']), ('credit', '부득이'): ('연금소득세', ['R-11']),
    ('credit', '연금외수령'): ('기타소득세', ['R-10']),
}


def test_tax_route():
    for (s, m), (tax, rids) in CELL_07.items():
        r = ontology.tax_route(s, m)
        check(f'tax_route({s}, {m}) 세목 = {tax}', r['tax'] == tax, f"결과 {r['tax']!r}")
        check(f'tax_route({s}, {m}) 07 규칙 번호 포함', all(x in r['rule_ids'] for x in rids), f"{r['rule_ids']} ⊉ {rids}")
    r = ontology.tax_route('retire', '부득이')
    check('부득이 × 퇴직금 = 문서 확인 필요(세목·규칙을 지어내지 않음)',
          r['tax'] == '문서 확인 필요' and r['rule_ids'] == [] and '문서 확인 필요' in r['text'], str(r))
    check('tax_route 별칭(②, 부득이한 사유)', ontology.tax_route('②', '부득이한 사유')['tax'] == '문서 확인 필요')
    check('tax_route 노드 id', ontology.tax_route('credit', '연금외수령')['nodes'] == ['fund.credit', 'wd.other', 'tax.other'])
    try:
        ontology.tax_route('pension', '연금수령')
        check('tax_route 모르는 재원 → ValueError', False, '예외가 안 났다')
    except ValueError:
        check('tax_route 모르는 재원 → ValueError', True)
    plain = ' '.join(c['plain'] for c in ontology.load()['tax_matrix']['cells'])
    check('표 쉬운 말에 판단 표현 없음', not BANNED.search(plain), BANNED.findall(plain))


SRC_OF = {'①': 'free', '②': 'retire', '③': 'credit'}
MODE_OF = {'연금수령': '연금수령', '부득이한 사유': '부득이', '연금외수령': '연금외수령'}


def md07_cells(md):
    """07 §6 표 → {(재원, 인출 유형): 칸 글자}."""
    t = _rows(md[md.find('재원 ＼ 인출 유형') - 2:].split('\n\n')[0])
    head = t[0][1:]
    return {(SRC_OF[row[0][0]], MODE_OF[col]): cell for row in t[1:] for col, cell in zip(head, row[1:])}


def cell_mismatch(o, cells):
    """온톨로지 표 칸 글자가 07 과 다른 칸 목록 + '부득이 × 퇴직금'에 세목을 지어낸 경우 — 변이 시험에서도 쓴다."""
    got = {(c['source'], c['mode']): c for c in o['tax_matrix']['cells']}
    bad = [k for k, v in cells.items() if k not in got or _norm(got[k]['text']) != _norm(v)]
    c = got.get(('retire', '부득이'))
    if not c or c['tax'] != '문서 확인 필요' or c['rule_ids'] or c['tax_node']:
        bad.append(('retire', '부득이', '세목을 지어냄'))
    return bad


def test_md07():
    if not os.path.exists(MD07):
        print('  (07_연금온톨로지.md 없음 — 원문 대조 생략, 내장 기대값으로만 검사)')
        return
    md = open(MD07, encoding='utf-8').read()
    o = ontology.load()

    # §2 클래스 — 맨 윗줄 11개
    s2 = _section(md, '## 2.')
    tops = re.findall(r'^[├└]─ (\S[^(\n]*?)\s*(?:\(|$)', s2, re.M)
    labels = [c['label'] for c in o['classes'] if not c.get('added')]
    check('§2 최상위 클래스 = 11개', len(tops) == 11 and len(labels) == 11, f'07 {tops} / json {labels}')
    check('§2 클래스 이름 일치', [t.split(' ')[0] for t in tops] == [x.split(' ')[0] for x in labels], f'{tops} / {labels}')

    # §3 관계
    rel = [r[0] for r in _rows(_section(md, '## 3.'))[1:]]
    check('§3 관계 이름·순서 일치', rel == [r['name'] for r in o['relations']], f'{rel}')

    # §5 규칙 — 번호·근거 문서
    rows = [r for r in _rows(_section(md, '## 5.')) if re.match(r'^R-\d\d[a-z]?$', r[0])]
    rmap = o['rule_index']
    check('§5 규칙 행 = 15 + 보류 2', len(rows) == 17, f'{len(rows)}행')
    for r in rows:
        rid = r[0]
        want = [f'doc{n}.txt' for n in _docs(r[3])]
        got = rmap.get(rid, {}).get('docs')
        check(f'§5 {rid} 근거 문서 일치', got == want, f'{got} ≠ {want}')
        st = '⚠' if '⚠' in r[4] and rid.endswith(('a',)) else '✅'
        check(f'§5 {rid} 확정/보류 구분', rmap.get(rid, {}).get('status') == st, f"{rmap.get(rid, {}).get('status')} ≠ {st}")

    # §6 재원 × 인출 유형 표 9칸 — 칸 글자 그대로
    s6 = md[md.find('재원 ＼ 인출 유형') - 2:]
    t = _rows(s6.split('\n\n')[0])
    head = t[0][1:]
    mode_of = {'연금수령': '연금수령', '부득이한 사유': '부득이', '연금외수령': '연금외수령'}
    src_of = {'①': 'free', '②': 'retire', '③': 'credit'}
    n = 0
    for row in t[1:]:
        s = src_of[row[0][0]]
        for col, cell in zip(head, row[1:]):
            m = mode_of[col]
            r = ontology.tax_route(s, m)
            n += 1
            check(f'§6 {row[0][:2]} × {col} 칸 = tax_route.text', _norm(r['text']) == _norm(cell), f'{r["text"]!r} ≠ {cell!r}')
            for rid in re.findall(r'R-\d\d', cell):
                check(f'§6 {row[0][:2]} × {col} 규칙 {rid}', rid in r['rule_ids'], str(r['rule_ids']))
    check('§6 표 9칸', n == 9, f'{n}칸')
    check('§6 표 칸 차이 없음(cell_mismatch)', not cell_mismatch(o, md07_cells(md)), str(cell_mismatch(o, md07_cells(md))))


# ---------------------------------------------------------------------------
# 3. JSON 무결성
# ---------------------------------------------------------------------------
def test_json():
    o = ontology.load()
    nodes = set(o['index']) | {r['id'] for r in o['rules']}
    bad = [(e['from'], e['to']) for e in o['edges'] if e['from'] not in nodes or e['to'] not in nodes]
    check('엣지 양끝이 노드', not bad, str(bad))
    bad = [(r['id'], t) for r in o['rules'] for t in r['applies_to'] if t not in nodes]
    check('규칙 적용 대상이 노드', not bad, str(bad))
    bad = [(r['id'], c['attr']) for r in o['rules'] for c in r['conditions'] if c['attr'] not in o['attr_index']]
    check('규칙 조건이 속성', not bad, str(bad))
    bad = [c['tax_node'] for c in o['tax_matrix']['cells'] if c['tax_node'] and c['tax_node'] not in nodes]
    check('표 세목이 노드', not bad, str(bad))
    covered = {f for s in o['stages'] for f in s['docs']}
    miss = [n for n in range(1, 59) if f'doc{n}.txt' not in covered]
    check('문서 58건이 모두 단계에 걸림(06 §2)', not miss, str(miss))
    files = set(os.listdir(ontology.DOCS_DIR))
    bad = [f for r in o['rules'] + o['pending_rules'] for f in r['docs'] if f not in files]
    check('근거 문서 파일이 있다', not bad, str(bad))
    ids = [n['id'] for n in o['nodes']]
    check('노드 id 중복 없음', len(ids) == len(set(ids)))
    check('메타에 07 번호 어긋남 기록', any('R-03' in d and 'R-04' in d for d in o['meta']['deviations']))


# ---------------------------------------------------------------------------
# 4. graph
# ---------------------------------------------------------------------------
def test_graph():
    g = ontology.graph()
    ids = [n['id'] for n in g['nodes']]
    check('graph 노드 60개 이하', len(ids) <= 60, f'{len(ids)}개')
    check('graph 노드 id 중복 없음', len(ids) == len(set(ids)))
    check('graph 노드 모양 {id,label,type}', all(n.get('id') and n.get('label') and n.get('type') for n in g['nodes']))
    check('graph 규칙 15개 모두', all(r in ids for r, *_ in taxcalc.RULEBOOK))
    bad = [e for e in g['edges'] if e['from'] not in ids or e['to'] not in ids or not e.get('label')]
    check('graph 엣지 양끝이 노드', not bad, str(bad[:3]))
    check('graph 자기 고리 없음', not [e for e in g['edges'] if e['from'] == e['to']])
    pairs = {(e['from'], e['to']) for e in g['edges']}
    check('graph 재원→세목(② → 퇴직소득세)', ('fund.retire', 'tax.retire') in pairs)
    check('graph 문서→규칙(doc39 → R-05)', ('doc39', 'R-05') in pairs)
    check('graph 퇴직금 × 부득이 엣지 없음(문서 확인 필요)',
          not [e for e in g['edges'] if e['from'] == 'fund.retire' and '부득이' in e['label']])
    json.dumps(g, ensure_ascii=False)          # 서버가 그대로 낸다 — 직렬화 가능해야
    check('graph JSON 직렬화', True)


# ---------------------------------------------------------------------------
# 5. rule_docs
# ---------------------------------------------------------------------------
def test_rule_docs():
    d = ontology.rule_docs('R-05')
    check('R-05 첫 근거 = doc39(원문 대조)', d and d[0]['doc'] == 'doc39' and d[0]['quoted'], str(d[:1]))
    docs = [x['doc'] for x in ontology.rule_docs('R-07')]
    check('R-07 근거 = 원문(doc38·39) + 07(doc1·5·20)', docs[:2] == ['doc38', 'doc39'] and
          all(x in docs for x in ('doc1', 'doc5', 'doc20')), str(docs))
    check('보류 규칙 R-07a 근거 doc38', [x['doc'] for x in ontology.rule_docs('R-07a')] == ['doc38'])
    check('모르는 규칙 → []', ontology.rule_docs('R-99') == [])
    sc = [x for x in ontology.rule_docs('R-01') if x['doc'] == 'doc27']
    check('doc27(과기공) 근거에 범위 주석', sc and sc[0]['scope'], str(sc))


# ---------------------------------------------------------------------------
# 6. path_for
# ---------------------------------------------------------------------------
def _missing(steps):
    return [m['label'] for m in steps[1]['missing']]


def test_path_scene2():
    steps = ontology.path_for(taxcalc.parse(SCENE2), SCENE2)
    check('경로 단계 이름·순서(07 §7)', [s['step'] for s in steps] == list(ontology.STEPS), str([s['step'] for s in steps]))
    check('경로 단계 모양 {step,detail,nodes}', all(set(('step', 'detail', 'nodes')) <= set(s) for s in steps))
    d1 = steps[0]['detail']
    check('장면2 개체 인식(62 · IRP · 1,800만 원)', '62' in d1 and 'IRP' in d1 and '1,800만 원' in d1, d1)
    check('장면2 재원 ②·③ 인식', all(x in steps[0]['nodes'] for x in ('fund.retire', 'fund.credit', 'account.irp')),
          str(steps[0]['nodes']))
    miss = ' '.join(_missing(steps))
    for w in ('평가액', '연차', '퇴직소득세'):
        check(f'장면2 빠진 속성에 {w}', w in miss and w in steps[1]['detail'], miss)
    check('장면2 빠진 속성에 연금수령연차·실제수령연차 둘 다', '연금수령연차' in miss and '실제수령연차' in miss, miss)
    check('장면2 요건: 55세 ✓ · 5년 요건 면제', '✓' in steps[2]['detail'] and '면제' in steps[2]['detail'], steps[2]['detail'])
    check('장면2 인출 순서 ② → ③', '② 이연퇴직소득 → ③' in steps[3]['detail'], steps[3]['detail'])
    d5 = steps[4]['detail']
    check('장면2 세금: R-09 감면 · R-10 100% · R-07 · 금액 보류', all(x in d5 for x in ('R-09', 'R-10', 'R-07'))
          and 'calc' not in steps[4], d5)
    check('장면2 근거: R-05 → doc39', 'R-05 연금수령한도 → doc39' in steps[5]['detail'], steps[5]['detail'])
    check('장면2 응답 = 되묻기', steps[6]['detail'].startswith('되묻기'), steps[6]['detail'])
    # 문자열만 줘도, parse 결과를 JSON 으로 왕복해도(set → list) 같은 결과
    check('path_for(문자열)', _missing(ontology.path_for(SCENE2)) == _missing(steps))
    p = taxcalc.parse(SCENE2)
    p2 = json.loads(json.dumps({k: (sorted(v) if isinstance(v, set) else v) for k, v in p.items()}, ensure_ascii=False))
    check('path_for(JSON 왕복 parse)', _missing(ontology.path_for(p2)) == _missing(steps))
    text = json.dumps(steps, ensure_ascii=False)
    check('경로 문구에 판단 표현 없음', not BANNED.search(text), BANNED.findall(text))


def test_path_full():
    # 손계산: 한도 = 1억 ÷ (11 − 1) × 120% = 1,200만 원
    #   인출 1,800만 원은 R-06 순서로 전부 ② 퇴직금(6,000만)에서 — 한도 안 1,200만, 밖 600만
    #   한도 안 1,200만 × 4% × 70%(30% 감면) = 33만 6천 원, 밖 600만 × 4%(감면 없음) = 24만 원 → 57만 6천 원
    steps = ontology.path_for(SCENE2_FULL)
    check('값이 다 있으면 빠진 속성 없음', not steps[1]['missing'], str(_missing(steps)))
    c = steps[4].get('calc') or {}
    check('장면2 손계산 57만 6천 원', c.get('total') == '57만 6천 원', str(c.get('total')))
    check('장면2 한도 1,200만 원', c.get('limit') == '1,200만 원' and '1,200만 원' in steps[2]['detail'], steps[2]['detail'])
    check('장면2 한도 밖 600만 원', '600만 원' in steps[2]['detail'] and '600만 원' in steps[3]['detail'], steps[3]['detail'])
    check('장면2 응답에 나눠 받기 비교', '나눠' in steps[6]['detail'], steps[6]['detail'])
    # ③ 재원만: 1,200만 × 5.5% = 66만 + 600만 × 16.5% = 99만 → 165만 원
    s = ontology.path_for('만 62세, IRP 평가액 1억 원, 연금수령 1년차, 올해 1,800만 원 빼면 세금은?')
    check('③만 손계산 165만 원', (s[4].get('calc') or {}).get('total') == '165만 원', str(s[4].get('calc')))
    check('③만 가정 밝힘', any('③' in a for a in s[1]['assumed']), str(s[1]['assumed']))
    # 11년차 이상 + 퇴직금 + 실제수령연차 없음 → 감면율을 몰라 금액 보류
    s = ontology.path_for('70세, IRP 평가액 2억 원, 연금수령 12년차, 퇴직금 1억 2천만 원, 퇴직소득세 실효세율 5%, '
                          '올해 3,000만 원 빼면 세금은?')
    check('12년차 퇴직금: 실제수령연차 되묻기', _missing(s) == ['실제수령연차'] and 'calc' not in s[4], str(_missing(s)))
    check('12년차: 한도 없음', '한도가 없음' in s[2]['detail'], s[2]['detail'])


def test_path_other():
    # 납입 — 총급여 5,000만 원, 연금저축 600 + IRP 300 → 900만 × 16.5% = 148만 5천 원
    s = ontology.path_for('총급여 5,000만 원인데 연금저축 600만원, IRP 300만원 넣으면 환급 얼마?')
    check('공제 손계산 148만 5천 원', (s[4].get('calc') or {}).get('total') == '148만 5천 원', s[4]['detail'])
    check('공제 경로 R-01·R-02', 'R-01' in s[2]['nodes'] and 'R-02' in s[2]['nodes'] and s[3]['detail'].startswith('해당 없음'))
    # 연금저축 900 → 공제 대상 600 × 16.5% = 99만 원, 못 받은 300만 원은 나중에 비과세
    s = ontology.path_for('총급여 5,000만 원인데 연금저축에 900만원 넣으면 환급 얼마?')
    check('공제 손계산 99만 원 + 300만 원 비과세', (s[4].get('calc') or {}).get('total') == '99만 원'
          and '300만 원' in s[4]['detail'] and 'fund.free' in s[4]['nodes'], s[4]['detail'])
    # 55세 전 해지 — 재원을 모르면 ① 금액을 묻고, 전부 ③이라 볼 때 2,000만 × 16.5% = 330만 원(최대)
    s = ontology.path_for('50세인데 연금저축 2,000만 원 해지하면 세금은?')
    check('해지: 연금외수령 판정', '연금외수령' in s[2]['detail'] and 'wd.other' in s[2]['nodes'], s[2]['detail'])
    check('해지: ① 금액 되묻기', _missing(s) and '①' in _missing(s)[0], str(_missing(s)))
    check('해지: 최대 330만 원', (s[4].get('calc') or {}).get('total') == '330만 원' and '최대' in s[4]['detail'], s[4]['detail'])
    # 부득이 × 퇴직금 — 금액을 내지 않고 '문서 확인 필요'
    s = ontology.path_for('62세, IRP에 퇴직금이 있는데 요양 때문에 1,000만 원 빼면 세금은?')
    check('부득이 경로: R-11 · 요양 사건', 'R-11' in s[2]['nodes'] and 'event.care' in s[0]['nodes'], str(s[0]['nodes']))
    check('부득이 × 퇴직금: 문서 확인 필요', '문서 확인 필요' in s[4]['detail'] and 'calc' not in s[4], s[4]['detail'])
    # 연금 수령(연 단위) — R-07·R-08 로 간다
    s = ontology.path_for('65세인데 연금저축에서 연 1,200만 원 받으면 세금은?')
    check('수령 경로: R-07 · 연금소득세', 'R-07' in s[5]['nodes'] and 'tax.pension' in s[4]['nodes'], str(s[4]['nodes']))
    # ISA 전환 — 소득이 없으면 공제율을 묻는다, 추가 공제 대상 300만 원(3,000만 × 10%, 최대 300만)
    s = ontology.path_for('ISA 만기 3천만원을 연금저축으로 옮기면 세액공제 얼마나?')
    check('ISA: 소득 되묻기 · 추가 공제 300만 원', _missing(s) == ['총급여·종합소득금액'] and '300만 원' in s[2]['detail'],
          f'{_missing(s)} / {s[2]["detail"]}')
    # 계산할 게 없는 질문
    s = ontology.path_for('연금저축이 뭐예요?')
    check('일반 질문: 7단계 · 빠진 속성 없음', len(s) == 7 and not s[1]['missing'])
    s = ontology.path_for({})
    check('빈 parse 도 동작', len(s) == 7)


# ---------------------------------------------------------------------------
# 7. 문서 꼬리표(doc_meta) — 있으면 붙이고 없어도 동작
# ---------------------------------------------------------------------------
def test_doc_meta():
    o = ontology.load(meta={'doc39.txt': {'title': '연금수령한도 안내', 'audience': '고객', 'as_of': '미상'}})
    d = o['docs']['doc39.txt']
    check('꼬리표 있으면 독자·기준일 붙음', d['audience'] == '고객' and d['as_of'] == '미상'
          and d['label'] == '고객용 · 기준일 미상', str(d))
    o = ontology.load(meta={})
    d = o['docs']['doc39.txt']
    check('꼬리표 없어도 동작(제목은 문서에서)', d['audience'] == '' and d['title'], str(d))
    shapes = [{'docs': {'doc39.txt': {'audience': '고객'}}}, {'doc39': {'audience': '고객'}},
              [{'file': 'doc39.txt', 'audience': '고객'}], {'docs': [{'file': 'doc39', 'audience': '고객'}]}]
    check('꼬리표 JSON 모양 4가지', all(ontology._norm_meta(s).get('doc39.txt', {}).get('audience') == '고객' for s in shapes))
    check('꼬리표 JSON 이상한 모양 → {}', ontology._norm_meta('x') == {} and ontology._norm_meta({'stale': []}) == {})
    real = ontology.load(refresh=True)
    check('실제 load(꼬리표 ' + ('있음' if real['meta']['doc_meta'] else '없음') + ')', len(real['docs']) >= 58)


# ---------------------------------------------------------------------------
# 8. (검토) 답변 계산 블록과 같은 금액 — 경로 숫자와 답 숫자가 다르면 시연에서 바로 들킨다
# ---------------------------------------------------------------------------
# (질문, 손계산 금액, 손계산 설명)
CONSIST = [
    (SCENE2_FULL, '57만 6천 원', '한도 1,200만 · 퇴직금 1,200만 × 4% × 70% + 600만 × 4%'),
    ('50세인데 공제 안 받은 돈 500만 원 있고 연금저축 2,000만 원 해지하면 세금은?', '247만 5천 원',
     '① 500만 비과세 · 나머지 1,500만 × 16.5%'),
    ('IRP에 퇴직금 1억 원 들어 있어요. 62세, 연금수령 1년차, 퇴직소득세 실효세율 4%, 올해 1,800만 원 빼면 세금은?',
     '57만 6천 원', '평가액 = 퇴직금 1억 · 한도 1,200만 → 33만 6천 + 24만'),
    ('50세, IRP 평가액 5,000만 원(퇴직금 3,000만 원, 공제받은 돈 2,000만 원), 퇴직소득세 실효세율 5%, '
     '2,000만 원 해지하면 세금은?', '100만 원', '55세 전 → 퇴직금 2,000만 × 5% × 100%'),
    ('75세, IRP 평가액 1억 원(퇴직금 6,000만 원, 공제받은 돈 4,000만 원), 퇴직소득세 실효세율 4%, 연금수령 12년차, '
     '실제 수령 12년차, 올해 1,800만 원 빼면 세금은?', '43만 2천 원', '한도 없음 · 1,800만 × 4% × 60%(40% 감면)'),
    ('62세, IRP 평가액 1억 원(퇴직금 6,000만 원, 공제받은 돈 4,000만 원), 퇴직소득세 240만 원, 연금수령 1년차, '
     '올해 1,800만 원 빼면 세금은?', '57만 6천 원', '실효세율 = 240만 ÷ 6,000만 = 4%'),
]


def _calc_total(steps):
    return (steps[4].get('calc') or {}).get('total')


def test_consistency():
    for q, want, how in CONSIST:
        s = ontology.path_for(q)
        c = taxcalc.compute(q) or {'blocks': []}
        theirs = [b['key_figure'] for b in c['blocks'] if b['topic'] == '재원별 인출 세금']
        check(f'손계산 {want} ({how})', _calc_total(s) == want, f'{_calc_total(s)}')
        check(f'taxcalc.compute 와 같은 금액 {want}', theirs == [want], f'taxcalc {theirs}')
    # taxcalc 가 금액을 내지 않는(규칙만) 질문이면 경로도 금액을 내지 않고 빠진 재원 금액을 묻는다
    for q in ('62세, IRP 평가액 1억 원, 퇴직금 1,000만 원, 퇴직소득세 실효세율 4%, 연금수령 1년차, 올해 1,800만 원 빼면 세금은?',
              '62세, IRP 평가액 1억 원, 공제 안 받은 돈 500만 원, 연금수령 1년차, 올해 1,800만 원 빼면 세금은?'):
        s = ontology.path_for(q)
        c = taxcalc.compute(q) or {'blocks': []}
        check(f'금액 보류(taxcalc 도 규칙만): {q[:30]}…', 'calc' not in s[4] and not any(b['numbers'] for b in c['blocks'])
              and '잔액보다' not in s[3]['detail'], f'{s[4].get("calc")} / {s[3]["detail"]}')
        check(f'③ 금액 되묻기: {q[:30]}…', any('③' in m['label'] for m in s[1]['missing']), str(_missing(s)))
    # ISA 전환 — taxcalc ISA 블록(doc4 산식) 글에 경로 금액이 그대로 있어야 한다
    isa = [('총급여 5,000만 원, ISA 만기 3천만원을 연금저축으로 옮기면 세액공제 얼마?', ['148만 5천 원'],
            '일반 600(전환액이 채움) + 추가 300 = 900 × 16.5%'),
           ('총급여 6,000만 원, ISA 만기 3천만원을 IRP로 옮기면 세액공제 얼마?', ['158만 4천 원'],
            '일반 900 + 추가 300 = 1,200 × 13.2%'),
           ('총급여 5,000만 원, ISA 만기 100만원을 연금저축으로 옮기면 세액공제 얼마?', ['16만 5천 원'],
            '일반 100 + 추가 10 → 실제 넣은 100만 원이 상한 × 16.5%'),
           ('총급여 5,000만 원인데 ISA 만기 3천만원을 연금계좌로 옮기면 세액공제 얼마?', ['148만 5천 원', '198만 원'],
            '계좌를 몰라 둘 다: 연금저축 900 × 16.5% / IRP 1,200 × 16.5%')]
    for q, want, how in isa:
        s = ontology.path_for(q)
        by = [r.get('refund') for r in (s[4].get('calc') or {}).get('by_dest', [])]
        text = ' '.join(b['text'] for b in (taxcalc.compute(q) or {'blocks': []})['blocks'] if 'ISA' in b['topic'])
        check(f'ISA 손계산 {want} ({how})', by == want, str(by))
        check(f'ISA taxcalc 블록과 같음 {want}', all(x in text for x in want), text[:200])
    # 55세 전 해지·① 금액 모름 — 전부 ③ 이라 볼 때(최대) 2,000만 × 16.5% = 330만 원, taxcalc 블록과 같음
    for q in ('50세인데 연금저축 2,000만 원 해지하면 세금은?', '50세, 공제 안 받은 돈도 있는데 연금저축 2,000만 원 해지하면 세금은?'):
        s = ontology.path_for(q)
        theirs = [b['key_figure'] for b in (taxcalc.compute(q) or {'blocks': []})['blocks'] if b['topic'] == '55세 전·해지 인출 세금']
        check(f'해지 최대 330만 원 = taxcalc: {q[:22]}…', _calc_total(s) == '330만 원' and s[4]['calc'].get('upper_bound')
              and theirs == ['330만 원'] and any('①' in m for m in _missing(s)), f'{s[4].get("calc")} / {theirs}')
    s = ontology.path_for('50세, IRP에 퇴직금도 있는데 2,000만 원 해지하면 세금은?')
    check('해지 + 퇴직금 금액 모름 → 최대도 내지 않음', 'calc' not in s[4], str(s[4].get('calc')))
    s = ontology.path_for(isa[3][0])
    check('ISA 계좌 모름 → 합계 하나로 정하지 않음', 'total' not in s[4]['calc'], str(s[4]['calc']))
    s = ontology.path_for(isa[0][0])
    check('ISA 공제 못 받은 2,100만 원 → 나중에 비과세(①)', '2,100만 원' in s[4]['detail'] and 'fund.free' in s[4]['nodes'],
          s[4]['detail'])


# ---------------------------------------------------------------------------
# 9. (검토) 경계값·이상한 입력
# ---------------------------------------------------------------------------
NEG_MONEY = re.compile(r'-\s*\d[\d,]*\s*(?:억|만|천|원)')


def test_edges():
    # 50대 — 55세 전후가 섞여 요건을 판정할 수 없다(✗ 로 단정하지 않는다)
    s = ontology.path_for('50대인데 IRP 평가액 1억 원, 연금수령 1년차, 1,800만 원 빼면 세금은?')
    check('50대: R-04 ✗ 로 단정하지 않음', '✗' not in s[2]['detail'] and '확인 필요' in s[2]['detail'], s[2]['detail'])
    check('50대: 나이 되묻기 · 금액 보류', any(m['attr'] == 'attr.age' for m in s[1]['missing']) and 'calc' not in s[4],
          str(_missing(s)))
    # 60대 — 55~69세 구간에 온전히 든다: 1,200만 × 5.5% + 600만 × 16.5% = 165만 원
    s = ontology.path_for('60대인데 IRP 평가액 1억 원, 연금수령 1년차, 1,800만 원 빼면 세금은?')
    check('60대: 165만 원', _calc_total(s) == '165만 원', str(s[4].get('calc')))
    # 0년차는 없다(1년차부터) → 빠진 값
    s = ontology.path_for('62세, IRP 평가액 1억 원, 연금수령 0년차, 올해 1,800만 원 빼면 세금은?')
    check('0년차 → 연금수령연차 되묻기(한도 ÷ 11 안 함)', '연금수령연차' in _missing(s) and 'calc' not in s[4]
          and '11 − 0' not in s[2]['detail'], s[2]['detail'])
    # 12년차·평가액 없음 — 한도가 없어 평가액이 필요 없다: 1,800만 > 1,500만 → 전액 16.5% = 297만 원
    s = ontology.path_for('70세, 연금수령 12년차, IRP에서 올해 1,800만 원 빼면 세금은?')
    check('12년차 평가액 없음: 297만 원 · 평가액 안 물음', _calc_total(s) == '297만 원' and '평가액' not in _missing(s),
          f'{_calc_total(s)} / {_missing(s)}')
    # 인출 금액이 없는 인출 질문 — 얼마를 빼는지 묻는다
    s = ontology.path_for('IRP에 퇴직금 있는데 세금은?')
    check('인출 금액 없음 → 되묻기', '인출 금액' in _missing(s) and any('②' in m for m in _missing(s)), str(_missing(s)))
    # '공제 안 받은 돈도 있어요' — 금액 없이 언급만 하면 0원으로 치지 않고 묻는다
    s = ontology.path_for('공제 안 받은 돈도 있고 IRP 평가액 1억 원, 퇴직금 6,000만 원, 퇴직소득세 실효세율 4%, 62세, '
                          '연금수령 1년차, 1,800만 원 빼면 세금은?')
    check('① 언급만 → ① 금액 되묻기 · 금액 보류', any('①' in m for m in _missing(s)) and 'calc' not in s[4], str(_missing(s)))
    # parse 결과가 JSON 을 거친 연대 나이(['대', 60])
    p = {'age': ['대', 60], 'withdraw': 1800, 'balance': 10000, 'year_n': 1}
    check('JSON 연대 나이 ["대", 60] → 165만 원', _calc_total(ontology.path_for(p)) == '165만 원')
    # path_for(None, q) = path_for(q)
    check('path_for(None, q) 는 q 를 parse', _missing(ontology.path_for(None, SCENE2)) == _missing(ontology.path_for(SCENE2)))
    # 이상한 입력 — 예외 없이 7단계, 음수 금액 표기 없음
    weird = [None, 5, [], '', '   ', 'x' * 3000, {'withdraw': '1800'}, {'withdraw': float('nan')},
             {'withdraw': -5, 'age': True}, {'withdraw': float('inf'), 'balance': 1e30, 'year_n': 1, 'age': 62},
             {'year_n': 0, 'balance': 10000, 'withdraw': 1800, 'age': 62}, {'fund': 'x', 'payout': 'y', 'contrib': [None, 1]},
             {'income': ['총급여', 'abc'], 'contrib': [{'acct': 'savings', 'v': -3}]}, {'age': ['대', 'x']},
             {'fund': {'free': -1, 'credit': 'a'}, 'fund_mentioned': 'free', 'withdraw': 100, 'early': True},
             {'retire_eff': 500, 'rtax': -1, 'actual_n': 2.5}]
    errs, neg = [], []
    for x in weird:
        try:
            st = ontology.path_for(x)
            if len(st) != 7:
                errs.append((repr(x)[:40], len(st)))
            if NEG_MONEY.search(json.dumps(st, ensure_ascii=False)):
                neg.append(repr(x)[:40])
        except Exception as e:                       # noqa: BLE001 — 어떤 예외든 실패로 센다
            errs.append((repr(x)[:40], repr(e)))
    check('이상한 입력 16가지: 예외 없이 7단계', not errs, str(errs))
    check('이상한 입력: 음수 금액 표기 없음', not neg, str(neg))
    s = ontology.path_for({'withdraw': '1,800', 'balance': '10000', 'year_n': '1', 'age': 62})
    check('글자 숫자("1,800")도 금액으로 읽음 → 165만 원', _calc_total(s) == '165만 원', str(s[4].get('calc')))
    # tax_route·rule_docs 표기 흔들림
    check('tax_route 재원 이름 전체("① 과세제외금액")', ontology.tax_route('① 과세제외금액', '연금외수령')['tax'] == '비과세')
    check('rule_docs("r-5") = rule_docs("R-05")', ontology.rule_docs('r-5') == ontology.rule_docs('R-05') != [])
    check('rule_docs(" R-07A ") = R-07a', [d['doc'] for d in ontology.rule_docs(' R-07A ')] == ['doc38'])
    for bad in (None, '', 'R-', 'R-999'):
        check(f'rule_docs({bad!r}) → []', ontology.rule_docs(bad) == [])


# ---------------------------------------------------------------------------
# 10. (검토) 변이 시험 — 일부러 틀리게 바꾼 사본을 검사가 잡는지
# ---------------------------------------------------------------------------
def test_mutation():
    o = copy.deepcopy(ontology.load())
    check('변이 전: RULEBOOK 차이 없음', not rulebook_mismatch(o))
    m = copy.deepcopy(o)
    next(r for r in m['rules'] if r['id'] == 'R-05')['name'] = '연금 수령 한도'      # 띄어쓰기 하나
    check('변이: R-05 이름 띄어쓰기 → 잡힘', ('R-05', '연금수령한도') in rulebook_mismatch(m), str(rulebook_mismatch(m)))
    m = copy.deepcopy(o)
    m['rules'] = [r for r in m['rules'] if r['id'] != 'R-15']
    check('변이: R-15 빠짐 → 잡힘', ('R-15', '(구)개인연금저축') in rulebook_mismatch(m))
    if not os.path.exists(MD07):
        return
    cells = md07_cells(open(MD07, encoding='utf-8').read())
    check('변이 전: 07 표 차이 없음', not cell_mismatch(o, cells), str(cell_mismatch(o, cells)))
    m = copy.deepcopy(o)
    next(c for c in m['tax_matrix']['cells'] if c['source'] == 'credit' and c['mode'] == '연금외수령')['text'] = '기타소득세 15% (R-10)'
    check('변이: ③ × 연금외수령 칸 16.5→15% → 잡힘', ('credit', '연금외수령') in cell_mismatch(m, cells))
    m = copy.deepcopy(o)
    c = next(c for c in m['tax_matrix']['cells'] if c['source'] == 'retire' and c['mode'] == '부득이')
    c.update(tax='연금소득세', tax_node='tax.pension', rule_ids=['R-11'])
    check('변이: 부득이 × 퇴직금에 세목을 지어냄 → 잡힘', any(len(k) == 3 for k in cell_mismatch(m, cells)))


# ---------------------------------------------------------------------------
# 11. (검토) 근거 인용·문구·숫자 상수
# ---------------------------------------------------------------------------
SWEEP = [SCENE2, SCENE2_FULL] + [q for q, _, _ in CONSIST] + [
    '총급여 5,000만 원인데 연금저축 600만원, IRP 300만원 넣으면 환급 얼마?', '연금저축이 뭐예요?',
    '50세인데 연금저축 2,000만 원 해지하면 세금은?', '62세, IRP에 퇴직금이 있는데 요양 때문에 1,000만 원 빼면 세금은?',
    '65세인데 연금저축에서 연 1,200만 원 받으면 세금은?', '65세인데 연금저축에서 월 150만 원 받으면 세금은?',
    'ISA 만기 3천만원을 연금저축으로 옮기면 세액공제 얼마나?', '주택 구입하려고 IRP 중도인출하면 세금은?',
    '<script>alert(1)</script> IRP 1,800만 원 빼면?', '파산했는데 IRP 2,000만 원 빼면 세금은?']


def _doc_text(f):
    with open(os.path.join(ontology.DOCS_DIR, f), encoding='utf-8') as fh:
        return fh.read()


def test_text():
    o = ontology.load()
    quotes = [(e['doc'] + '.txt', e['quote']) for e in o['edges'] if e.get('quote')]
    quotes += [(q['file'], q['text']) for c in o['tax_matrix']['cells'] for q in c.get('evidence', [])]
    quotes += [(q['file'], q['text']) for x in o['excluded_rules'] for q in x.get('evidence', [])]
    check('근거 인용 10개 이상', len(quotes) >= 10, str(len(quotes)))
    for f, t in quotes:
        check(f'원문 그대로 {f}: {t[:24]}…', t in _doc_text(f))
    c = next(c for c in o['tax_matrix']['cells'] if c['source'] == 'retire' and c['mode'] == '부득이')
    check('부득이 × 퇴직금: 세목은 비우고 문서 근거 후보만', c['tax'] == '문서 확인 필요' and len(c.get('evidence', [])) >= 2)
    r = ontology.tax_route('retire', '부득이')
    check('tax_route 가 근거 후보를 함께 냄(세목은 그대로 비움)', r['evidence'] and r['tax'] == '문서 확인 필요' and not r['rule_ids'])
    pairs = {(e['from'], e['to']) for e in ontology.graph()['edges']}
    check('그래프: 요양 → 부득이한 사유 인출(doc20 표)', ('event.care', 'wd.unavoidable') in pairs)
    check('그래프: 요양·파산·재난 → 연금외수령 아님(doc20 표 세법상 부득이 O)',
          not {('event.care', 'wd.other'), ('event.rehab', 'wd.other'), ('event.disaster', 'wd.other')} & pairs)
    check('그래프: 주택 구입 → 연금외수령(doc20 표 16.5%)', ('event.house', 'wd.other') in pairs)
    check('메타에 doc20 대조 기록', sum('doc20' in d for d in o['meta']['deviations']) >= 2)

    tag = re.compile(r'<[A-Za-z/!][^>]*>')
    bad_tag, bad_word = [], []
    for q in SWEEP:
        text = json.dumps(ontology.path_for(q), ensure_ascii=False)
        if tag.search(text.replace(q, '')):          # 질문 원문은 경로 문구에 들어가지 않는다 — 우리 문구만 본다
            bad_tag.append(q[:20])
        if BANNED.search(text):
            bad_word.append((q[:20], BANNED.findall(text)))
    check(f'경로 문구 {len(SWEEP)}개 질문: HTML 태그 없음', not bad_tag, str(bad_tag))
    check(f'경로 문구 {len(SWEEP)}개 질문: 판단 표현 없음', not bad_word, str(bad_word))
    s = ontology.path_for('<script>alert(1)</script> IRP 1,800만 원 빼면?')
    check('질문 속 태그가 경로 문구로 새지 않음', '<script' not in json.dumps(s, ensure_ascii=False))
    g = json.dumps(ontology.graph(), ensure_ascii=False)
    raw = open(ontology.ONTO_PATH, encoding='utf-8').read()
    check('graph·ontology.json: HTML 태그 없음', not tag.search(g) and not tag.search(raw))
    check('graph·ontology.json: 판단 표현 없음', not BANNED.search(g) and not BANNED.search(raw), BANNED.findall(g + raw))


# 세법 숫자를 ontology.py 에 다시 적으면 taxcalc 와 두 곳을 고쳐야 한다(계약서 '식 복붙 금지')
RULE_NUMBERS = {5500, 4500, 600, 900, 300, 1500, 1800, 0.165, 16.5, 13.2, 5.5, 4.4, 3.3, 1.2, 55, 70, 80, 11, 0.7, 0.6,
                0.5, 0.1}


def test_no_rule_constants():
    with open(ontology.__file__, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    nums = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
    bad = sorted(x for x in nums if x in RULE_NUMBERS)
    check('ontology.py 에 세법 숫자 상수 없음(taxcalc 공용 함수로만)', not bad, str(bad))


def main():
    for t in (test_rulebook, test_tax_route, test_md07, test_json, test_graph, test_rule_docs,
              test_path_scene2, test_path_full, test_path_other, test_doc_meta,
              test_consistency, test_edges, test_mutation, test_text, test_no_rule_constants):
        try:
            t()
        except Exception as e:                  # 한 묶음이 죽어도 나머지는 돈다
            check(f'{t.__name__} 예외', False, repr(e))
    fail = [(n, w) for n, ok, w in results if not ok]
    print(f'{len(results) - len(fail)}/{len(results)} 통과')
    for n, w in fail:
        print(f'  [실패] {n} — {w}')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
