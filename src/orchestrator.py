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


# MAPS 화면 경로 — 받은 문서 58건 밖 정보라 답 본문에는 쓰지 않고 '다음 행동'의 안내 버튼(kind 'guide')으로만 보여 준다.
# 경로를 알려 줄 뿐 연동은 아니다. 10-13 MAPS 앱에서 직접 본 메뉴(본선_작업정리/11 §10) — 앱 메뉴가 바뀌면 여기만 고친다.
MAPS_AS_OF = '10-13'
MAPS_PATH = {
    'simulation': ('MAPS 연금 수령 시뮬레이션', '메뉴 › 연금 › 연금관리 › 연금지급관리 › 연금 수령 시뮬레이션',
                   '내 계좌로 앞으로 받을 세후 연금은 여기서 계산해요'),
    'credit': ('MAPS 세액공제 관리', '메뉴 › 연금 › 연금관리 › 연금입금관리 › 세액공제 관리',
               '올해 납입금액·연간 납입한도·과세제외금액을 여기서 확인해요'),
    'isa': ('MAPS ISA 전환입금', '메뉴 › 연금 › 연금관리 › 연금입금관리 › ISA 전환입금',
            '만기 ISA를 연금계좌로 옮기는 신청 화면이에요'),
    'cancel': ('MAPS 연금 개시/해지', '메뉴 › 연금 › 연금관리 › 연금지급관리 › 연금 개시/해지',
               '해지 신청 화면이에요. 신청하기 전에 세액공제 안 받은 납입금부터 확인하세요'),
    'transfer': ('MAPS 타사 연금가져오기', '메뉴 › 연금 › 타사 연금가져오기/실물이전',
                 '다른 금융회사 연금을 옮겨 오는 신청 화면이에요'),
}
_TRANSFER = re.compile(r'(?:타사|다른\s*(?:증권사|은행|보험사|금융회사|회사)|보험사).{0,20}(?:이전|옮기|옮겨|가져오)|실물\s*이전|계약\s*이전')


def maps_guide(question, calc=None):
    """질문·계산 블록에 맞는 MAPS 화면 경로 안내 하나(없으면 None). payload 는 화면에 그대로 보일 안내문."""
    topics = [b['topic'] for b in (calc or {}).get('blocks', [])]
    key = None
    if taxcalc._CANCEL.search(question or '') and any('해지' in t or '재원별' in t for t in topics):
        key = 'cancel'
    elif any(t.startswith('ISA') for t in topics):
        key = 'isa'
    elif any(t.startswith('세액공제') for t in topics):
        key = 'credit'
    elif any(t.startswith(('연금 수령', '재원별 인출', '연금수령한도', '퇴직금 재원')) for t in topics):
        key = 'simulation'
    elif _TRANSFER.search(question or ''):
        key = 'transfer'
    if not key:
        return None
    label, path, what = MAPS_PATH[key]
    return {'label': label, 'kind': 'guide',
            'payload': f'{path} — {what}. ({MAPS_AS_OF} MAPS 앱에서 확인한 경로 안내예요. 이 화면과 연동되어 있지는 않아요)'}


def next_actions(action, question, calc=None, verify=None, category=''):
    """답 아래 '다음 행동' 버튼. kind: chat(그 문장으로 다시 묻기) · diy(DIY 설계 열기) · guide(MAPS 화면 경로 안내) ·
    handoff(상담 연결). 4개까지, '상담 연결'은 늘 마지막에 남긴다."""
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
    guide = maps_guide(question, calc)
    if guide:
        acts.append(guide)
    acts.append({'label': '상담 연결', 'kind': 'handoff', 'payload': ''})
    seen, out = set(), []
    for a in acts:
        if a['label'] not in seen:
            seen.add(a['label'])
            out.append(a)
    return out if len(out) <= 4 else out[:3] + out[-1:]


# 빠진 정보마다 '어디서 확인하나' — 문서에 있는 확인 경로만 쓴다(원천징수영수증·연말정산·계좌 평가액)
_WHERE = [
    (r'평가액', '올해 1월 1일(또는 연금 개시일) 기준 계좌 평가액'),
    (r'퇴직소득세', '퇴직할 때 받은 퇴직소득 원천징수영수증의 퇴직소득세(또는 실효세율)'),
    (r'재원별 금액', '세액공제 안 받은 돈은 연말정산 때 공제받지 않은 납입액, 퇴직금은 퇴직소득 원천징수영수증, 나머지는 세액공제 받은 돈·수익'),
    (r'퇴직금', '퇴직금을 IRP로 받을 때 들어온 금액(퇴직소득 원천징수영수증)'),
    (r'세액공제 안 받은|비과세 원금', '연말정산 때 세액공제를 받지 않은 납입액(금융회사에 과세제외금액으로 확인)'),
    (r'세액공제 받은 돈', '평가액에서 퇴직금과 공제 안 받은 돈을 뺀 나머지'),
    (r'연금수령연차', '연금을 받을 수 있게 된 해(만 55세 이후 요건을 갖춘 해)부터 센 햇수 — 2013년 3월 이전 가입 계좌는 6년차부터'),
    (r'나이', '만 나이'),
    (r'해지·인출하려는 금액', '이번에 빼려는 금액'),
]


