#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ot_core.py — 回归测试（纯标准库 unittest，不需要联网、不需要 data_hub）

只测「纯函数 + 不取数的分支」，把本轮修的每个缺陷都钉成一条断言：
    python -m unittest discover -s skill -p "test_*.py" -v
    # 或在 skill/ 下：python test_ot_core.py

覆盖：
  P0-1  稳态模板：auto 选窗、合法性校验、空模板硬失败
  P0-2  状态机：多数票、六档状态全部可达、靠岸中不再被企稳可起吃掉
  P0-3  健壮性：_norm_dates 缺 amount/vol 不 KeyError；ot_alert 无 charts 不崩
  P1-1  谱共振：λmax/N 与平均 λ_L 是仿射关系，判色只看 λ_L
  P1-3  分位：因果分位无未来函数、与全样本分位口径不同
"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ot_core as oc


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _synthetic_close(n=400, seed=0, drift=0.0, vol=0.012, start="2024-01-01"):
    """生成一条合成收盘价序列（工作日）。"""
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(close, index=idx)


def _close_wide(n=400, k=6, seed=0):
    return pd.DataFrame(
        {f"c{i}": _synthetic_close(n, seed=seed + i) for i in range(k)})


# ---------------------------------------------------------------------------
# P0-1 稳态模板
# ---------------------------------------------------------------------------

class TestSteadyWindow(unittest.TestCase):
    def test_auto_picks_legal_window(self):
        cw = _close_wide()
        st = oc.resolve_steady_window(cw, mode="auto", strict=True)
        self.assertEqual(st["mode"], "auto")
        self.assertGreaterEqual(st["days"], oc.STEADY_MIN_DAYS)
        # 合法性三条件
        self.assertLessEqual(abs(st["ret"]), oc.STEADY_MAX_ABS_RET)
        lo, hi = oc.STEADY_VOL_RATIO_BAND
        self.assertGreaterEqual(st["vol_ratio"], lo)
        self.assertLessEqual(st["vol_ratio"], hi)

    def test_auto_avoids_crash_segment(self):
        """把最后一段做成暴跌，auto 不应把模板选在暴跌段上。"""
        cw = _close_wide(n=500)
        cw.iloc[-80:] = cw.iloc[-80:].values * np.linspace(1.0, 0.55, 80)[:, None]
        st = oc.resolve_steady_window(cw, mode="auto", strict=True)
        self.assertLess(st["end"], cw.index[-80].strftime("%Y-%m-%d"))

    def test_manual_illegal_window_raises(self):
        """P0-1：把窗口指定在一段暴跌上，必须硬失败，不能静默返回 NaN。"""
        cw = _close_wide(n=400)
        # 在 100~160 区间人为砸出一个 -35% 的暴跌段
        cw.iloc[100:161] = cw.iloc[100:161].values * np.linspace(1.0, 0.65, 61)[:, None]
        start = cw.index[100].strftime("%Y-%m-%d")
        end = cw.index[160].strftime("%Y-%m-%d")
        with self.assertRaises(SystemExit):
            oc.resolve_steady_window(cw, mode="manual", start=start, end=end, strict=True)

    def test_empty_window_raises(self):
        """窗口落在样本外 → 有效交易日不足 → 硬失败。"""
        cw = _close_wide(n=200)
        with self.assertRaises(SystemExit):
            oc.resolve_steady_window(cw, mode="manual", start="2030-01-01",
                                     end="2030-03-01", strict=True)

    def test_validate_reports_reason(self):
        cw = _close_wide(n=300)
        cw.iloc[50:111] = cw.iloc[50:111].values * np.linspace(1.0, 0.6, 61)[:, None]
        s = cw.index[50].strftime("%Y-%m-%d")
        e = cw.index[110].strftime("%Y-%m-%d")
        with self.assertRaises(SystemExit) as cm:
            oc.validate_steady_window(cw, s, e, strict=True)
        self.assertIn("稳态模板不合法", str(cm.exception.code))

    def test_validate_too_few_days(self):
        cw = _close_wide(n=300)
        s = cw.index[10].strftime("%Y-%m-%d")
        e = cw.index[20].strftime("%Y-%m-%d")   # 仅 11 个交易日 < STEADY_MIN_DAYS
        with self.assertRaises(SystemExit) as cm:
            oc.validate_steady_window(cw, s, e, strict=True)
        self.assertIn("稳态模板不合法", str(cm.exception.code))


# ---------------------------------------------------------------------------
# P1-3 分位口径
# ---------------------------------------------------------------------------

