# -*- coding: utf-8 -*-
"""生成修正口径后的买点清单工作簿（xlsx 技能工作流：项目工作区 + validation.json + 校验）。"""
import os
import sys
import json
import shutil
import subprocess

WORK = os.path.dirname(os.path.abspath(__file__))
SKILL = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/official/xlsx"
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

out = subprocess.run([sys.executable, os.path.join(SKILL, "scripts/project_manager.py"), "init", "buy_list",
                      "--dir", os.path.join(WORK, ".dumate")], capture_output=True, text=True, check=True)
PROJ = out.stdout.strip().split("Project created: ")[-1].strip()
print("project:", PROJ)

m = pd.read_csv(os.path.join(WORK, "buypoint_merged_v2.csv"), dtype={"代码": str}).fillna("—")
fix = pd.read_csv(os.path.join(WORK, "smc_fix_compare.csv"), dtype={"代码": str}).fillna("—")
rep = pd.read_csv(os.path.join(WORK, "smc_fix_reasons.csv"), dtype={"ts_code": str})
zones = pd.read_csv(os.path.join(WORK, "smc_zones_v2.csv"), dtype={"ts_code": str})

BLUE, LIGHT1, LIGHT2 = "1F3864", "F2F5FA", "FFFFFF"
f_title = Font(name="Arial", size=20, bold=True, color=BLUE)
f_sub = Font(name="Arial", size=10, italic=True, color="808080")
f_head = Font(name="Arial", size=11, bold=True, color="FFFFFF")
f_body = Font(name="Arial", size=11)
fill_head = PatternFill("solid", fgColor=BLUE)
fill1 = PatternFill("solid", fgColor=LIGHT1)
fill2 = PatternFill("solid", fgColor=LIGHT2)
thin = Side(style="thin", color="CCCCCC")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
CEN = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)


def sheet(wb, name, title, head, widths, df, first=False):
    ws = wb.active if first else wb.create_sheet(name)
    ws.title = name
    ws["A1"] = title
    ws["A1"].font = f_title
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(head))
    ws["A2"] = "数据截至 2026-10-30 收盘｜日线前复权｜缠论：分型版 struct + MACD(20,40,5)；SMC：结构突破+新鲜区间回踩"
    ws["A2"].font = f_sub
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(head))
    hr = 4
    for j, h in enumerate(head, start=1):
        c = ws.cell(row=hr, column=j, value=h)
        c.font = f_head
        c.fill = fill_head
        c.alignment = CEN
        c.border = BORDER
        ws.column_dimensions[get_column_letter(j)].width = widths[j - 1]
    ws.row_dimensions[hr].height = 36
    for i, (_, r) in enumerate(df.iterrows()):
        rr = hr + 1 + i
        for j, h in enumerate(head, start=1):
            v = r[h]
            if isinstance(v, float) and v != v:
                v = "—"
            c = ws.cell(row=rr, column=j, value=v)
            c.font = f_body
            c.alignment = CEN if h not in ("缠论笔结构", "说明") else LEFT
            c.border = BORDER
            c.fill = fill1 if i % 2 == 0 else fill2
            if h in ("缠论价", "SMC跌到价", "SMC买点价"):
                c.number_format = "0.00"
            elif h == "缠论确认后涨幅":
                c.number_format = "0.0%"
        ws.row_dimensions[rr].height = 30
    ws.freeze_panes = ws.cell(row=hr + 1, column=1)
    ws.sheet_view.zoomScale = 90
    return ws


# 逐只清单（33 行）：把未命中原因/失效说明补进说明列
reasons = dict(zip(rep.ts_code, rep.失效原因))
m["说明"] = m.apply(lambda r: ("—" if r.命中 != "未命中" else
                          ("该标的无有效缠论买点；上一版 SMC 买点已按新口径失效（%s）" % reasons.get(r.代码, "区间已被填补或跌破"))
                          if r.代码 in reasons else "该标的无有效缠论买点，且无满足新口径的 SMC 回踩"), axis=1)
H1 = ["代码", "名称", "命中类型", "缠论买点", "缠论日期", "缠论价", "缠论笔结构", "缠论确认后涨幅",
      "SMC类型", "SMC区间", "SMC跌到价", "SMC买点价", "SMC首次触碰日", "说明"]
m2 = m.rename(columns={"命中": "命中类型"})[H1]
W1 = [13, 12, 13, 11, 12, 10, 46, 15, 10, 14, 12, 12, 15, 46]

H2 = ["代码", "名称", "修正前SMC买点", "修正后SMC买点", "对比", "最新有效买点"]
W2 = [13, 12, 34, 40, 14, 52]

summ = zones[zones.失效原因 != "有效"].groupby("失效原因").size().reset_index(name="区间数")
summ.loc[len(summ)] = ["有效（可作为买点）", int(zones.修正后仍成立.astype(bool).sum())]
summ.loc[len(summ)] = ["区间候选合计", int(len(zones))]
H3 = ["失效原因", "区间数"]
W3 = [52, 12]

wb = Workbook()
sheet(wb, "买点清单", "33 只三标签标的：缠论买点与 SMC 买点（修正口径）", H1, W1, m2, first=True)
sheet(wb, "SMC修正对比", "SMC 修正前后逐只对比（最近 30 个交易日内）", H2, W2, fix[H2])
sheet(wb, "区间失效统计", "SMC 区间候选的失效原因统计（全历史）", H3, W3, summ)

os.makedirs(os.path.join(PROJ, "working"), exist_ok=True)
OUT = os.path.join(PROJ, "working", "output.xlsx")
wb.save(OUT)
spec = {"tables": [
    {"name": "list", "sheet": "买点清单", "header_row": 4, "data_start_row": 5, "start_column": "A",
     "expected_headers": H1, "column_rules": {"代码": {"type": "text", "width": 9, "pattern": r"[0-9]{6}\.(SH|SZ|BJ)"}}},
    {"name": "smc_fix", "sheet": "SMC修正对比", "header_row": 4, "data_start_row": 5, "start_column": "A",
     "expected_headers": H2, "column_rules": {"代码": {"type": "text", "width": 9, "pattern": r"[0-9]{6}\.(SH|SZ|BJ)"}}},
    {"name": "zones", "sheet": "区间失效统计", "header_row": 4, "data_start_row": 5, "start_column": "A",
     "expected_headers": H3}], "gap_columns": 1, "gap_rows": 2,
    "formula_validation": {"engine": "auto", "timeout_seconds": 60}}
with open(os.path.join(PROJ, "working", "validation.json"), "w", encoding="utf-8") as f:
    json.dump(spec, f, ensure_ascii=False, indent=1)
print("saved", OUT, "| sheets:", wb.sheetnames, "| 清单行数:", len(m2))
r = subprocess.run([sys.executable, os.path.join(SKILL, "scripts/validate.py"), OUT, "--work-dir", PROJ],
                   capture_output=True, text=True)
j = json.loads(r.stdout)
print("validate status:", j.get("status"))
for c in j.get("checks", []):
    print("  -", c["name"], c["status"], c.get("total_errors", ""))
    for e in c.get("errors", [])[:5]:
        print("     *", e.get("type"), e.get("sheet"), str(e.get("message"))[:70])