# 사람 상담 창구 — 번호·운영시간은 doc54 원문('☎연금자산관리센터: 1588-5577', '평일 08시~17시/주말 및 공휴일 제외')
CENTER = '미래에셋증권 연금자산관리센터(☎ 1588-5577, 평일 08시~17시, 주말·공휴일 제외)'


def handoff_reply(prof='', ui=True):
    """고객이 사람 상담을 원할 때의 안내(모델 호출 없음). answer.py 와 /chat 규칙 답(server.py)이 같이 쓴다.
    상담사에게 대화를 넘기는 연동은 아직 없다 — '함께 넘겨 드린다'고 약속하지 않는다(본선_작업정리/12 부록 F).
    ui: 시연 화면(/chat)이면 '상담 요약' 카드(demo.html — 상담 버튼을 누르면 이 대화의 계산값·근거로 만든다)를 안내한다.
    대회 규격(/answer)에는 버튼이 없어 그 문장을 뺀다."""
    cond = f'({prof})' if prof and prof != '조건 없음' else ''
    what = f'지금까지 말씀하신 조건{cond}과 계산 결과·근거' if cond else '지금까지의 계산 결과·근거'
    return ('상담사 연결을 요청하셨어요. 지금은 상담 연결을 표시만 하고, 대화 내용이 상담사에게 자동으로 전달되지는 않아요.\n\n'
            f'연금 상담은 {CENTER}에서 받으실 수 있어요. '
            + (f'아래 상담 버튼을 누르면 {what}를 한 장으로 정리한 상담 요약이 나와요. '
               '상담하실 때 보여 주시면 처음부터 다시 설명하지 않으셔도 돼요.' if ui else
               f'상담하실 때 {what}를 함께 알려 주시면 처음부터 다시 설명하지 않으셔도 돼요.'))


# 앞으로 받을 연금액을 묻는 말 — '65세에 연금 얼마 받니?', '월 300 받고 싶은데 가능할까', '평균으로 가정해서 계산해줘'.
# 받은 문서에는 미래 수익률·적립액을 정하는 규칙이 없다. 10-13 외부 시험에서 모델이 수익률 5%를 가정해 금액을 지어내고
# (식도 틀림), '65세라면 4.4%'처럼 세율도 틀렸다 → 시연 채널은 정해진 문장으로 답하고 계산할 수 있는 질문으로 잇는다
_PROJECTION = re.compile(
    r'(?:연금|수령액|연금액)?[^.?!\n]{0,20}얼마(?:나|쯤|정도)?\s*(?:를|씩)?\s*(?:받|나오|타|수령)'
    r'|(?:수령액|연금액|받을\s*(?:돈|금액))[^.?!\n]{0,10}(?:얼마|계산|예상)'
    r'|\d+\s*(?:만\s*원?)?\s*(?:정도|쯤|씩)?\s*받고\s*싶'
    r'|받(?:으면|을\s*때|는다면)[^.?!\n]{0,10}(?:어느\s*정도|얼마)'
    r'|(?:평균|수익률)[^.?!\n]{0,12}가정[^.?!\n]{0,10}(?:계산|알려)')
_TAX_WORDS = re.compile(r'세금|세액|공제|세율|과세|소득세|환급|돌려받|줄어|늘어|차이|아끼|절세|한도|이자|지연|담보|대출|빌리|보험금')


def is_projection_ask(q):
    """앞으로 받을 연금액(적립·수익률 가정이 필요한 금액)을 묻는가. 세금을 묻는 말이면 아니다(규칙 계산이 맡는다)."""
    q = q or ''
    return bool(_PROJECTION.search(q)) and not _TAX_WORDS.search(q)