class TestPctRank(unittest.TestCase):
    def test_causal_has_no_lookahead(self):
        """因果分位：把未来数据改掉，历史分位不能变。"""
        s = pd.Series(np.random.default_rng(1).normal(size=200))
        a = oc.causal_rank(s)
        s2 = s.copy()
        s2.iloc[150:] = s2.iloc[150:] * 10  # 篡改「未来」
        b = oc.causal_rank(s2)
        pd.testing.assert_series_equal(a.iloc[:150], b.iloc[:150])

    def test_causal_differs_from_full(self):
        s = pd.Series(np.random.default_rng(2).normal(size=300).cumsum())
        full = oc.pct_rank(s, mode="full")
        causal = oc.pct_rank(s, mode="causal")
        self.assertFalse(np.allclose(full.values, causal.values, equal_nan=True))

    def test_causal_min_periods(self):
        s = pd.Series(np.arange(100, dtype=float))
        r = oc.causal_rank(s, min_periods=60)
        self.assertTrue(np.isnan(r.iloc[:59]).all())
        self.assertFalse(np.isnan(r.iloc[59]))


# ---------------------------------------------------------------------------
# P0-3 数据规整
# ---------------------------------------------------------------------------

class TestNormDates(unittest.TestCase):
    def test_missing_amount_and_vol(self):
        """P0-3：只有 date+close 时原来 KeyError: ['amount'] not in index。"""
        idx = pd.bdate_range("2024-01-01", periods=30)
        df = pd.DataFrame({"trade_date": idx.strftime("%Y%m%d"), "close": np.arange(30.0) + 10})
        out = oc._norm_dates(df)
        self.assertIn("amount", out.columns)
        self.assertEqual(len(out), 30)

    def test_only_vol_promotes_to_amount(self):
        idx = pd.bdate_range("2024-01-01", periods=30)
        df = pd.DataFrame({"trade_date": idx.strftime("%Y%m%d"),
                           "close": np.arange(30.0) + 10, "vol": np.arange(30.0) + 1})
        out = oc._norm_dates(df)
        self.assertTrue(np.allclose(out["amount"].values, out["vol"].values))

    def test_real_amount_kept(self):
        idx = pd.bdate_range("2024-01-01", periods=30)
        df = pd.DataFrame({"trade_date": idx.strftime("%Y%m%d"),
                           "close": np.arange(30.0) + 10,
                           "vol": np.arange(30.0) + 1,
                           "amount": np.arange(30.0) * 7 + 3})
        out = oc._norm_dates(df)
        self.assertTrue(np.allclose(out["amount"].values, np.arange(30.0) * 7 + 3))


# ---------------------------------------------------------------------------
# P0-2 状态机
# ---------------------------------------------------------------------------

class TestClassifyState(unittest.TestCase):
    def _ctx(self, T=60, C=6, seed=0):
        rng = np.random.default_rng(seed)
        W = np.abs(rng.normal(0.4, 0.15, (T, C)))
        R = np.full((T, C), 50.0)
        J = np.full(T, 1.0)
        JR = np.full(T, 50.0)
        return W, R, J, JR

    def test_all_states_reachable(self):
        """P0-2：六档状态必须全部可达（原实现靠岸中/底部摩擦是死代码）。"""
        big, small, codes = [0, 1], [3, 4, 5], [f"c{i}" for i in range(6)]
        # _not_new_high 需要 t >= 2*PLATEAU_K 才有定义，历史长度要够
        T = 2 * oc.PLATEAU_K + oc.DECLINE_K
        t = T - 1
        cases = {
            "🟥 共振崩塌": dict(last=[95] * 6, JR=95.0, J=2.0, hist=50.0),
            "🟧 底部摩擦": dict(last=[70] * 6, JR=50.0, J=1.0, hist=50.0),
            # 企稳可起要求：多数低位 + 联合 W1 低位（JR<40）+ 联合 W1 连降（J 比 K 天前小）
            "🟩 企稳可起": dict(last=[20] * 6, JR=20.0, J=1.0, hist=50.0, Jhist=2.0),
            "ℹ️ 大盘先稳，小盘滞后": dict(last=[20, 20, 50, 70, 70, 70], JR=50.0, J=1.0, hist=50.0),
            "🟨 靠岸中": dict(last=[55, 55, 55, 58, 58, 58], JR=50.0, J=1.0, hist=90.0),
            "⚪ 中性": dict(last=[50] * 6, JR=50.0, J=1.0, hist=50.0),
        }
        for want, kw in cases.items():
            W = np.full((T, 6), 0.4)
            R = np.vstack([np.full((T - 1, 6), kw["hist"]), [kw["last"]]])
            J = np.concatenate([np.full(T - 1, kw.get("Jhist", kw["J"])), [kw["J"]]])
            JR = np.concatenate([np.full(T - 1, 50.0), [kw["JR"]]])
            got = oc.classify_state(W, R, J, JR, t, big, small, codes)
            self.assertEqual(got, want, msg=f"{want} 未触发，实际得到 {got}")

    def test_majority_not_all(self):
        """4/6 满足就成立，不再要求 6/6。"""
        T = oc.DECLINE_K + 1
        t = T - 1
        W = np.full((T, 6), 0.4)
        R = np.vstack([np.full((T - 1, 6), 50.0), [[95, 95, 95, 95, 10, 10]]])
        J = np.full(T, 2.0)
        JR = np.full(T, 95.0)
        got = oc.classify_state(W, R, J, JR, t, [0, 1], [3, 4, 5],
                                [f"c{i}" for i in range(6)])
        self.assertEqual(got, "🟥 共振崩塌")

    def test_three_of_six_is_not_enough(self):
        """3/6 不成立（多数票门槛是 4）。"""
        T = oc.DECLINE_K + 1
        t = T - 1
        W = np.full((T, 6), 0.4)
        R = np.vstack([np.full((T - 1, 6), 50.0), [[95, 95, 95, 10, 10, 10]]])
        J = np.full(T, 2.0)
        JR = np.full(T, 95.0)
        got = oc.classify_state(W, R, J, JR, t, [0, 1], [3, 4, 5],
                                [f"c{i}" for i in range(6)])
        self.assertNotEqual(got, "🟥 共振崩塌")

    def test_crossed_low_constant(self):
        """CROSSED_LOW 必须宽于「到底」的 40，否则靠岸中会被企稳可起吃掉。"""
        self.assertGreater(oc.CROSSED_LOW, 40)
        self.assertLess(oc.CROSSED_LOW, 80)


