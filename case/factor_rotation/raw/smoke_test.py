# -*- coding: utf-8 -*-
import importlib.util, sys
spec = importlib.util.spec_from_file_location("fe", "raw/factor_engine.py")
fe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fe)
# 单周冒烟：最新周截面
xs = fe.build_cross_section("20261009", ["20261009"])
print("xsec cols:", len(xs.columns), "rows:", len(xs))
print("cols:", sorted(xs.columns.tolist()))
print("non-null per col:")
print(xs.notna().sum().to_string())