def projection_answer():
    """앞으로 받을 연금액 질문 — 수익률을 지어내지 않고, 문서 규칙으로 계산할 수 있는 것과 확인할 곳을 안내(모델 호출 없음).
    세율·한도는 taxcalc RULES 원문(R-01·R-02·R-07)과 같은 값."""
    return '\n'.join([
        '앞으로 받을 연금액은 얼마를 몇 년 넣는지, 운용수익률, 몇 년에 걸쳐 받는지에 따라 달라져요. '
        '받은 문서에는 미래 수익률을 정하는 기준이 없어서, 수익률을 가정해 금액을 만들어 드리지는 않을게요.',
        '',
        '**대신 문서 규칙으로 지금 바로 계산해 드릴 수 있는 것**',
        '- 넣을 때: 연금저축·IRP 납입액의 세액공제(연금저축 600만 원, IRP 합산 900만 원까지 · '
        '총급여 5,500만 원 이하 16.5%, 초과 13.2%)',
        '- 받을 때: 연금소득세(55세 이상 70세 미만 5.5%, 70세 이상 80세 미만 4.4%, 80세 이상 3.3%), '
        '연금수령한도, 한 해 1,500만 원 기준',
        '- 55세 전에 꺼낼 때: 돈의 출처(재원)별 세금',
        '',
        '연금계좌가 있으면 계좌 기준 예상 수령액은 아래 다음 행동의 MAPS 화면 경로에서 확인할 수 있고, '
        f'상담은 {CENTER}에서 받을 수 있어요.'])


def projection_actions(question):
    """'얼마 받나' 질문 뒤에 이어 갈 계산 질문 — 질문에 나이·소득이 있으면 그 값으로."""
    p = taxcalc.parse(question or '')
    age = p['age'] if isinstance(p['age'], int) and 55 <= p['age'] <= 100 else 65
    inc = p['income'][1] if p['income'] else None
    ded_q = (f"{p['income'][0]} {taxcalc.won(inc)}이고 연금저축 600 + IRP 300 넣으면 세액공제는 얼마예요?" if inc else
             '총급여 5,000만 원이고 연금저축 600 + IRP 300 넣으면 세액공제는 얼마예요?')
    return [{'label': '넣을 때 세액공제 계산', 'kind': 'chat', 'payload': ded_q},
            {'label': f'{age}세에 받을 때 세금', 'kind': 'chat', 'payload': f'{age}세에 연금저축에서 연 1,200만 원 받으면 세금은 얼마예요?'},
            {'label': MAPS_PATH['simulation'][0], 'kind': 'guide',
             'payload': f"{MAPS_PATH['simulation'][1]} — {MAPS_PATH['simulation'][2]}. ({MAPS_AS_OF} MAPS 앱에서 확인한 경로 안내예요. 이 화면과 연동되어 있지는 않아요)"},
            {'label': '상담 연결', 'kind': 'handoff', 'payload': ''}]


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


def amount_clarify_answer(calc):
    """단위가 없어 읽는 법에 따라 결과가 크게 갈리는 금액('월 50,000' — 5만 원인가 5억 원인가)을 먼저 묻는다(모델 호출 없음).
    사용자 결정(10-10): 시연 채널(/chat)에서는 되묻고, 대회 규격(/answer)은 가정을 밝히고 답한다.
    지금 읽은 대로 계산한 값도 함께 보여 준다 — 맞으면 고객은 '네'만 하면 된다."""
    amb = (calc or {}).get('ambiguous') or []
    key = next((b.get('key_figure') for b in (calc or {}).get('blocks', []) if b.get('numbers') and b.get('key_figure')), None)
    out = ['계산하기 전에 금액을 하나만 확인할게요.', '']
    out += [f'- {a["ask"]}' for a in amb]
    if key:
        out += ['', f'{amb[0]["text"].replace("으로 봄", "")}으로 보면 결과는 **{key}**이에요. 다른 금액이면 알려 주세요 — 문서 규칙으로 다시 계산해 드릴게요.']
    else:
        out += ['', '알려 주시면 문서 규칙으로 바로 계산해 드릴게요.']
    return '\n'.join(out)


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
            ln = re.sub(r'\s*\(R-\d+(?:·R-\d+)*\)', '', ln)          # '(R-04)' 통째로
            ln = re.sub(r'R-\d+(?:·R-\d+)*\s*', '', ln)                 # '(R-06 순서…)' 안의 번호만
            rules.append(re.sub(r'\(\s*\)', '', ln).strip())
    needs = list(dict.fromkeys((calc or {}).get('needs', [])))
    out = ['지금 말씀하신 조건으로 문서 규칙에 따라 답하면 이렇습니다' + (f' [문서{cite}].' if cite else '.'), '']
    # '해지 신청 전 확인'(과세재원 확정)은 되돌릴 수 없는 절차라 눈에 띄게
    out += ['- **해지 신청 전 확인**:' + r.split(':', 1)[1] if r.startswith('해지 신청 전 확인:') else f'- {r}'
            for r in rules[:8]]
    if needs:
        out += ['', '**세액까지 정확히 계산해 드리려면 아래 정보가 더 필요해요**']
        for k, n in enumerate(needs, 1):
            name = re.sub(r'\(.*?\)', '', n).split(' —')[0].strip()   # 괄호 안의 '—'에서 자르면 이름이 깨진다
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
