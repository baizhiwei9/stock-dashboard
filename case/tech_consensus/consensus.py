# -*- coding: utf-8 -*-
"""多空平汇总投票"""
from config import SCORE_LONG, SCORE_SHORT, SCORE_FLAT

VIEW_SCORE = {"多": SCORE_LONG, "空": SCORE_SHORT, "震荡": SCORE_FLAT, "N/A": None}


def to_score(view):
    return VIEW_SCORE.get(view, None)


def consensus(views, method_names=None):
    """views: {方法名: view}；返回 (结论, score, 明细)
    结论: 买(看多) / 卖(看空) / 平(震荡) —— 按票面总分：
    score>0 -> 买；score<0 -> 卖；==0 -> 平
    """
    total, n_na = 0, 0
    detail = []
    for m, v in views.items():
        s = to_score(v)
        if s is None:
            n_na += 1
            detail.append(f"{m}: N/A")
        else:
            total += s
            detail.append(f"{m}: {v}({s:+d})")
    if total > 0:
        view = "买"
    elif total < 0:
        view = "卖"
    else:
        view = "平"
    return view, total, detail, n_na