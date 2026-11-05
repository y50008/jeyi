"""lang.py — 文本原语（v4.0）：分词 / 停用词 / 问题判定 / 日期解析 / 情绪判定 / 实体抽取

为什么单独一层：v3 的 bug 之一是「同一个中文分词需求散落在三个模块」——
score.py 有 tokenize()，settle.py 却用 str.split() 算 Jaccard，于是中文冲突检测恒为 0。
把原语收进 lang.py，所有模块只走同一套，从结构上消灭这类失配。

设计约束（刻意）：零第三方依赖。中文一律走「字二元组 + 内容字一元组」，
配合停用词表；这是没有分词器时性价比最高的方案：
  · 二元组保留短语（"猫叫"、"过敏"）
  · 内容字一元组解决"家猫/猫叫"这类跨词命中（v3 只留二元组，导致"我家猫叫什么"检索不到）
"""
from __future__ import annotations
import re
import time
import datetime as _dt

CJK_RANGE = "\u4e00-\u9fff"

_LATIN = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(f"[{CJK_RANGE}]+")
_QUOTED = re.compile(r"[「『\"“]([^」』\"”]{1,24})[」』\"”]")
_NAMED = re.compile(f"叫(?:做|作)?([{CJK_RANGE}]{{1,8}})")
# 位移动词后的目的地：去/到/回 + 2-6 字，到下一个动作/标点为止（不碰"在"，太容易误抓）
_PLACE = re.compile(
    f"(?:去|到|回)([{CJK_RANGE}]{{2,6}}?)(?=出差|上班|工作|玩|旅游|度假|开会|看病|"
    f"吃饭|，|。|、|$|了|过)")
_DIGITS = re.compile(r"\d+(?:\.\d+)?")

# 只有纯功能词进停用表；否定词（不/没/别）和程度词（好/难）绝不丢——
# 丢掉"不"会把"我不喜欢"变成"我喜欢"，这是语义反转级事故。
CJK_STOP_CHARS = set(
    "的了是在有和就都很也到说要去会着看这那我你他她它们个把被让给与或但而且之其为于从对上下里中"
    "叫名字只又才更最太再可能过来出多少些样种次点")
CJK_STOP_BIGRAMS = {
    "什么", "怎么", "怎样", "如何", "为什", "什么", "哪里", "哪儿", "哪个", "哪些",
    "多少", "这个", "那个", "这些", "那些", "我们", "你们", "他们", "她们", "它们",
    "可以", "是否", "已经", "还是", "就是", "然后", "因为", "所以", "但是", "不过",
    "一下", "一些", "自己", "现在", "时候", "觉得", "知道", "一个", "没有", "不是",
    "名字", "叫什", "么名", "叫做", "想问", "告诉",
    # 频率副词是典型的"假朋友"：「我一般几点下班」会被「我周末一般会去…」抢走
    "一般", "平时", "经常", "通常", "常常", "老是", "总是",
}

# 疑问/假设/玩笑：这类句子不入卡（v3 的 _ME 把"？"排除在抽取范围外，导致问句漏进记忆）
WH_WORDS = ("什么", "怎么", "怎样", "如何", "为什么", "为啥", "哪里", "哪儿", "哪个",
            "哪些", "多少", "是否", "是不是", "能不能", "可不可以", "要不要", "有没有",
            "谁", "几时", "几点", "哪天", "什么样")
QUESTION_TAILS = ("吗", "呢", "吧", "么", "嘛")
HYPOTHETICAL = ("如果", "假如", "要是", "假设", "万一", "倘若")
JOKING = ("开玩笑", "哈哈", "呵呵", "随便说说", "逗你", "玩笑", "说着玩")
HEARSAY = ("听说", "据说", "朋友说", "网上说", "别人说", "同事说", "据传")

POS_WORDS = ("喜欢", "爱", "开心", "高兴", "满意", "期待", "放心", "舒服", "顺利",
             "成功", "感谢", "信任", "踏实", "振奋", "暖", "喜欢", "好看", "好吃",
             "好喝", "不错", "棒", "强")
NEG_WORDS = ("讨厌", "烦", "难受", "焦虑", "担心", "害怕", "生气", "失望", "累",
             "痛", "病", "过敏", "失眠", "压力", "后悔", "糟", "崩溃", "怕", "苦",
             "差", "烂", "不喜欢", "不想")
_INTENSIFIERS = ("很", "非常", "特别", "太", "超", "极其", "巨", "十分", "格外")

