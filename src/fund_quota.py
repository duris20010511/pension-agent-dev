# -*- coding: utf-8 -*-
"""
④ 찾기 보강 — 질의에 언급된 펀드마다 최소 1조각 보장.

진단(diag_product.py)에서 나온 사실:
  Q-019 "솔로몬 단기·중장기·장기 뭐가 달라요"
        → 상위에 중장기·장기만 오고 단기가 top-100 밖
  Q-022 "라이프사이클 7090 이랑 2030"
        → 7090 이 상위를 독식하고 2030 은 32위

펀드명 자체는 잘 맞힌다. 문제는 **여러 펀드를 물었을 때 한 펀드가 상위를 독식**하는 것.

하위질의 분해(폐기)와 다른 점:
  분해는 질의를 쪼개 라운드로빈으로 섞었고, 그 결과 정답 조각이 밀려 R@10 이 75→55 로 무너졌다.
  여기서는 **원래 순위를 그대로 두고**, 언급된 펀드 중 상위에 한 조각도 없는 펀드만
  그 펀드의 최상위 조각을 끌어와 뒤에 덧붙인다. 기존 순위를 건드리지 않는다.
"""
import re

# 투자설명서 제목에서 펀드를 식별할 수 있는 키워드를 뽑는다
STOP = {'증권', '투자신탁', '자투자신탁', '모투자신탁', '전환형', '연금', '호', '주식',
        '채권', '혼합', '재간접', '미래에셋', '한국투자', '삼성', '신한', 'KB', '키움'}


def fund_keys(title):
    """제목에서 식별 키워드 집합. '미래에셋솔로몬단기국공채…' → {'솔로몬', '단기국공채', ...}"""
    if not title:
        return set()
    t = re.sub(r'[\[\]()（）]', ' ', title)
    keys = set()
    # 한글 3자 이상 덩어리
    for w in re.findall(r'[가-힣]{3,}', t):
        for s in STOP:
            w = w.replace(s, ' ')
        for part in w.split():
            if len(part) >= 2:
                keys.add(part)
    # 숫자 코드 (7090, 2030 같은 것)
    keys |= set(re.findall(r'\b\d{4}\b', t))
    keys |= set(re.findall(r'\b[A-Z]{2,}\b', t))
    return keys


def mentioned_funds(question, chunks_by_file, titles):
    """질의에 언급된 펀드 파일들을 찾는다. 반환: {파일명: 매칭점수}"""
    q = re.sub(r'\s+', '', question)
    hit = {}
    for f, title in titles.items():
        if not f.startswith('KR') or not title:
            continue
        keys = fund_keys(title)
        if not keys:
            continue
        score = sum(1 for k in keys if k in q)
        if score >= 2:              # 2개 이상 맞아야 그 펀드를 지목한 것으로 본다
            hit[f] = score
    return hit


def apply_quota(question, hits, all_chunks, titles, by_file, max_add=4):
    """상위에 한 조어도 없는 '언급된 펀드'가 있으면 그 펀드 최상위 조각을 덧붙인다.

    반환: (보강된 hits, 추가된 파일 목록)
    """
    mentioned = mentioned_funds(question, by_file, titles)
    if len(mentioned) < 2:          # 펀드를 하나만 물었으면 손대지 않는다
        return hits, []

    present = {c['file'] for c, _ in hits}
    missing = [f for f in mentioned if f not in present]
    if not missing:
        return hits, []

    # 매칭점수 높은 순으로, 그 파일에서 원래 랭킹상 가장 높은 조각을 가져온다
    missing.sort(key=lambda f: -mentioned[f])
    added = []
    extra = []
    rank_of = {id(c): i for i, (c, _) in enumerate(hits)}
    for f in missing[:max_add]:
        cands = by_file.get(f, [])
        if not cands:
            continue
        # 그 파일 조각 중 원래 검색에 안 들어온 것 → 첫 페이지 쪽(개요·위험등급)이 유용하다
        pick = min(cands, key=lambda c: (c['page'] or 999, c['idx']))
        extra.append((pick, 0.0))
        added.append(f)
    return hits + extra, added
