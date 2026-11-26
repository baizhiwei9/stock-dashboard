import sys, os
sys.path.insert(0, '/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器/scripts')
os.chdir('/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器')
import importlib.util
spec = importlib.util.spec_from_file_location('gs', '/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器/scripts/growth_screener_dual.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