# 承诺/前瞻记忆：这类句子要能"到期主动提起"
COMMIT_CUES = ("我要", "我会", "我打算", "我计划", "我准备", "我争取", "我得", "我需要",
               "答应", "承诺", "约好", "说好", "下次", "回头")
CORRECTION_CUES = ("改成", "改为", "不对", "错了", "记错", "纠正", "更正", "其实我是",
                   "不是", "而是", "应该是")

_TIME_MARKERS = (r"\d{4}-\d{1,2}-\d{1,2}", r"\d{1,2}月\d{1,2}[日号]",
                 r"\d{1,2}月", r"下{1,2}周[一二三四五六日天]", r"这周[一二三四五六日天]",
                 r"本周[一二三四五六日天]", r"周[一二三四五六日天]",
                 r"下{1,2}周(?![一二三四五六日天])",
                 r"今天|今日|明天|明日|后天|大后天|昨天|前天",
                 r"下个?月|这个?月|本月|月底|月初|年底|明年|今年|周末",
                 r"\d+\s*天后", r"\d+\s*周后")
_TIME_RE = re.compile("|".join(_TIME_MARKERS))

_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


# ---------------------------------------------------------------- 分词
def tokenize(text: str) -> list:
    """拉丁词 + 中文二元组 + 内容字一元组。返回带重复的 token 列表（tf 需要）。"""
    if not text:
        return []
    out = []
    for w in _LATIN.findall(text.lower()):
        if len(w) >= 2 or w.isdigit():
            out.append(w)
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            if run not in CJK_STOP_CHARS:
                out.append(run)
            continue
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in CJK_STOP_BIGRAMS:
                out.append(bg)
        for ch in run:
            if ch not in CJK_STOP_CHARS:
                out.append(ch)
    return out


def term_set(text: str) -> set:
    return set(tokenize(text))


# 属性归一化别名表：**只收"同一属性"的不同说法**（年纪=岁、配偶=老婆/妻子），
# 不收"上位词"关系（饮料≠茶）——后者必须靠 embedding，硬编码只会自欺。
# 这是 embedding 缺位时的最小补丁，生产应整体替换为向量召回。
ALIASES = {
    "年纪": ("岁", "年龄"), "年龄": ("岁", "年纪"),
    "老婆": ("妻子", "媳妇", "爱人"), "妻子": ("老婆", "媳妇", "爱人"),
    "女儿": ("孩子", "闺女"), "孩子": ("女儿", "儿子"),
    "上班": ("工作", "公司"), "工作": ("上班", "公司"),
    "讨厌": ("不喜欢", "烦"), "不喜欢": ("讨厌", "烦"), "烦": ("讨厌", "不喜欢"),
    "爸爸": ("父亲", "爸"), "爸": ("父亲", "爸爸"),
    "妈妈": ("母亲", "妈"), "妈": ("母亲", "妈妈"),
    "体检": ("检查", "医院"),
}


def expand(tokens: list) -> list:
    """查询侧别名扩展。只扩展查询，不扩展文档——避免污染 idf。"""
    out = list(tokens)
    seen = set(out)
    for t in tokens:
        for a in ALIASES.get(t, ()):
            if a not in seen:
                out.append(a)
                seen.add(a)
    return out


