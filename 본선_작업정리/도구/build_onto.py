# 기록용(10-15): 판독 기준표 칸을 처음 data/ontology.json 에 넣을 때 쓴 빌더. 이제 원본은 data/ontology.json 이다 —
# 칸을 고칠 때는 ontology.json 을 직접 고치고 src/test_onto_check.py 로 확인한다. 아래 경로(OC·pre_data)는 당시 작업 폴더라 그대로는 돌지 않는다.
# -*- coding: utf-8 -*-
"""data/ontology.json 에 '판독 기준표'를 더한다 — 고치기 전 사본(oc/pre_data/ontology.json) 글자에 끼워 넣어 손 서식을 그대로 둔다.
더하는 것: 속성의 read(모델이 질문에서 읽는 칸·허용 값·근거 말·계산기 쪽 값), 새 속성, 기존 조건의 cmp(기계 시험),
규칙의 read_conditions(판독 전용 조건 — ontology.path 의 '빠진 속성' 목록에 끼지 않게 conditions 와 따로 둔다), reading.examples.
다시 돌려도 같은 결과(사본에서 새로 만든다)."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OC = os.path.dirname(HERE)
PRE = os.path.join(OC, 'pre_data', 'ontology.json')
DST = r'C:\Users\장선우\Desktop\진행해야하는것\미래에셋\data\ontology.json'
LAST = os.path.join(OC, '_onto_built.json')

# ---------------------------------------------------------------------------
# 근거 말(정규식) — 고객 말투에서 그 값을 가리키는 글자
# ---------------------------------------------------------------------------
DED_ASK = r'세액\s*공제|연말\s*정산|환급|돌려\s*받|공제\s*(?:는|를|가|도)?\s*(?:얼마|받|돼|되|가능|늘|최대)'
CMP = r'차이|비교|보다|(?:^|[^A-Za-z])vs(?![A-Za-z])|이득|손해|나아|나을|낫|유[리]|불리|어느\s*쪽|남는\s*게|본전|결국'
PAYOUT = (r'연금\s*(?:으로|을|를)?\s*(?:받|수령(?!\s*한도|\s*연차))|매달|매월|매년|해마다|(?:^|[^마])씩|나눠\s*받|월\s*\d')
WD = r'빼|뺄|뺐|인출|해지|해약|깨|찾|꺼내|일시금|한꺼번에|한\s*번에'
LIMIT = r'한도\s*(?:안|넘|초과|이내|내에서|까지|를\s*넘|가\s*얼마|는\s*얼마)'
ACCT_MOVE = (r'(?:IRP|개인형|연금\s*저축|연금\s*계좌)\S*?\s*(?:를|을|에서|에\s*있는\s*(?:돈|거|걸))\s*[^.?!]{0,15}?'
             r'(?:IRP|개인형|연금\s*저축|연금\s*계좌|다른\s*(?:증권사|은행|금융\s*회사|금융사|회사|곳|데))')
PENS = r'IRP|개인형|연금\s*저축|연금\s*계좌'
# 계산 대상이 여럿임을 말하는 말만 — '같이·함께'(남편이랑 같이 상담, 엄마랑 같이 사는데)는 사람 수를 말하지 않는다(검증 10-15)
EACH = r'각자|각각|따로따로|(?:두|세)\s*(?:사람|분)\s*(?:다|모두|각)|둘\s*다|(?:사람|분)\s*마다|한\s*(?:사람|분)\s*씩|1\s*인당'
SUM = r'합치|합쳐|합하|합해|합산|더하면|더해서|모아서|둘이\s*합'
AMT = r'\d|얼마'
OTHER =(r'남편|아내|와이프|배우자|부부|어머니|아버지|엄마|아빠|부모|(?:^|[^A-Za-z가-힣])형(?![가-힣]*\s*(?:퇴직|연금))|누나|언니|오빠|동생'
         r'|아들|(?:^|[^가-힣])딸|자녀|애들|친구')
MONTH_ASK = r'(?:한\s*달|매달|매월|(?:^|[^가-힣\d])월)\s*(?:에|마다|에\s*얼마씩)?\s*(?:세금|떼|얼마|내|나가)'
YEAR_WORDS = r'1\s*년|일\s*년|연간|한\s*해|해마다|매년|(?:^|[^가-힣])연\s'
MONTHS = r'(?:^|\D)\d{1,2}\s*월|\d{1,2}\s*개월|[한두세네]\s*달|\d{1,2}\s*달|남은|상반기|하반기|반년'
MONTHLY_AMT = r'매달|매월|월\s*\d|월마다|달마다|한\s*달에?\s*\d|다달이'
FUTURE = (r'내년|후년|\d{1,2}\s*년\s*(?:뒤|후|있다가)|나중에|(?:세|살)\s*(?:가|이)?\s*(?:되면|넘으면|되어서|돼서|지나서)|생일\s*지나'
          r'|은퇴\s*(?:후|하면)')
PAST = r'작년|지난\s*해|재작년|\d{1,2}\s*년\s*전에?\s*(?:뺐|받았|인출|해지|깼|찾)'
NOW_ACT = r'(?:올해|지금|당장|이번에|이번\s*달)\s*[^.?!]{0,15}?(?:빼|뺄|인출|해지|깨|찾|꺼내|받)'
FREE_YES = r'공제\s*(?:를|는|도|까지)?\s*(?:안|못)\s*받|공제\s*받지\s*(?:않|못)|공제\s*(?:안|못)\s*된|비과세\s*(?:원금|돈|금액)'
FREE_NONE = (r'공제\s*(?:를\s*)?(?:안|못)\s*받은\s*(?:돈|건|거|금액|납입금)?\s*(?:은|는|이|가|도)?\s*(?:하나도\s*|전혀\s*|따로\s*)?없')
FREE_ALL = r'(?:모두|다|전부|전액)\s*(?:세액\s*)?공제\s*(?:를\s*)?받'
NOT_RECV = r'안\s*받|못\s*받|받지\s*않|받지\s*못'
RETIRE = r'퇴직\s*금|퇴직\s*급여|이연\s*퇴직|위로금|명퇴금'
RET_ACCT = r'IRP|개인형|연금\s*저축|연금\s*계좌|계좌|옮|이전|이체|넣|들어'
RET_APART = r'따로|별도|말고|빼고|없|아니|안\s*(?:넣|옮|받)|일반\s*계좌|통장'
RET_MOVED = (r'(?:^|[^A-Za-z])D[BC](?![A-Za-z])[^.?!]{0,25}?(?:IRP|개인형|연금\s*저축)[^.?!]{0,6}?(?:로|으로)\s*'
             r'(?:옮|이전|이체|받|들어)')
RET_NONE = r'퇴직\s*금\s*(?:은|는|이)?\s*(?:없|아니|안\s*(?:들|받|넣))|퇴직\s*금\s*(?:없이|말고|빼고)'
NOT_STARTED = (r'개시\s*(?:신청\s*)?(?:를|을|은|는|도)?\s*(?:아직\s*)?(?:안|않|못|전|하기\s*전)'
               r'|아직\s*(?:연금\s*)?(?:개시|안\s*받|받지\s*않|못\s*받)|한\s*번도\s*(?:안|못)\s*받')
STARTED = r'개시\s*(?:했|함|한\s*지|하고|된|해서)|연금\s*(?:을|를)?\s*받(?:고\s*있|는\s*중)|받기\s*시작'
JOIN = r'가입|든\s*지|만든\s*지|넣은\s*지|부은\s*지|들어\s*놓|개설|들었'
ISA_DAYS = r'만기[^.?!]{0,15}?\d+\s*(?:일|개월|달|년)\s*(?:이|가|이나|정도|쯤|넘게|이상)?\s*(?:지났|넘었|지나|넘|경과|됐|되었)'
# 낼 세금이 적다는 상태 꼴만 — '세금 적게 내려면·덜 내려고·적게 내는 게 목표'(바람)는 적음이 아니다(검증 10-15).
# '적게·작게'는 상태 꼴에 넣지 않고, 같은 문장에 바람 말이 있으면 TAX_WISH 로 뺀다
SMALL_ST = r'(?:적|작)(?:어|었|은|고|다|아|았|네)(?!\s*지)'
TAX_SMALL = (r'세금\s*(?:을|이|은|는|도)?\s*(?:거의\s*|별로\s*|원래\s*|많이\s*|얼마\s*)?'
             r'(?:없(?!애|앨)|안\s*(?:내|냈|나와|나오|떼)|' + SMALL_ST + r'|조금\s*(?:밖에\s*)?(?:안\s*)?(?:내|냈|나와|나오)|안\s*(?:돼|되))'
             r'|결정\s*세액\s*(?:이|은|는|도)?\s*(?:거의\s*|겨우\s*|고작\s*)?(?:없|0\s*원|' + SMALL_ST
             + r'|얼마\s*(?:안|되지)|\d[\d,.]*\s*(?:만\s*)?(?:원\s*)?(?:밖에|뿐|정도밖에))'
             r'|소득세\s*(?:를|가|는|도)?\s*(?:거의\s*)?(?:없|안\s*(?:내|냈))')
TAX_WISH = r'려면|려고|고\s*싶|게\s*목표|(?:적게|작게|덜)\s*(?:내|낼|토해|납부)|줄이|줄여|줄어|줄일|아끼|절세|아니'
AGE_NUM = r'(?:^|[^\d,.]){v}(?![\d,.])(?!\s*(?:만|천|억|원|%|년|개월|달|일|월|대|번|회|명|개))'

for name, rx in list(globals().items()):
    if name.isupper() and isinstance(rx, str) and name not in ('PRE', 'DST', 'LAST', 'HERE', 'OC'):
        re.compile(rx.replace('{v}', '70'))           # 깨진 정규식이면 여기서 멈춘다


def ev(cue=None, scope='q', unless=None, **kw):
    d = {}
    if cue is not None:
        d['cue'] = cue
    d['scope'] = scope
    if unless:
        d['unless'] = unless
    d.update(kw)
    return d


def Q(doc, quote):
    return {'doc': doc, 'quote': quote}


# 자주 쓰는 원문(글자 그대로 — test_ontology_audit test_quotes 가 대조한다)
Q_DED = Q('doc41', '연금계좌 세액공제는 연말정산 세금이 정해지면 그 금액에서 세액공제액만큼 직접 차감해서 세금이 줄이는 방식이다.')
Q_LIMIT_DED = Q('doc41', '연금저축은 연600만원, IRP는 연금저축 납입액을 포함해서 연900만원이다.')
Q_TAXDUE = Q('doc41', '차감할 세금이 있으면 최대 금액까지 세액공제되지만 애초에 납부할 세금이 작으면 세액공제액도 그 금액만큼 줄어든다.')
Q_PAYRATE = Q('doc38', '반대로 연 1,500만원 이내로 인출하는 경우 타 소득과 합산한 필요 없이 연령에 따라 5.5%에서 3.3% 세율로 분리과세 되어 세부담을 줄일 수 있다.')
Q_YEAR1500 = Q('doc38', '사적연금소득을 1년에 1,500만원 초과 수령 시 (전 금융기관의 연금계좌 합산) 종합과세 되지만')
Q_OTHER165 = Q('doc20', '사유에 해당하지 않는다면 연금외수령으로 16.5% 기타소득세가 과세된다.')
Q_UNAVOID = Q('doc20', '부득이한 사유에 해당하면 중도 인출 시에도 전액 5.5% ~ 3.3% 저율의 연금소득세를 적용 받는다.')
Q_LIMITCORE = Q('doc39', '연금수령연차는 한도 계산의 핵심이다.')
Q_ACCT2 = Q('doc41', '연금계좌는 연금저축과 IRP, 두 종류다.')
Q_TAXLAW = Q('doc20', '소득세법은 IRP·연금저축 모두에 적용되며 ‘꺼낼 때 세율’을 결정한다.')
Q_RET_ACCT = Q('doc51', '연금 수령 혜택을 받으려면 퇴직금을 연금계좌(IRP·연금저축)으로 받아야한다.')
Q_DC_DED = Q('doc27', '퇴직연금의 세액공제는 금융기관에 가입한 개인연금 등과 합산하여 1인당 900만원까지 연말 세액공제가 가능합니다.')
Q_PER_PERSON = Q('doc27', '연금계좌란 개인별로 통합 관리되는 사적연금의 납입·인출 계좌로')
Q_ISA = Q('doc4', '1) ISA만기일로부터 60일이 경과 한 경우 전환 불가 (60일은 캘린더 기준, 초일불산입방식 적용)')
Q_REQ = Q('doc1', '연금수령 요건 | 적립기간 5년 이상 & 만 55세 이후(이연퇴직금 보유시 만 55세 이후) 개시 신청 후 연간 연금수령한도 내 수령')
Q_BEFORE_START = Q('doc5', '연금개시 신청 전 - 연금외수령 | 이연퇴직소득세 (부득이한 사유 : 연금소득세) | 기타소득세 16.5% (부득이한 사유 : 연금소득세)')
Q_FREE = Q('doc38', '세액공제 안받은 돈은 인출할 때에도 비과세 처리되며')
Q_RETIRE = Q('doc38', '연금계좌로 입금된 퇴직금을 연금수령하면 연금소득세로 분리과세 된다.')
Q_AGE_AT = Q('doc38', '연금 수령 시기 (연금수령일 현재) | 연금소득세율 | 종신연금 수령 시')
Q_OPEN = Q('doc39', '2013년 3월 1일 이후 가입한 연금계좌는 1년차부터 시작하지만, 2013년 3월 1일 이전 가입한 연금계좌는 6년차로 기산한다.')
Q_TRANSFER = Q('doc35', '타사로 실제 계약이전을 신청하기 전 실물이전 가능한 상품을 미리 확인할 수 있는 서비스입니다')
Q_RET_TWO = Q('doc51', '퇴직금을 수령하는 방식은 크게 두 가지다.')
Q_DB = Q('doc49', '제도별로는 DC(확정기여형)제도, IRP제도의 경우 중도인출이 가능하며 DB(확정급여형)제도의 경우는 중도인출이 허용되지 않습니다.')
Q_OLD = Q('doc1', '가. 개인연금저축: 2000.12.31 이전에 개설된 계좌')
Q_PUBLIC = Q('doc21', '(전 금융기관 합산 / 공적연금, 퇴직소득재원 및 부득이한 사유 제외)')

# ---------------------------------------------------------------------------
# 속성의 read — key(모델 JSON 칸), type, hint(칸 뜻), values(허용 값·근거 말·고객 말), evidence(정수 칸의 근거),
#   engine(계산기 쪽 값: parsed 매핑 → 블록이 밝힌 것 → 가정 기본값 순), cmp(조건에 cmp 가 없을 때 쓰는 비교)
# ---------------------------------------------------------------------------
PLAN_EV = lambda rx: [ev(rx, 'q', PENS), ev(rx, 'ask', PENS)]   # noqa: E731 — 연금계좌 말이 같이 나오면 그 제도는 배경일 수 있다

NEW_ATTRS = [
    {'id': 'attr.ask_kind', 'label': '묻는 일', 'of': 'Stage', 'parsed': [], 'added': True,
     'ask': '알고 싶으신 것이 넣는 돈의 세액공제인지, 연금으로 나눠 받는 돈의 세금인지, 한 번에 빼는 돈의 세금인지, 연금수령한도인지(둘 다면 둘 다)',
     'quotes': [Q_DED, Q_PAYRATE, Q_OTHER165, Q_LIMITCORE],
     'read': {
         'key': 'asks', 'type': 'list',
         'hint': '고객이 마지막에 답을 원하는 일. 두 가지를 묻거나 견주면 둘 다, 주로 묻는 것 먼저(예: 그동안 받은 세액공제와 해지할 때 세금의 차이를 물으면 ["인출", "납입"])',
         'values': [
             {'v': '납입', 'node': 'stage.pay', 'hint': '연금저축·IRP·DC에 넣는 돈의 세액공제·연말정산 환급(ISA 만기금을 옮겨 받는 공제도)',
              'say': '넣는 돈의 세액공제', 'rules': ['R-01', 'R-02', 'R-03'],
              'evidence': [ev(DED_ASK, 'ask')], 'also': [ev([CMP, r'공제|환급|돌려\s*받|연말\s*정산'], 'ask')]},
             {'v': '연금수령', 'node': 'stage.payout', 'hint': '연금으로 매달·매년 나눠 받을 때 세금',
              'say': '연금으로 나눠 받는 돈의 세금', 'rules': ['R-07', 'R-08', 'R-09'],
              'evidence': [ev(PAYOUT)], 'also': [ev([CMP, r'세금|떼|세율|과세'], 'ask')]},
             {'v': '인출', 'node': 'stage.early', 'hint': '한 번에 빼기·해지·깨기·중도인출의 세금',
              'say': '한 번에 빼는(해지·인출) 돈의 세금', 'rules': ['R-04', 'R-05', 'R-06', 'R-10', 'R-11'],
              'evidence': [ev(WD)], 'also': [ev([CMP, r'세금|떼|세율|과세'], 'ask')]},
             {'v': '한도', 'node': 'stage.payout', 'hint': '연금수령한도(1년에 연금으로 받을 수 있는 한도) 안인지·얼마인지',
              'say': '연금수령한도', 'rules': ['R-05'],
              'evidence': [ev(LIMIT, 'ask', r'공제|납입|넣|부어|붓')], 'also': [ev(LIMIT, 'ask', r'공제|납입|넣|부어|붓')]},
             {'v': '이전', 'node': 'stage.transfer', 'hint': '연금계좌끼리 옮기기(계약이전)',
              'say': '연금계좌끼리 옮기기', 'rules': [],
              'evidence': [ev(ACCT_MOVE, 'ask', r'ISA|퇴직')]},
             {'v': '기타', 'hint': '가입·상품·운용 등', 'say': '그 밖의 일'},
         ],
         'engine': [{'rules': True}],
         'cmp': {'covers': True}}},
    {'id': 'attr.account_kind', 'label': '계좌·제도', 'of': 'Account', 'parsed': ['plan'], 'added': True,
     'ask': '그 돈이 들어 있는(넣을) 계좌나 제도(연금저축 · IRP · DC · DB 등)',
     'quotes': [Q_ACCT2, Q_TAXLAW, Q_DB, Q_OLD, Q_PUBLIC],
     'read': {
         'key': 'account', 'type': 'enum',
         'hint': '계산할 돈이 들어 있는(들어갈) 계좌·제도. 퇴직금을 이미 IRP로 옮겼으면 IRP. 구개인연금=2000년 이전에 가입한 개인연금',
         'values': [
             {'v': '연금저축', 'node': 'account.savings', 'say': '연금저축'},
             {'v': 'IRP', 'node': 'account.irp', 'say': '개인형 IRP'},
             {'v': '연금계좌', 'node': 'account.pension', 'say': '연금계좌(IRP·연금저축)'},
             {'v': 'DC', 'node': 'account.dc', 'say': 'DC(확정기여형) 퇴직연금', 'evidence': PLAN_EV(r'(?:^|[^A-Za-z])DC(?![A-Za-z])|확정\s*기여')},
             {'v': 'DB', 'node': 'plan.db', 'say': 'DB(확정급여형) 퇴직연금', 'evidence': PLAN_EV(r'(?:^|[^A-Za-z])DB(?![A-Za-z])|확정\s*급여')},
             {'v': '기업형IRP', 'node': 'plan.irp_corp', 'say': '기업형 IRP', 'evidence': PLAN_EV(r'기업형')},
             {'v': '구개인연금', 'node': 'plan.old', 'say': '(구)개인연금저축(2000년 이전 가입)',
              'evidence': PLAN_EV(r'개인\s*연금(?!\s*저축\s*계좌)|\(구\)|(?:19\d\d|2000)\s*년')},
             {'v': '공적연금', 'say': '국민연금 같은 공적연금',
              'evidence': PLAN_EV(r'국민\s*연금|공무원\s*연금|사학\s*연금|군인\s*연금|기초\s*연금')},
             {'v': 'ISA', 'node': 'plan.isa', 'say': 'ISA', 'evidence': PLAN_EV(r'ISA')},
         ],
         'engine': [{'parsed': 'plan', 'map': {'db': 'DB', 'dc': 'DC', 'irp_corp': '기업형IRP'}},
                    {'default': '연금계좌', 'say': '연금저축·IRP(연금계좌) 규칙'}]}},
    {'id': 'attr.contrib_acct', 'label': '납입 계좌', 'of': 'Account', 'parsed': ['contrib'], 'added': True,
     'ask': '어느 계좌(연금저축 · IRP)에 얼마를 넣는지',
     'quotes': [Q_LIMIT_DED, Q_DC_DED],
     'read': {
         'key': 'pay_accounts', 'type': 'list',
         'hint': '올해 돈을 넣는(넣은) 계좌. 연금저축과 IRP 둘 다면 둘 다. DC 가입자부담금은 IRP 쪽',
         'values': [
             {'v': '연금저축', 'node': 'account.savings', 'say': '연금저축 납입', 'evidence': [ev(r'연금\s*저축')]},
             {'v': 'IRP', 'node': 'account.irp', 'say': 'IRP(퇴직연금) 납입',
              'evidence': [ev(r'IRP|개인형|(?:^|[^A-Za-z])DC(?![A-Za-z])|확정\s*기여|퇴직\s*연금')]},
         ],
         'engine': [{'parsed': 'contrib', 'field': 'acct', 'map': {'savings': '연금저축', 'irp': 'IRP'}}],
         'cmp': {'same': True}}},
    {'id': 'attr.person', 'label': '계산할 사람', 'of': 'Actor', 'parsed': [], 'added': True,
     'ask': '누구의 몫을 계산할지 — 두 분이면 한 분씩 나이 · 소득 · 금액을 나눠 주세요',
     'quotes': [Q_DC_DED, Q_PER_PERSON],
     'read': {
         'key': 'person', 'type': 'enum',
         'hint': '세금·공제를 계산해 달라는 사람. 본인 / 다른한사람(가족 한 사람 것만) / 여럿각자(두 사람 이상 각자) / 여럿합산(두 사람 이상 돈을 합쳐). '
                 '가족과 같이 산다·함께 상담한다는 말만으로는 여럿이 아니다',
         'values': [
             {'v': '본인', 'say': '가입자 한 사람 몫'},
             {'v': '다른한사람', 'say': '가족 한 사람 몫'},
             # 여럿을 말하는 말과 가족 말이 묻는 문장에, 또는 금액이 있는 한 문장에 함께 있어야
             {'v': '여럿각자', 'say': '두 사람 이상 각자', 'evidence': [ev([EACH, OTHER], 'ask'), ev([EACH, OTHER, AMT], 'sentence')]},
             {'v': '여럿합산', 'say': '두 사람 이상 합쳐서', 'evidence': [ev([SUM, OTHER], 'ask'), ev([SUM, OTHER, AMT], 'sentence')]},
         ],
         'engine': [{'block': r'두\s*사람\s*각자', 'value': '여럿각자', 'say': '두 사람 각자 — 한 사람 몫이라고 밝힘'},
                    {'default': '본인', 'say': '가입자 한 사람 몫'}]}},
    {'id': 'attr.ask_period', 'label': '묻는 금액의 기간', 'of': 'Withdrawal', 'parsed': [], 'added': True,
     'ask': '한 달 치로 나눠 드리면 될지(1년 치 ÷ 12달)',   # 고객이 이미 '한 달에'라고 했으니 기간을 다시 묻지 않는다
     'quotes': [Q_YEAR1500, Q_LIMIT_DED],
     'read': {
         'key': 'ask_per', 'type': 'enum',
         'hint': '고객이 알고 싶은 금액이 한 달 치면 월(예: 한 달에 세금 얼마), 1년 치면 연, 말이 없으면 null',
         'values': [
             {'v': '월', 'say': '한 달 치 금액', 'evidence': [ev(MONTH_ASK, 'ask', YEAR_WORDS)]},
             {'v': '연', 'say': '1년 치 금액'},
         ],
         'engine': [{'default': '연', 'say': '1년 치 금액'}]}},
    {'id': 'attr.months', 'label': '올해 달 수', 'of': 'Account', 'parsed': ['contrib', 'payout'], 'added': True,
     'ask': '올해 몇 달 치를 넣거나 받는지',
     'quotes': [Q_LIMIT_DED, Q_YEAR1500],
     'read': {
         'key': 'months', 'type': 'int',
         'hint': '올해 넣거나 받는 달 수가 12달이 아니라고 질문에 적혀 있을 때만 그 달 수(예: 10월부터 매달 넣으면 3). 아니면 null',
         'say': '올해 {v}달',
         'evidence': [ev([MONTHS, MONTHLY_AMT], 'sentence', **{'if': {'min': 1, 'max': 11}})],
         'engine': [{'parsed': 'contrib', 'where': {'monthly': True}, 'value': 12, 'say': '월 금액 × 12달(1년 치)'},
                    {'parsed': 'payout', 'where': {'period': 'monthly'}, 'value': 12, 'say': '월 금액 × 12달(1년 치)'}]}},
    {'id': 'attr.has_free', 'label': '공제 안 받은 돈 포함 여부', 'of': 'FundSource', 'parsed': ['fund'], 'added': True,
     'ask': '계좌 안 돈 가운데 세액공제를 받지 않은 납입금이 있는지와 그 금액',
     'quotes': [Q_FREE],
     'read': {
         'key': 'free_money', 'type': 'enum',
         'hint': '그 계좌에 세액공제를 안 받은 돈이 있다고 했으면 있음, 다 공제받았다고 했으면 없음, 말이 없으면 null',
         'values': [
             {'v': '있음', 'say': '세액공제 안 받은 돈(①)이 있음', 'facts': ['free'], 'evidence': [ev(FREE_YES, 'q', FREE_NONE)]},
             {'v': '없음', 'say': '넣은 돈을 모두 세액공제 받음', 'evidence': [ev(FREE_ALL, 'q', FREE_YES)]},
         ],
         'engine': [{'parsed': 'fund', 'has': 'free', 'value': '있음'},
                    {'default': '없음', 'say': '넣은 돈을 모두 세액공제 받은 돈(③)으로 봄'}],
         'cmp': {'same': True}}},
    {'id': 'attr.tax_due', 'label': '낼 세금(결정세액)', 'of': 'Actor', 'parsed': [], 'added': True,
     'ask': '올해 낼 세금(근로소득 원천징수영수증의 결정세액)이 얼마인지',
     'quotes': [Q_TAXDUE],
     'read': {
         'key': 'tax_small', 'type': 'enum',
         'hint': '낼 세금(결정세액)이 적거나 없다고 했으면 적음. 질문에 적힌 올해 총급여·종합소득금액이 아주 적어도(예: 총급여 1,500만 원) 적음. '
                 '세금을 줄이고 싶다·적게 내고 싶다는 바람은 적음이 아니다. 아니면 null',
         'note': '총급여·종합소득 2천만 원 미만 문턱은 문서 값이 아니라 되묻기를 거는 어림이다 — 그 정도 소득이면 결정세액이 공제액(최대 148만 5천 원)보다 적을 수 있다(doc41 원문은 "납부할 세금이 작으면 그만큼만")',
         'values': [
             {'v': '적음', 'say': '낼 세금(결정세액)이 공제액보다 적을 수 있음',
              'evidence': [ev(TAX_SMALL, 'sentence', TAX_WISH), {'engine_attr': 'attr.income', 'max': 1999}]},
             {'v': '충분', 'say': '낼 세금이 공제액보다 많음'},
         ],
         'engine': [{'block': r'질문의\s*결정세액', 'value': '적음', 'say': '질문의 결정세액만큼만 공제된다고 밝힘'},
                    {'default': '충분', 'say': '공제액을 다 돌려받음'}]}},
]

# 기존 속성에 더하는 read
OLD_READ = {
    'attr.age': {
        'key': 'age', 'type': 'int', 'say': '{v}세',
        'hint': '계산할 사람(돈 주인)의 지금 만 나이. 질문에 없으면 null',
        'evidence': [ev(None, 'q', num=AGE_NUM, **{'if': {'min': 19, 'max': 100}})],
        'engine': [{'parsed': 'age', 'int': True}],
        'engine_unknown': 'diff', 'unknown_say': '나이를 몰라 나이 구간별',   # 되묻기 '저는 …로 계산했는데'에 들어가는 고객 말
        'quotes': [Q_AGE_AT]},
    'attr.withdraw_year': {
        'key': 'years_from_now', 'type': 'int', 'say': '{v}년 뒤', 'say_neg': '{n}년 전', 'say_zero': '올해',
        'hint': '돈을 넣거나 빼거나 받는 일이 일어나는 해. 올해 0, 내년 1, 3년 뒤 3, 작년 -1. 환급받는 해가 아니다. 질문에 없으면 null',
        'evidence': [ev(FUTURE, 'q', NOW_ACT, **{'if': {'min': 1, 'max': 30}}),
                     ev(PAST, 'q', NOW_ACT, **{'if': {'min': -5, 'max': -1}})],
        'engine': [{'default': 0, 'say': '올해'}],
        # 되묻기 말은 read 안에 — 속성 ask 를 더하면 ontology.path 의 '빠진 속성' 되묻기 글(동결)이 바뀐다
        'ask': '언제 빼거나 받으실 계획인지(올해 · 내년 · 몇 년 뒤)',
        'quotes': [Q_AGE_AT]},
    'attr.income': {
        'engine': [{'parsed': 'income', 'index': 1}],
        'quotes': [Q(('doc41'), '직장인은 총급여 5,500만원, 종합소득이 있으면 종합소득금액 4,500만원이 기준이 되는데 그 금액 이하면 16.5%, 초과하면 13.2%가 적용된다.')]},
    'attr.period': {
        'key': 'join_years', 'type': 'int', 'say': '가입 {v}년',
        'hint': '계좌에 가입한 지 몇 년인지. 질문에 없으면 null',
        'evidence': [ev(JOIN, 'q', num=r'(?:^|[^\d,.]){v}\s*년', **{'if': {'min': 0, 'max': 60}})],
        'engine': [{'default': 5, 'say': '가입 5년 이상으로 봄'}],
        'quotes': [Q_REQ]},
    'attr.open_date': {
        'key': 'open_year', 'type': 'int', 'say': '{v}년 가입',
        'hint': '계좌에 가입한 해(예: 2012). 질문에 없으면 null',
        'evidence': [ev(None, 'q', r'연금\s*수령\s*연차', num=r'(?:^|\D){v}\s*년', **{'if': {'min': 1990, 'max': 2030}})],
        'engine': [{'default': 2013, 'say': '2013년 3월 1일 이후 가입 계좌(연금수령연차 1년차부터)로 봄'}],
        'ask': '그 계좌에 처음 가입하신 해(2013년 3월 1일 전인지 뒤인지)',
        'quotes': [Q_OPEN]},
    'attr.started': {
        'key': 'started', 'type': 'enum',
        'hint': '연금 개시(연금 받기 시작)를 했다고 했으면 개시함, 아직 안 했다고 했으면 개시안함, 말이 없으면 null',
        'values': [
            {'v': '개시함', 'say': '연금 개시 신청 후', 'evidence': [ev(STARTED)]},
            {'v': '개시안함', 'say': '아직 연금 개시 전', 'evidence': [ev(NOT_STARTED)]},
        ],
        'engine': [{'default': '개시함', 'say': '연금 개시 신청 후로 봄'}],
        'quotes': [Q_REQ, Q_BEFORE_START]},
    'attr.isa_days': {
        'key': 'isa_days', 'type': 'int', 'say': '만기 후 {v}일',
        'hint': 'ISA 만기일부터 지난 날 수(3개월이면 90). 질문에 없으면 null',
        'evidence': [ev(ISA_DAYS)],
        'derive': {'re': r'만기[^.?!]{0,15}?(\d+)\s*(일|개월|달|년)', 'units': {'일': 1, '개월': 30, '달': 30, '년': 365}},
        'engine': [{'default': 0, 'say': '만기일부터 60일 안에 옮긴다고 봄'}],
        'quotes': [Q_ISA]},
    'attr.has_retire': {
        'key': 'retire_money', 'type': 'enum',
        'hint': '그 계좌에 퇴직금(퇴직급여, DC·DB에서 옮긴 돈)이 있다고 했으면 있음, 퇴직금이 아니라거나 없다고 했으면 없음, 말이 없으면 null',
        'values': [
            {'v': '있음', 'say': '계좌에 퇴직금(②)이 있음', 'facts': ['retire'],
             'evidence': [ev([RETIRE, RET_ACCT], 'sentence', RET_APART), ev(RET_MOVED)]},
            {'v': '없음', 'say': '퇴직금이 아닌 돈', 'evidence': [ev(RET_NONE)]},
        ],
        'engine': [{'parsed': 'fund', 'has': 'retire', 'value': '있음'},
                   {'parsed': 'fund_mentioned', 'has': 'retire', 'value': '있음'},
                   {'parsed': 'plan_moved', 'value': '있음'},
                   {'topic': '퇴직금', 'value': '있음'},
                   {'default': '없음', 'say': '퇴직금(②)은 없다고 봄'}],
        'cmp': {'same': True},
        'quotes': [Q_RETIRE]},
    'attr.reason': {
        'key': 'reason', 'type': 'enum',
        'hint': '돈을 빼는 사유. 요양=본인·가족의 입원·수술·병원 치료. 생활비·사업자금처럼 다른 이유면 기타. 말이 없으면 null',
        'values': [
            {'v': '요양', 'as': '부득이', 'say': '요양(세법상 부득이한 사유일 수 있음)', 'evidence': [ev(r'요양|입원|수술|병원|치료|투병|간병')]},
            {'v': '재난', 'as': '부득이', 'say': '재난(세법상 부득이한 사유일 수 있음)', 'evidence': [ev(r'재난|태풍|홍수|지진|화재|산불|천재지변')]},
            {'v': '파산회생', 'as': '부득이', 'say': '파산·개인회생(세법상 부득이한 사유일 수 있음)', 'evidence': [ev(r'파산|회생')]},
            {'v': '해외이주', 'as': '부득이', 'say': '해외이주(세법상 부득이한 사유일 수 있음)', 'evidence': [ev(r'이민|해외\s*이주|이주')]},
            {'v': '사망', 'as': '부득이', 'say': '사망(세법상 부득이한 사유일 수 있음)', 'evidence': [ev(r'사망|돌아가|별세')]},
            {'v': '주택구입', 'as': '일반', 'say': '주택 구입'},
            {'v': '전월세', 'as': '일반', 'say': '전월세 보증금'},
            {'v': '대출상환', 'as': '일반', 'say': '대출 상환'},
            {'v': '기타', 'as': '일반', 'say': '그 밖의 사유'},
        ],
        'engine': [{'parsed': 'unavoidable', 'value': '부득이', 'say': '세법상 부득이한 사유 인출'},
                   {'topic': '부득이', 'value': '부득이', 'say': '세법상 부득이한 사유 인출'},
                   {'default': '일반', 'say': '일반 인출(연금외수령)'}],
        'quotes': [Q_UNAVOID, Q_OTHER165]},
}

# 기존 조건에 더하는 cmp — (규칙, 속성, 조건 글자) → cmp
OLD_CMP = [
    ('R-03', 'attr.isa_days', {'max': 60}),
    ('R-04', 'attr.age', {'min': 55, 'plus': 'attr.withdraw_year'}),
    ('R-04', 'attr.period', {'min': 5}),
    ('R-04', 'attr.started', {'eq': '개시함'}),
    ('R-05', 'attr.open_date', {'min': 2013}),
    ('R-07', 'attr.age', {'bands': [55, 70, 80], 'plus': 'attr.withdraw_year'}),
    ('R-11', 'attr.reason', {'eq': '부득이'}),
]


def C(attr, cmp, test, quotes, waived_by=None):
    d = {'attr': attr, 'test': test, 'cmp': cmp}
    if waived_by:
        d['waived_by'] = waived_by
    d['quotes'] = quotes
    return d


ASK = lambda q: C('attr.ask_kind', {'covers': True}, '질문이 묻는 일을 이 규칙이 계산함', [q])  # noqa: E731
PENS_ONLY = ['연금저축', 'IRP', '연금계좌']
ACCT = lambda q: C('attr.account_kind', {'in': PENS_ONLY}, '연금계좌(IRP·연금저축) 돈 — 퇴직급여를 연금계좌로 옮겼으면 같다',  # noqa: E731
                   [q] + ([Q_RET_ACCT] if q != Q_RET_ACCT else []), 'retire')   # R-09 는 같은 인용을 두 번 붙이지 않게
PERSON = lambda q: C('attr.person', {'in': ['본인', '다른한사람']}, '한 사람 몫을 계산', [q])  # noqa: E731
STARTED_C = C('attr.started', {'eq': '개시함'}, '연금 개시 신청 후(한도 안 연금수령)', [Q_REQ, Q_BEFORE_START])
PERIOD_C = C('attr.period', {'min': 5}, '가입 5년 이상(연금수령 요건)', [Q_REQ], 'retire')
FREE_C = C('attr.has_free', {'same': True}, '세액공제 안 받은 돈(①)이 있는지 같게 읽음', [Q_FREE])
RETIRE_C = C('attr.has_retire', {'same': True}, '퇴직금(②)이 있는지 같게 읽음', [Q_RETIRE])

READ_CONDS = {
    # R-01 계좌 조건은 퇴직금으로 풀지 않는다 — 퇴직금은 세액공제 받은 납입금과 다른 재원(doc38 ①②③), doc51 은 연금수령 얘기
    'R-01': [ASK(Q_DED),
             C('attr.account_kind', {'in': PENS_ONLY + ['DC', 'ISA']}, '연금저축·IRP·DC 가입자부담금(합산 900만 원)·ISA 만기 전환 납입',
               [Q_DC_DED, Q_ISA]),
             C('attr.contrib_acct', {'same': True}, '납입 계좌를 같게 읽음(연금저축 600만 원 · IRP 포함 900만 원)', [Q_LIMIT_DED]),
             PERSON(Q_DC_DED),
             C('attr.ask_period', {'eq': '연'}, '1년 치 세액공제', [Q_LIMIT_DED]),
             C('attr.months', {'min': 12}, '월 납입 × 12달(올해 내내)', [Q_LIMIT_DED]),
             C('attr.tax_due', {'eq': '충분'}, '낼 세금이 공제액보다 많아 다 돌려받음', [Q_TAXDUE])],
    'R-03': [ASK(Q_ISA),
             C('attr.account_kind', {'in': PENS_ONLY + ['ISA']}, 'ISA 만기금을 연금계좌로 옮김', [Q_ISA])],
    'R-04': [ASK(Q_REQ)],
    'R-05': [ASK(Q_LIMITCORE), ACCT(Q_TAXLAW), PERSON(Q_PER_PERSON), STARTED_C, PERIOD_C],
    'R-06': [ASK(Q_FREE), ACCT(Q_TAXLAW), FREE_C, RETIRE_C],
    'R-07': [ASK(Q_PAYRATE), ACCT(Q_TAXLAW), PERSON(Q_PER_PERSON),
             C('attr.ask_period', {'eq': '연'}, '1년 치 연금소득세(연 1,500만 원 판단)', [Q_YEAR1500]),
             C('attr.months', {'min': 12}, '월 수령액 × 12달(올해 내내)', [Q_YEAR1500]),
             PERIOD_C, FREE_C, RETIRE_C],
    'R-08': [ASK(Q_YEAR1500)],
    'R-09': [ASK(Q_RETIRE), ACCT(Q_RET_ACCT), PERSON(Q_PER_PERSON),
             C('attr.ask_period', {'eq': '연'}, '1년 치 세금', [Q_YEAR1500])],
    'R-10': [ASK(Q_OTHER165), ACCT(Q_TAXLAW), PERSON(Q_PER_PERSON),
             C('attr.reason', {'eq': '일반'}, '부득이한 사유가 아닌 일반 인출(16.5%)', [Q_OTHER165, Q_UNAVOID]),
             FREE_C, RETIRE_C],
    'R-11': [ASK(Q_UNAVOID)],
}

READING = {
    'note': '판독 기준표(src/onto_check.py) — 계산 블록 규칙(rids)의 conditions·read_conditions 가 가리키는 속성 가운데 read 가 있는 것만 '
            '모델이 질문에서 읽는다. read_conditions 는 conditions 와 같은 꼴(attr·test·waived_by)이지만 ontology.path 의 '
            "'빠진 속성 확인'에 끼지 않게 따로 둔다. cmp: eq·in(열거값) · min·max·bands(수 범위, plus 는 더해서 시험) · same(같게 읽음) · covers(묻는 일을 다 계산). "
            "되묻기 말은 read.ask → 속성 ask → label 순서(read.ask 는 ontology.path 되묻기 글을 바꾸지 않으려고 read 안에 둔 것).",
    'examples': [
        {'q': '56세예요. 2011년에 든 연금저축인데 개시는 아직 안 했고 공제 안 받은 돈도 좀 있어요. 내년에 2천 빼면 세금 얼마예요?',
         'read': {'attr.ask_kind': {'q': '내년에 2천 빼면 세금 얼마예요?', 'v': ['인출']},
                  'attr.account_kind': {'q': '연금저축', 'v': '연금저축'},
                  'attr.person': {'q': '56세예요', 'v': '본인'},
                  'attr.age': {'q': '56세예요', 'v': 56},
                  'attr.withdraw_year': {'q': '내년에 2천 빼면', 'v': 1},
                  'attr.open_date': {'q': '2011년에 든 연금저축', 'v': 2011},
                  'attr.started': {'q': '개시는 아직 안 했고', 'v': '개시안함'},
                  'attr.has_free': {'q': '공제 안 받은 돈도 좀 있어요', 'v': '있음'}}},
        {'q': '와이프랑 저 각자 연금저축에 월 50씩 넣어요. 저는 총급여 4천인데 공제 얼마씩 받아요?',
         'read': {'attr.ask_kind': {'q': '공제 얼마씩 받아요?', 'v': ['납입']},
                  'attr.account_kind': {'q': '연금저축에 월 50씩 넣어요', 'v': '연금저축'},
                  'attr.contrib_acct': {'q': '연금저축에 월 50씩 넣어요', 'v': ['연금저축']},
                  'attr.person': {'q': '와이프랑 저 각자', 'v': '여럿각자'},
                  'attr.withdraw_year': {'q': '넣어요', 'v': 0}}},
        {'q': '72세고 IRP 가입한 지 3년 됐어요. 8월부터 매달 80씩 받기 시작했는데 한 달에 세금 얼마 떼요?',
         'read': {'attr.ask_kind': {'q': '한 달에 세금 얼마 떼요?', 'v': ['연금수령']},
                  'attr.account_kind': {'q': 'IRP', 'v': 'IRP'},
                  'attr.age': {'q': '72세고', 'v': 72},
                  'attr.period': {'q': 'IRP 가입한 지 3년 됐어요', 'v': 3},
                  'attr.months': {'q': '8월부터 매달 80씩', 'v': 5},
                  'attr.ask_period': {'q': '한 달에 세금 얼마 떼요?', 'v': '월'},
                  'attr.started': {'q': '받기 시작했는데', 'v': '개시함'}}},
    ],
}


# ---------------------------------------------------------------------------
# 글자에 끼워 넣기
# ---------------------------------------------------------------------------
def j(x):
    return json.dumps(x, ensure_ascii=False)


def match_close(s, i):
    """s[i] 가 '{' 나 '[' 일 때 짝 닫는 괄호 위치(문자열 안 괄호는 건너뜀)."""
    depth, k, ins = 0, i, False
    while k < len(s):
        c = s[k]
        if ins:
            if c == '\\':
                k += 2
                continue
            if c == '"':
                ins = False
        elif c == '"':
            ins = True
        elif c in '{[':
            depth += 1
        elif c in '}]':
            depth -= 1
            if depth == 0:
                return k
        k += 1
    raise ValueError('짝 없음')


def obj_span(s, anchor, start=0):
    i = s.index(anchor, start)
    i = s.rindex('{', 0, i + 1)
    return i, match_close(s, i)


def fmt_read(r, ind):
    """read 를 여러 줄로 — 칸마다 한 줄, values·evidence·engine 은 항목마다 한 줄."""
    pad = ' ' * ind
    lines = []
    for k, v in r.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            inner = (',\n' + pad + '  ').join(j(x) for x in v)
            lines.append(f'{j(k)}: [\n{pad}  {inner}]')
        else:
            lines.append(f'{j(k)}: {j(v)}')
    return '{' + (',\n' + pad + ' ').join(lines) + '}'


def build(text):
    s = text
    # 1) 기존 속성에 read
    for aid, r in OLD_READ.items():
        a, b = obj_span(s, f'{{"id": "{aid}",')
        s = s[:b] + ',\n   "read": ' + fmt_read(r, 4) + s[b:]
    # 2) 새 속성 — attributes 목록 끝에
    i = s.index('"attributes": [') + len('"attributes": ')
    e = match_close(s, i)
    new = []
    for at in NEW_ATTRS:
        head = {k: v for k, v in at.items() if k not in ('read', 'quotes')}
        txt = j(head)[:-1] + ',\n   "quotes": ' + j(at['quotes']) + ',\n   "read": ' + fmt_read(at['read'], 4) + '}'
        new.append('  ' + txt)
    body_end = s.rindex('}', i, e) + 1
    s = s[:body_end] + ',\n' + ',\n'.join(new) + s[body_end:]
    # 3) reading — attributes 바로 뒤
    i = s.index('"attributes": [') + len('"attributes": ')
    e = match_close(s, i)
    ex = (',\n  ').join(j(x) for x in READING['examples'])
    s = s[:e + 1] + ',\n\n "reading": {"note": ' + j(READING['note']) + ',\n  "examples": [\n  ' + ex + ']}' + s[e + 1:]
    # 4) 기존 조건에 cmp, 규칙에 read_conditions
    for rid in [f'R-{n:02d}' for n in range(1, 16)]:
        a, b = obj_span(s, f'{{"id": "{rid}",')
        seg = s[a:b + 1]
        for r2, aid, cmp in OLD_CMP:
            if r2 != rid:
                continue
            m = re.search(r'\{"attr": "' + re.escape(aid) + r'"[^{}]*\}', seg)
            assert m, (rid, aid)
            c = m.group(0)
            seg = seg[:m.start()] + c[:-1] + ', "cmp": ' + j(cmp) + '}' + seg[m.end():]
        if rid in READ_CONDS:
            ci = seg.index('"conditions": [') + len('"conditions": ')
            ce = match_close(seg, ci)
            rc = (',\n    ').join(j(x) for x in READ_CONDS[rid])
            seg = seg[:ce + 1] + ',\n   "read_conditions": [\n    ' + rc + ']' + seg[ce + 1:]
        s = s[:a] + seg + s[b + 1:]
    return s


def main():
    pre = open(PRE, encoding='utf-8').read()
    cur = open(DST, encoding='utf-8').read()
    last = open(LAST, encoding='utf-8').read() if os.path.exists(LAST) else None
    if cur != pre and cur != last:
        sys.exit('data/ontology.json 이 사본·지난 결과와 다르다 — 다른 손이 고쳤을 수 있어 멈춘다')
    out = build(pre)
    d = json.loads(out)                               # 깨지면 여기서 멈춘다
    assert sum(1 for a in d['attributes'] if a.get('read')) == len(OLD_READ) + len(NEW_ATTRS)
    open(DST, 'w', encoding='utf-8', newline='\n').write(out)
    open(LAST, 'w', encoding='utf-8', newline='\n').write(out)
    print('ok', len(pre), '->', len(out))


if __name__ == '__main__':
    main()