# ---------------------------------------------------------------------------
# P1-1 谱共振冗余
# ---------------------------------------------------------------------------

class TestResonance(unittest.TestCase):
    def test_resonance_is_affine_of_mean_lamL(self):
        """λmax/N = 1/N + (1-1/N)·ρ 对等相关矩阵是恒等式，说明谱共振不是独立信号。"""
        N = 6
        for rho in (0.1, 0.3, 0.5, 0.7, 0.9):
            M = np.full((N, N), rho)
            np.fill_diagonal(M, 1.0)
            lam_max_over_n = np.linalg.eigvalsh(M)[-1] / N
            base = 1.0 / N + (1.0 - 1.0 / N) * rho
            self.assertAlmostEqual(lam_max_over_n, base, places=10)

    def test_residual_removes_level(self):
        """残差化后与 λ_L 的相关性应显著低于 λmax/N 与 λ_L 的相关性。"""
        N = 6
        rng = np.random.default_rng(7)
        ll = rng.uniform(0.5, 0.9, 300)
        res = np.array([
            np.linalg.eigvalsh(np.where(np.eye(N, dtype=bool), 1.0,
                                        np.clip(v + rng.normal(0, 0.03, (N, N)), 0, 1)))[-1] / N
            for v in ll
        ])
        base = 1.0 / N + (1.0 - 1.0 / N) * ll
        excess = res - base
        c_raw = abs(np.corrcoef(res, ll)[0, 1])
        c_exc = abs(np.corrcoef(excess, ll)[0, 1])
        self.assertLess(c_exc, c_raw)


# ---------------------------------------------------------------------------
# P0-3 ot_alert 降级
# ---------------------------------------------------------------------------

class TestAlertDegrades(unittest.TestCase):
    def test_no_hardcoded_mailbox(self):
        """P0-3：仓库是公开的，不能再有硬编码收发件邮箱。"""
        import ot_alert
        src = open(os.path.join(HERE, "ot_alert.py"), encoding="utf-8").read()
        for bad in ("baizhiwei9492@dingtalk.com", "410119229@qq.com"):
            self.assertNotIn(bad, src)
        self.assertEqual(ot_alert.MAIL_FROM, os.environ.get("ALERT_MAIL_FROM", ""))
        self.assertEqual(ot_alert.MAIL_TO, os.environ.get("ALERT_MAIL_TO", ""))

    def test_state_file_outside_repo(self):
        """状态文件不应落在仓库目录里（否则会被提交）。"""
        import ot_alert
        repo = os.path.dirname(HERE)
        self.assertFalse(os.path.abspath(ot_alert.STATE_PATH).startswith(repo + os.sep))

    def test_send_mail_skips_without_config(self):
        import ot_alert
        old = (ot_alert.MAIL_FROM, ot_alert.MAIL_TO)
        try:
            ot_alert.MAIL_FROM = ""
            ot_alert.MAIL_TO = ""
            r = ot_alert.send_mail("t", "b")
            self.assertIn("skipped", r)
        finally:
            ot_alert.MAIL_FROM, ot_alert.MAIL_TO = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