def jaccard(a, b) -> float:
    sa, sb = (a if isinstance(a, set) else set(a)), (b if isinstance(b, set) else set(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------- 问句与语用
def is_question(text: str) -> bool:
    """整句判定：必须带上句末标点再判（v3 只判抽取片段，问号被排除在片段外，故漏判）。"""
    t = (text or "").strip()
    if not t:
        return True
    if t[-1] in "？?":
        return True
    if any(w in t for w in WH_WORDS):
        return True
    if t[-1] in QUESTION_TAILS or t[-2:] in ("了吗", "的呢", "的吧"):
        return True
    return False


def is_hypothetical(text: str) -> bool:
    return any(w in text for w in HYPOTHETICAL) or any(w in text for w in JOKING)


def is_hearsay(text: str) -> bool:
    return any(w in text for w in HEARSAY)


def has_commitment(text: str) -> bool:
    return any(w in text for w in COMMIT_CUES)


def is_planned(text: str) -> bool:
    """是否是一个「未来安排」而非泛泛陈述（决定要不要挂 due_at）。

    只认明确的时效/意图词；不能把「会」算进来——「我周三一般有例会」里的会是名词。
    """
    if has_commitment(text):
        return True
    if "要" in text:
        return True
    return any(w in text for w in ("计划", "安排", "打算", "准备", "将"))


def has_correction_cue(text: str) -> bool:
    return any(w in text for w in CORRECTION_CUES)


# 显式回忆线索：出现这些说法时，检索要允许翻归档（"遗忘=可及性下降，不是可用性消失"）
RECALL_CUES = ("还记得", "记得吗", "你记得", "以前", "曾经", "那次", "上次", "当初",
               "最早", "刚开始", "说起过", "回忆", "老早", "第一次")
def has_recall_cue(text: str) -> bool:
    return any(w in (text or "") for w in RECALL_CUES)


def split_clauses(text: str) -> list:
    """按句读切分，保留分隔符（问句判定需要它）。返回 [(片段, 分隔符)]。"""
    out, buf = [], ""
    for ch in text or "":
        if ch in "。！？!?；;\n":
            if buf.strip():
                out.append((buf.strip(), ch))
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append((buf.strip(), ""))
    return out


# ---------------------------------------------------------------- 日期
def _at_midnight(d: _dt.date) -> float:
    return time.mktime(_dt.datetime(d.year, d.month, d.day).timetuple())


def next_weekday(now: float, wd: int, next_week: bool = True) -> float:
    today = _dt.date.fromtimestamp(now)
    delta = (wd - today.weekday()) % 7
    if next_week:
        delta = delta + 7 if delta <= 0 else delta + 7
    elif delta == 0:
        delta = 7
    return _at_midnight(today + _dt.timedelta(days=delta))


def find_date(text: str, now: float = None) -> tuple:
    """从文本中解析一个到期时间戳。返回 (ts|None, 原始词)。只取第一个命中。"""
    from . import clock
    now = now or clock.now()
    m = _TIME_RE.search(text or "")
    if not m:
        return None, None
    raw = m.group(0)
    today = _dt.date.fromtimestamp(now)

    if raw in ("今天", "今日"):
        return _at_midnight(today), raw
    if raw in ("明天", "明日"):
        return _at_midnight(today + _dt.timedelta(days=1)), raw
    if raw == "后天":
        return _at_midnight(today + _dt.timedelta(days=2)), raw
    if raw == "大后天":
        return _at_midnight(today + _dt.timedelta(days=3)), raw
    if raw == "昨天":
        return _at_midnight(today - _dt.timedelta(days=1)), raw
    if raw == "前天":
        return _at_midnight(today - _dt.timedelta(days=2)), raw

    if raw in ("周末",):
        wd = 5
        delta = (wd - today.weekday()) % 7 or 7
        return _at_midnight(today + _dt.timedelta(days=delta)), raw

    m2 = re.search(r"(下{1,2}|这|本)?周([一二三四五六日天])", raw)
    if m2:
        prefix, ch = m2.group(1), m2.group(2)
        wd = _WEEKDAY[ch]
        if prefix and prefix.startswith("下"):
            return next_weekday(now, wd, next_week=True), raw
        return next_weekday(now, wd, next_week=False), raw

    if raw in ("下周", "下下周"):
        monday = _dt.date.fromtimestamp(now) + _dt.timedelta(days=7 - _dt.date.fromtimestamp(now).weekday())
        if raw == "下下周":
            monday += _dt.timedelta(days=7)
        return _at_midnight(monday), raw

    if raw in ("下个月", "下月"):
        y, mo = today.year, today.month + 1
        if mo > 12:
            y, mo = y + 1, 1
        return _at_midnight(_dt.date(y, mo, 1)), raw
    if raw in ("月底",):
        nxt = (today.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
        return _at_midnight(nxt - _dt.timedelta(days=1)), raw
    if raw in ("年底",):
        return _at_midnight(_dt.date(today.year, 12, 31)), raw
    if raw in ("明年",):
        return _at_midnight(_dt.date(today.year + 1, 1, 1)), raw
    if raw == "今年":
        return _at_midnight(_dt.date(today.year, 12, 31)), raw

    m3 = re.match(r"(\d+)\s*天后", raw)
    if m3:
        return _at_midnight(today + _dt.timedelta(days=int(m3.group(1)))), raw
    m4 = re.match(r"(\d+)\s*周后", raw)
    if m4:
        return _at_midnight(today + _dt.timedelta(days=7 * int(m4.group(1)))), raw

    m5 = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if m5:
        y, mo, d = int(m5.group(1)), int(m5.group(2)), int(m5.group(3))
        try:
            return _at_midnight(_dt.date(y, mo, d)), raw
        except ValueError:
            return None, None
    m6 = re.match(r"(\d{1,2})月(\d{1,2})[日号]$", raw)
    if m6:
        mo, d = int(m6.group(1)), int(m6.group(2))
        y = today.year
        try:
            ts = _at_midnight(_dt.date(y, mo, d))
        except ValueError:
            return None, None
        if ts < _at_midnight(today) - 86400:      # 已过则视为明年
            try:
                ts = _at_midnight(_dt.date(y + 1, mo, d))
            except ValueError:
                return None, None
        return ts, raw
    m7 = re.match(r"(\d{1,2})月$", raw)
    if m7:
        mo = int(m7.group(1))
        y = today.year if mo >= today.month else today.year + 1
        try:
            return _at_midnight(_dt.date(y, mo, 1)), raw
        except ValueError:
            return None, None
    return None, None


# ---------------------------------------------------------------- 情绪
def sentiment(text: str):
    """返回 (label, valence, arousal) 或 None。词表法，零依赖；生产可换模型。"""
    if not text:
        return None
    pos = next((w for w in POS_WORDS if w in text), None)
    neg = next((w for w in NEG_WORDS if w in text), None)
    if not pos and not neg:
        return None
    if pos and neg:
        hit_neg = text.find(neg)
        hit_pos = text.find(pos)
        pos = pos if hit_pos >= hit_neg else None
        neg = neg if hit_neg > hit_pos else None
    label = pos or neg
    val = 0.7 if pos else -0.7
    arousal = 0.8 if any(w in text for w in _INTENSIFIERS) else 0.5
    return label, val, arousal


# ---------------------------------------------------------------- 实体
def extract_entities(text: str) -> list:
    """实体 = 可用来建图的稳定锚点：日期词 / 目的地 / 数字 / 引号词 / 命名（叫X）/ 大写拉丁词。"""
    if not text:
        return []
    out = []
    for m in _TIME_RE.finditer(text):
        out.append(m.group(0))
    for m in _PLACE.finditer(text):
        out.append(m.group(1))
    for m in _QUOTED.finditer(text):
        out.append(m.group(1))
    for m in _NAMED.finditer(text):
        cand = m.group(1)
        for tail in ("什么", "啥", "谁", "哪里"):
            cand = cand.replace(tail, "")
        if 1 <= len(cand) <= 8:
            out.append(cand)
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_+.#-]{1,20}", text):
        if w[0].isupper() or w.isupper():
            out.append(w)
    for n in _DIGITS.findall(text):
        if len(n) >= 3:              # 3 位以上才有辨识度（房间号/金额/型号）
            out.append(n)
    seen, uniq = set(), []
    for e in out:
        if e and e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


def extract_names(text: str) -> list:
    """只取「专名」：叫X / 引号内 / 大写拉丁词。用于判断一条新陈述是不是在讲已知对象。"""
    if not text:
        return []
    out = []
    for m in _NAMED.finditer(text):
        cand = m.group(1)
        for tail in ("什么", "啥", "谁", "哪里"):
            cand = cand.replace(tail, "")
        if 1 <= len(cand) <= 8:
            out.append(cand)
    for m in _QUOTED.finditer(text):
        out.append(m.group(1))
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_+.#-]{1,20}", text):
        if w[0].isupper() or w.isupper():
            out.append(w)
    return out


def strip_filler(text: str) -> str:
    """去掉口语填充词，得到更稳的 gist。"""
    out = text
    for w in ("我觉得", "我感觉", "其实", "然后", "另外", "还有", "就是", "那个",
              "嗯", "呃", "对了", "说起来"):
        out = out.replace(w, "")
    return out.strip(" ，,、") or text


_TAIL_FILLER = ("不假", "真的", "确实", "对吧", "是吧", "哈哈", "啦", "呀", "哦", "嗯",
                "啊", "呢", "吧", "了", "的")


def strip_tail(text: str) -> str:
    """去掉句尾语气/强调词，用于"复述判定"——「…，不假」和「…」是同一件事。"""
    out = (text or "").strip(" 。！？!?，,、；;")
    changed = True
    while changed:
        changed = False
        for f in _TAIL_FILLER:
            if out.endswith(f) and len(out) > len(f) + 1:
                out = out[:-len(f)].strip(" ，,、")
                changed = True
    return out
