# -*- coding: utf-8 -*-
"""
오케스트레이터(작업 반장) — 고객 상태를 보고 다음 행동을 정한다. 직접 답을 쓰지 않는다.

  거절          가드가 막은 질문(공격·타인 정보)
  상담 연결     고객이 사람 상담을 원하거나, 답 속 숫자가 규칙 계산과 어긋날 때(verify)
  되묻기        계산에 꼭 필요한 값이 빠졌거나(평가액·재원·퇴직소득세…), 상품 비교 기준이 없을 때
  계산 + 되묻기 계산할 수 있는 만큼 계산하고 빠진 값은 묻는다
  계산 → 설명   규칙 계산(taxcalc)으로 숫자를 내고 AI 는 설명만
  검색 → 설명   제도·절차·상품 설명

결정은 규칙으로 한다(모델을 부르지 않는다) — 같은 상태면 늘 같은 행동이 나와야 시연·운영에서 설명할 수 있다.
answer.py 가 단계마다 이 모듈을 불러 생각 과정(think_trace)에 남기고, /chat 이 화면의 '오케스트레이터 단계'와
'다음 행동' 버튼으로 보여 준다.
"""
import re

import taxcalc

_HANDOFF = re.compile(r'상담(?:원|사)\s*(?:연결|바꿔|과\s*통화|랑\s*통화)|사람(?:이랑|과|하고)?\s*(?:상담|통화|얘기)|직원\s*연결|전화\s*상담')
_PRODUCT = re.compile(r'(?:좋은|괜찮은|추천|뭘\s*사|어떤)\s*(?:연금\s*)?(?:상품|펀드|ETF|TDF)|상품\s*추천|펀드\s*추천')


def customer_state(question, profile_text=''):
    """질문(+앞 대화 고객 정보)에서 읽은 고객 상태 — 화면·생각 과정 표시용 한 줄."""
    p = taxcalc.parse(question or '')
    parts = []
    if p['age'] is not None:
        parts.append(f"{p['age'][1]}대" if isinstance(p['age'], tuple) else f"나이 {p['age']}세")
    accts = [n for n, rx in (('연금저축', r'연금저축'), ('IRP', r'IRP|개인형'), ('DC', r'(?<![A-Za-z])DC'),
                             ('ISA', r'ISA')) if re.search(rx, question or '', re.I)]
    if accts:
        parts.append('·'.join(accts))
    if p['fund_mentioned']:
        parts.append('재원 ' + '·'.join(taxcalc.FUND_NAME[k].split(' ', 1)[1].split('(')[0]
                                      for k in ('free', 'retire', 'credit') if k in p['fund_mentioned']))
    if p['balance'] is not None:
        parts.append(f"평가액 {taxcalc.won(p['balance'])}")
    if p['withdraw'] is not None:
        parts.append(f"인출 {taxcalc.won(p['withdraw'])}")
    if p['income']:
        parts.append(f"{p['income'][0]} {taxcalc.won(p['income'][1])}")
    if p['contrib']:
        parts.append('납입 ' + ', '.join(('IRP ' if c['acct'] == 'irp' else '연금저축 ') + taxcalc.won(c['v'])
                                       for c in p['contrib']))
    s = ' · '.join(parts)
    if profile_text:
        s = (s + ' / ' if s else '') + f'앞 대화: {profile_text}'
    return s or '조건 없음'


def decide(question, blocked=False, clarify=False, clarify_what='', calc=None):
    """→ (행동, 이유). 질문을 받은 직후(생성 전)에 정한다."""
    if blocked:
        return '거절', '안전 관문에서 막힘'
    if _HANDOFF.search(question or ''):
        return '상담 연결', '고객이 사람 상담을 원함'
    if calc:
        nums = any(b.get('numbers') for b in calc['blocks'])
        needs = calc.get('needs') or []
        if needs and not nums:
            return '되묻기', '계산에 필요한 값이 없음: ' + ', '.join(n.split(' —')[0].split('(')[0] for n in needs[:4])
        if needs:
            return '계산 + 되묻기', '계산한 값 + 빠진 값: ' + ', '.join(n.split(' —')[0].split('(')[0] for n in needs[:3])
        if nums:
            return '계산 → 설명', '규칙 ' + '·'.join(calc.get('rids') or []) + ' 로 계산'
        return '규칙 → 설명', '조건이 갈려 규칙만 ' + '·'.join(calc.get('rids') or [])
    if clarify:
        return '되묻기', f'비교 기준이 없음({clarify_what})'
    return '검색 → 설명', '제도·상품 문서 검색'


