# regex_patterns.py
import re

QINGLI_AI_BIAOQIAN_ZHUJIE = re.compile(r"<!--.*?-->|<content>|</content>", re.DOTALL)