def next_actions(action, question, calc=None, verify=None, category=''):
    """답 아래 '다음 행동' 버튼. kind: chat(그 문장으로 다시 묻기) · diy(DIY 설계 열기) · handoff(상담 연결)."""
    acts = []
    topics = [b['topic'] for b in (calc or {}).get('blocks', [])]
    text = '\n'.join(b['text'] for b in (calc or {}).get('blocks', []))
    if '비교(나눠 받기)' in text:
        q2 = taxcalc.restate_split((calc or {}).get('parsed') or {}) or \
            '올해는 연금수령한도까지만 받고 나머지는 내년에 받으면 세금이 어떻게 달라져요?'
        acts.append({'label': '올해 한도까지만 받으면?', 'kind': 'chat', 'payload': q2})
    if any(t.startswith('세액공제') or t.startswith('ISA') for t in topics):
        acts.append({'label': 'DIY 설계로 납입 조합 비교', 'kind': 'diy', 'payload': ''})
    for b in (calc or {}).get('blocks', []):
        if b['topic'] == '연금 수령 세금' and (b.get('yearly') or 0) > 1500:
            acts.append({'label': '연 1,500만 원 이하로 받으면?', 'kind': 'chat',
                         'payload': '연 1,500만 원 이하로 나눠 받으면 세금은 얼마예요?'})
    if is_product_ask(question):
        acts.append({'label': '투자성향 진단 받기', 'kind': 'handoff', 'payload': '적합성 진단'})
    if verify and verify.get('contradiction'):
        acts.insert(0, {'label': '상담사에게 금액 확인', 'kind': 'handoff', 'payload': '계산 확인'})
    acts.append({'label': '상담 연결', 'kind': 'handoff', 'payload': ''})
    seen, out = set(), []
    for a in acts:
        if a['label'] not in seen:
            seen.add(a['label'])
            out.append(a)
    return out[:4]


# 빠진 정보마다 '어디서 확인하나' — 문서에 있는 확인 경로만 쓴다(원천징수영수증·연말정산·계좌 평가액)
_WHERE = [
    (r'평가액', '올해 1월 1일(또는 연금 개시일) 기준 계좌 평가액'),
    (r'퇴직소득세', '퇴직할 때 받은 퇴직소득 원천징수영수증의 퇴직소득세(또는 실효세율)'),
    (r'퇴직금', '퇴직금을 IRP로 받을 때 들어온 금액(퇴직소득 원천징수영수증)'),
    (r'세액공제 안 받은|비과세 원금', '연말정산 때 세액공제를 받지 않은 납입액(금융회사에 과세제외금액으로 확인)'),
    (r'세액공제 받은 돈', '평가액에서 퇴직금과 공제 안 받은 돈을 뺀 나머지'),
    (r'연금수령연차', '연금을 받을 수 있게 된 해(만 55세 이후 요건을 갖춘 해)부터 센 햇수 — 2013년 3월 이전 가입 계좌는 6년차부터'),
    (r'나이', '만 나이'),
    (r'해지·인출하려는 금액', '이번에 빼려는 금액'),
]


def is_product_ask(q):
    return bool(_PRODUCT.search(q or '') or re.search(r'제일\s*좋은|가장\s*좋은|뭐가\s*좋|어떤\s*게\s*좋', q or ''))


def product_clarify_answer():
    """'제일 좋은 연금 상품?' — 상품을 지목하지 않고 고르는 기준을 되묻는다(발표 장면 3). 상품 선택은 적합성 진단으로."""
    return '\n'.join([
        '어느 상품이 제일 좋다고 정해 드릴 수는 없어요. 연금 상품은 투자성향(적합성) 진단 결과와 돈의 목적에 맞춰 고르게 되어 있어서, '
        '아래를 알려 주시면 고르는 기준을 나란히 비교해 드릴게요.',
        '',
        '1. 나이와 연금을 받기 시작하려는 시기 — 남은 기간에 따라 감당할 수 있는 변동이 달라져요',
        '2. 지금 가진 연금 계좌(연금저축·IRP·DC)와 이 돈의 목적 — 세액공제용 납입인지, 퇴직금 운용인지, 곧 받을 생활비인지',
        '3. 원금 손실을 어디까지 견딜 수 있는지 — 원리금 보장 상품과 실적배당 상품 중 어느 쪽을 볼지 정하는 기준이에요',
        '',
        '상품을 실제로 고르는 단계는 투자성향 진단을 거쳐 진행돼요. 아래 다음 행동에서 진단이나 상담으로 바로 이어 갈 수 있어요.'])


def clarify_answer(calc, cite=1):
    """되묻기 답 — 계산 블록의 규칙 줄과 빠진 정보 목록으로 만든다(모델을 부르지 않는다).
    시연에서 모델이 되묻기 답에 예시 숫자를 지어내 틀린 계산(2억 ÷ 10 × 120% = 1,818만 원)을 쓴 일이 있어,
    고객 채널(/chat)의 되묻기는 규칙 문장만으로 낸다. 숫자는 고객이 값을 알려 준 다음 턴에 규칙 계산이 낸다.
    질문에 먼저 답한다 — 지금 조건으로 알 수 있는 규칙을 앞에 다 싣고, 빠진 정보는 끝에 덧붙여 묻는다
    (예전 판은 '정보가 더 필요해요'로 시작하고 규칙을 5줄에서 잘라, 규칙만으로 답할 수 있는 질문에도 묻기만 했다)."""
    rules, seen = [], set()
    for b in (calc or {}).get('blocks', []):
        for ln in b['text'].split('[원문]')[0].split('\n')[1:]:
            ln = ln.strip()
            if not ln.startswith('- '):
                continue
            ln = ln[2:].strip()
            if ln.startswith(('계산에 필요한데', '가정(', '입력:')) or ln in seen:
                continue
            seen.add(ln)
            rules.append(re.sub(r'\s*\(R-\d+(?:·R-\d+)*\)', '', ln))
    needs = list(dict.fromkeys((calc or {}).get('needs', [])))
    out = ['지금 말씀하신 조건으로 문서 규칙에 따라 답하면 이렇습니다' + (f' [문서{cite}].' if cite else '.'), '']
    out += [f'- {r}' for r in rules[:8]]
    if needs:
        out += ['', '**세액까지 정확히 계산해 드리려면 아래 정보가 더 필요해요**']
        for k, n in enumerate(needs, 1):
            name = re.sub(r'\(.*?\)', '', n.split(' —')[0]).strip()
            where = next((w for rx, w in _WHERE if re.search(rx, n)), '')
            out.append(f'{k}. {name}' + (f' — {where}' if where else ''))
        out += ['', '알려 주시면 위 규칙으로 바로 계산해 드릴게요. 모르는 값은 상담사가 계좌 내역에서 확인해 드릴 수 있어요'
                '(아래 다음 행동의 상담 연결).']
    return '\n'.join(out)


def plan_steps(trace_lines, action, reason, state, calc=None, verify=None):
    """화면의 '오케스트레이터 단계' — 생각 과정에서 단계별 결과를 골라 쓴다."""
    def field(head):
        for ln in trace_lines:
            if ln.startswith(head):
                return ln[len(head):].strip()
        return ''
    steps = [{'step': '안전 관문', 'detail': field('가드:') or '통과'},
             {'step': '고객 상태', 'detail': state}]
    mem = field('대화 기억:')
    if mem:
        steps.append({'step': '대화 기억', 'detail': mem})
    rw = next((ln for ln in trace_lines if ln.startswith('질문 재작성')), '')
    if rw:
        steps.append({'step': '질문 재작성', 'detail': rw.split(':', 1)[1].strip()})
    onto = field('온톨로지 경로:')
    if onto:
        steps.append({'step': '온톨로지 경로', 'detail': onto})
    steps.append({'step': '다음 행동', 'detail': f'{action} — {reason}'})
    if calc:
        steps.append({'step': '규칙 계산', 'detail': calc.get('summary', '')[:160]})
    top = next((ln.split('상위:', 1)[1].strip() for ln in trace_lines if '상위:' in ln), '')
    if top:
        steps.append({'step': '근거 검색', 'detail': top[:120]})
    flt = field('대상 필터:')
    if flt:
        steps.append({'step': '독자·기관 필터', 'detail': flt})
    pf = field('세금 계산 질문:')
    if pf:
        steps.append({'step': '근거 정리', 'detail': pf})
    if verify is not None:
        d = f"{verify['matched']}/{verify['checked']} 확인 · {verify['action']}"
        steps.append({'step': '숫자 대조', 'detail': d})
    return steps
