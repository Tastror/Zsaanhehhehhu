"""
上海闲话字音查询 GUI
=====================

输入汉字序列，输出每个字对应的：
    (1) IPA
    (2) T拼（Tastror 拼音方案）
    (3) 通吴上（通用吴语拼音-上海话）

数据来源：https://www.wugniu.com （字音查詢 → 上海閒話）

工作原理
--------
wugniu.com 把 IPA / 吳拼 列渲染成 SVG 反爬，但每一行结果里的音频文件名
（如 ``zy6.mp3``、``gniq8.mp3``）正是该字在「通用吴语拼音-上海话」下的拼写，
因此只要抓下音频文件名，再按对照表转换就能得到三种写法。

多音字 / 文白读 / 异体字：wugniu 的结果页每种读音对应一个 ``<tr class="resultRow">``，
``備註`` 列会标注 "文"/"白" 或者语义搭配示例，本程序把所有读音都列出并带上備註。
"""

from __future__ import annotations

import json
import re
import sys
import tkinter as tk
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from threading import Lock, Thread
from tkinter import font as tkfont, ttk, scrolledtext


# =============================================================================
# 通吴上 <-> T拼 / IPA 对照表
# =============================================================================

# 声母：通吴上 key -> (T拼, IPA)
INITIAL_MAP: dict[str, tuple[str, str]] = {
    'p':   ('b',  'p'),
    'ph':  ('p',  'pʰ'),
    'b':   ('bh', 'b'),
    'm':   ('m',  'm'),
    'f':   ('f',  'f'),
    'v':   ('v',  'v'),
    't':   ('d',  't'),
    'th':  ('t',  'tʰ'),
    'd':   ('dh', 'd'),
    'n':   ('n',  'n'),
    'l':   ('l',  'l'),
    'ts':  ('z',  'ts'),
    'tsh': ('c',  'tsʰ'),
    's':   ('s',  's'),
    'z':   ('zs', 'z'),
    'c':   ('j',  'tɕ'),
    'ch':  ('q',  'tɕʰ'),
    'j':   ('jh', 'dʑ'),
    'sh':  ('x',  'ɕ'),
    'zh':  ('xh', 'ʑ'),
    'k':   ('g',  'k'),
    'kh':  ('k',  'kʰ'),
    'g':   ('gh', 'ɡ'),
    'ng':  ('ng', 'ŋ'),
    'h':   ('h',  'h'),
    'gh':  ("'",  'ɦ'),
    # 日/娘母腭化鼻音 /ɲ/（wugniu 用 gn-，MD 未直接列出；T拼用 n- 记，
    # 实际腭化由后接的 i-介音体现）
    'gn':  ('n',  'ɲ'),
    '':    ('',   ''),  # 零声母（影母）
}

# 介音：通吴上 -> (T拼, IPA)
MEDIAL_MAP: dict[str, tuple[str, str]] = {
    '':   ('',  ''),
    'i':  ('i', 'j'),
    'u':  ('u', 'w'),
    'iu': ('ü', 'ɥ'),
}

# 主韵母：通吴上 -> (T拼, IPA)
FINAL_MAP: dict[str, tuple[str, str]] = {
    # 单元音 / 复合元音
    'a':   ('a',  'a'),
    'o':   ('u',  'o'),
    'i':   ('i',  'i'),
    'y':   ('y',  'ɿ'),
    'u':   ('uu', 'u'),
    'iu':  ('ü',  'y'),
    'e':   ('ê',  'ɛ'),
    'au':  ('o',  'ɔ'),
    'eu':  ('eu', 'ɤ'),
    'ae':  ('oe', 'ø'),
    # 鼻尾韵
    'an':  ('an',  'ã'),
    'aon': ('aan', 'ɑ̃'),
    'on':  ('ong', 'oŋ'),
    'en':  ('en',  'ən'),
    'in':  ('in',  'ɪɲ'),
    'iuin':('üin', 'yɪɲ'),
    # 入声（MD 形式：h 结尾）
    'ah':  ('aq',  'aʔ'),
    'eh':  ('eq',  'əʔ'),
    'oh':  ('oq',  'oʔ'),
    'ih':  ('iq',  'iɪʔ'),
    'iuih':('üiq', 'yɪʔ'),
    # 自成音节
    'er':  ('er', 'əɻ'),
    'm':   ('m',  'm̩'),
    'n':   ('n',  'n̩'),
    'ng':  ('ng', 'ŋ̍'),
}

# 声调：tone digit -> (T拼 组合变音符, IPA 调值上标, IPA 调值数字串)
COMBINING_GRAVE = '\u0300'   # 声调 1 阴平：à
COMBINING_MACRON = '\u0304'  # 声调 5 阴去：ā
COMBINING_ACUTE = '\u0301'   # 声调 6 阳去 / 8 阳入：á / áq

TONE_MAP: dict[str, tuple[str, str, str]] = {
    '1': (COMBINING_GRAVE,  '⁵²', '52'),
    '5': (COMBINING_MACRON, '³⁴', '34'),
    '6': (COMBINING_ACUTE,  '²³', '23'),
    '7': ('',               '⁵',  '5'),
    '8': (COMBINING_ACUTE,  '¹²', '12'),
}

# 数字调号 → tone key
_DIGIT_TO_TONE_KEY: dict[str, str] = {v[2]: k for k, v in TONE_MAP.items()}


# =============================================================================
# 通吴上音节解析
# =============================================================================

_INITIALS_ORDERED = sorted(
    (k for k in INITIAL_MAP if k),
    key=lambda x: -len(x),
)

# 成音节（清化的用 h 前缀）
_SYLLABIC_BODIES = {'m', 'n', 'ng', 'hm', 'hn', 'hng'}


def _normalize_body(body: str) -> str:
    """把 wugniu 音频名风格（q 结尾、iun/iuh、oe）规范化到 MD 通吴上形式。"""
    # 入声 q → h
    if body.endswith('q'):
        body = body[:-1] + 'h'
    # 君 韵 iun → iuin（仅在词尾）
    if body.endswith('iun') and not body.endswith('iuin'):
        body = body[:-3] + 'iuin'
    # 决 韵 iuh → iuih
    if body.endswith('iuh') and not body.endswith('iuih'):
        body = body[:-3] + 'iuih'
    # 看 韵 /ø/：wugniu 音频名写作 oe，MD 通吴上写作 ae
    if body.endswith('oe'):
        body = body[:-2] + 'ae'
    return body


def _expand_yw(body: str) -> str:
    """按 MD 规则反向展开 y/w 简写：

    * ``gh + i + xx → y + xx``，``gh + i → yi``
    * ``w`` 同理对应 ``gh + u`` 的简写。
    """
    if body.startswith('y'):
        rest = body[1:]
        if rest.startswith('i') or rest == '':
            return 'gh' + rest if rest else 'ghi'
        return 'gh' + 'i' + rest
    if body.startswith('w'):
        rest = body[1:]
        if rest.startswith('u') or rest == '':
            return 'gh' + rest if rest else 'ghu'
        return 'gh' + 'u' + rest
    return body


def _split_initial(body: str) -> tuple[str, str]:
    """把 body 切成 (initial, rest)。"""
    if body in _SYLLABIC_BODIES:
        if body.startswith('h'):
            return 'h', body[1:]
        return '', body

    if body.startswith('gn'):
        return 'gn', body[2:]

    for ini in _INITIALS_ORDERED:
        if body.startswith(ini):
            rest = body[len(ini):]
            # 声母吃掉之后为空、而自身又是成音节韵母（n、m、ng）：视为零声母 + 成音节
            if rest == '' and ini in _SYLLABIC_BODIES:
                return '', ini
            return ini, rest
    return '', body


def _split_final(rest: str) -> tuple[str, str]:
    """把 rest（去掉声母后的部分）切成 (medial, main_final)。"""
    if rest in FINAL_MAP:
        return '', rest
    # 试 iu-介音（两字母优先于单字母）
    if rest.startswith('iu') and rest[2:] in FINAL_MAP:
        return 'iu', rest[2:]
    if rest.startswith('i') and rest[1:] in FINAL_MAP:
        return 'i', rest[1:]
    if rest.startswith('u') and rest[1:] in FINAL_MAP:
        return 'u', rest[1:]
    return '', rest


_SYLL_RE = re.compile(r'^([A-Za-z\']+?)([1-8])$')


def parse_syllable(raw: str) -> tuple[str, str, str, str, str] | None:
    """解析一个音节字符串，返回 ``(tongwushang, initial, medial, final, tone)``；
    其中 ``tongwushang`` 是规范化后的 MD 形式写法。"""
    m = _SYLL_RE.match(raw.strip())
    if not m:
        return None
    body, tone = m.group(1), m.group(2)
    body = _expand_yw(body.lower())
    body = _normalize_body(body)
    initial, rest = _split_initial(body)
    medial, final = _split_final(rest)
    if final == '' and rest == '':
        return None
    # 规范化重组成 MD 通吴上形式（应用 y/w 回写规则）
    canon = _compose_tongwushang(initial, medial, final) + tone
    return canon, initial, medial, final, tone


def _compose_tongwushang(initial: str, medial: str, final: str) -> str:
    """把 (声母, 介音, 韵母) 按 MD 写法组合成通吴上字符串（不含声调）。

    MD 简写规则（见 ``上海闲话.md``）：

    * ``gh + i + xx`` 写作 ``y + xx``；``gh + i``（单独）写作 ``yi``。
    * ``gh + iu`` 写作 ``yu``（``yu`` 即 ``ghiu`` 的简写）。
    * ``w`` 与 ``y`` 对偶，代表 ``gh + u + xx``。

    这里「i」指**介音**或合口呼里作音首的 i；若 ``i`` 是主韵母的主元音
    （如 ``in``/``ih`` 里的 ``i``），则不省略，所以 ``ghin → yin``、``ghih → yih``。
    """
    # 成音节韵母
    if initial == '' and medial == '' and final in {'m', 'n', 'ng', 'er'}:
        return final
    if initial == 'h' and final in {'m', 'n', 'ng'}:
        return 'h' + final
    # gn-（本程序内部声母符号，原样输出）
    if initial == 'gn':
        return 'gn' + medial + final

    if initial == 'gh':
        # gh + iu-系韵母（iu/iuin/iuih）：砍掉首 i，写成 yu/yuin/yuih
        if medial == '' and final.startswith('iu'):
            return 'y' + final[1:]
        # gh + i-介音：砍掉介音 i，写成 y + final
        if medial == 'i':
            return 'y' + final
        # gh + iu-介音（罕见）：= gh + u-介音 的对称写法
        if medial == 'iu':
            return 'y' + 'u' + final
        # gh + 单独 i 主元音：yi / yin / yih 等
        if medial == '' and final == 'i':
            return 'yi'
        if medial == '' and final.startswith('i'):
            return 'y' + final
        # gh + u-介音：写成 w + final；gh + u 单独：wu；gh + u-系（u-开头的韵母）：w + final[1:]
        if medial == 'u':
            return 'w' + final
        if medial == '' and final == 'u':
            return 'wu'
        if medial == '' and final.startswith('u'):
            return 'w' + final[1:]
    return initial + medial + final


# =============================================================================
# 转写：通吴上 → T拼 / IPA
# =============================================================================

def _place_tone_tpin(text: str, tone: str) -> str:
    """把 T拼 声调的组合变音符标到 text 的首字符上。"""
    mark = TONE_MAP.get(tone, ('', '', ''))[0]
    if not mark or not text:
        return text
    return text[0] + mark + text[1:]


def to_tpin(initial: str, medial: str, final: str, tone: str) -> str:
    """T拼（NFC 规范化，能预组合的字符都用 precomposed 形式）。

    例如 ``n + U+0300`` 会被合并为 ``ǹ``（U+01F9）；``ü + U+0301`` 合并为
    ``ǘ``（U+01D8）。唯一例外是 ``ê`` 加 macron 的 ``ê̄``（阴去）——这个变体
    本身没有预组合形式，只能保留 ``ê + U+0304``。
    """
    ini_t, _ = INITIAL_MAP.get(initial, (initial, ''))
    med_t, _ = MEDIAL_MAP.get(medial, (medial, ''))
    fin_t, _ = FINAL_MAP.get(final, (final, ''))
    # 声调标在韵母（不含介音）首字符上
    fin_toned = _place_tone_tpin(fin_t, tone)
    return unicodedata.normalize('NFC', ini_t + med_t + fin_toned)


def to_ipa(initial: str, medial: str, final: str, tone: str) -> str:
    """上标声调形式的 IPA（用于展示，NFC 规范化）。"""
    _, ini_i = INITIAL_MAP.get(initial, ('', initial))
    _, med_i = MEDIAL_MAP.get(medial, ('', medial))
    _, fin_i = FINAL_MAP.get(final, ('', final))
    tone_i = TONE_MAP.get(tone, ('', '', ''))[1]
    return unicodedata.normalize('NFC', ini_i + med_i + fin_i + tone_i)


def to_ipa_digit(initial: str, medial: str, final: str, tone: str) -> str:
    """数字后标声调形式的 IPA（用于本地 JSON 存储；NFC 规范化）。"""
    _, ini_i = INITIAL_MAP.get(initial, ('', initial))
    _, med_i = MEDIAL_MAP.get(medial, ('', medial))
    _, fin_i = FINAL_MAP.get(final, ('', final))
    digit = TONE_MAP.get(tone, ('', '', ''))[2]
    return unicodedata.normalize('NFC', ini_i + med_i + fin_i + digit)


# 反向索引：IPA(数字调号) → (initial, medial, final, tone)。一次性构造。
_IPA_DIGIT_INDEX: dict[str, tuple[str, str, str, str]] = {}


def _build_ipa_index() -> None:
    for ini in INITIAL_MAP:
        for med in MEDIAL_MAP:
            for fin in FINAL_MAP:
                for tone in TONE_MAP:
                    # 过滤掉声调与韵类不匹配的组合：入声只配 7/8、非入声只配 1/5/6
                    is_ru = fin in {'ah', 'eh', 'oh', 'ih', 'iuih'}
                    if is_ru and tone not in {'7', '8'}:
                        continue
                    if (not is_ru) and tone in {'7', '8'}:
                        continue
                    key = to_ipa_digit(ini, med, fin, tone)
                    # 保留首个命中（遍历顺序已让较常见的声母/韵母优先）
                    _IPA_DIGIT_INDEX.setdefault(key, (ini, med, fin, tone))


_build_ipa_index()


def ipa_digit_to_parts(ipa: str) -> tuple[str, str, str, str] | None:
    """把 ``ɦjoʔ12`` 这样的字符串反解析为 (initial, medial, final, tone)。"""
    if not ipa:
        return None
    key = unicodedata.normalize('NFC', ipa)
    return _IPA_DIGIT_INDEX.get(key)


# =============================================================================
# 抓取 wugniu.com
# =============================================================================

_ROW_RE = re.compile(
    r'<tr class="resultRow">.*?'
    r'href="/allplaces\?char=(?P<ch>[^"]+)"[^>]*>[^<]*</a>.*?'
    r'<td[^>]*>\s*(?P<note>[^<]*?)\s*</td>\s*'
    r'<td[^>]*id="audioBtn"[^>]*>\s*<audio[^>]*>\s*'
    r'<source\s+src="/sounds/shanghai/(?P<py>[a-zA-Z0-9]+)\.mp3"',
    re.DOTALL,
)


@lru_cache(maxsize=4096)
def fetch_readings(ch: str, timeout: float = 10.0) -> tuple[tuple[str, str, str], ...]:
    """向 wugniu.com 请求单字字音，返回一组 ``(返回字, 音频id, 備註)``。

    返回字可能是繁体（例如输入「话」会返回「話」），表示 wugniu 里该字的条目名。
    """
    url = (
        'https://www.wugniu.com/search?char='
        + urllib.parse.quote(ch)
        + '&table=shanghai'
    )
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'Mozilla/5.0 (ShanghaiPinyinTool/1.0)'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode('utf-8', errors='replace')
    return tuple(
        (
            urllib.parse.unquote(m.group('ch')),
            m.group('py'),
            m.group('note').strip(),
        )
        for m in _ROW_RE.finditer(html)
    )


# =============================================================================
# 本地 JSON 缓存
# =============================================================================
#
# 按用户 MD 要求：查到的结果存本地 JSON，IPA 用数字后标声调形式（如 ``ɦã23``），
# 以后优先从本地读；没有结果也写「暂空」占位，方便手动补。
#
# 文件格式（扁平）::
#
#     {
#       "字": [{"ipa": "zɿ23", "note": "", "variants": ["字"]}],
#       "行": [
#         {"ipa": "ɦã23", "note": "~開來，流行。白", "variants": ["行"]},
#         ...
#       ],
#       "查不到的字": []          # 空列表 = 暂空，请手动补
#     }
#
# 条目的 ``ipa`` 字段为 ``null`` 或空字符串 → 也算暂空；手动填好 IPA 再保存
# 文件即可，后续程序能自动反解析出 T拼 / 通吴上。

CACHE_PATH = Path(__file__).resolve().parent / 'readings.json'

_cache_lock = Lock()
_cache: dict[str, list[dict]] = {}
_cache_loaded = False


def _load_cache() -> None:
    global _cache, _cache_loaded
    with _cache_lock:
        if _cache_loaded:
            return
        if CACHE_PATH.exists():
            try:
                data = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    _cache = {k: v for k, v in data.items() if isinstance(v, list)}
            except Exception as exc:
                print(f'[warn] 读取本地缓存失败：{exc}', file=sys.stderr)
                _cache = {}
        _cache_loaded = True


def _save_cache() -> None:
    """原子写入 readings.json。"""
    with _cache_lock:
        tmp = CACHE_PATH.with_suffix('.json.tmp')
        tmp.write_text(
            json.dumps(_cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        tmp.replace(CACHE_PATH)


def _entry_from_parsed(
    variants: list[str],
    py: str,
    note: str,
    parsed: tuple[str, str, str, str, str] | None,
) -> dict:
    """把解析结果打包为 JSON 存储条目。"""
    if parsed is None:
        # 解析失败 —— 音标表未覆盖这个拼写，写 ``null`` 方便手动补 IPA
        return {
            'ipa': None,
            'note': (note + f' [解析失败: {py}]').strip(),
            'variants': variants,
        }
    _canon, ini, med, fin, tone = parsed
    return {
        'ipa': to_ipa_digit(ini, med, fin, tone),
        'note': note,
        'variants': variants,
    }


def _view_from_entry(entry: dict, ch: str) -> dict:
    """把 JSON 条目展开成 UI 显示用的 reading dict。"""
    ipa_digit = entry.get('ipa')
    note = entry.get('note') or ''
    variants = entry.get('variants') or [ch]
    if not ipa_digit:
        return {
            'variants': variants,
            'raw': '',
            'tongwushang': '（暂空）',
            'tpin': '（暂空）',
            'ipa': '（暂空）',
            'ipa_digit': None,
            'note': note,
            'placeholder': True,
        }
    parts = ipa_digit_to_parts(ipa_digit)
    if parts is None:
        # 手动填的 IPA 字符串对不上表 —— 直接原样显示 IPA
        return {
            'variants': variants,
            'raw': ipa_digit,
            'tongwushang': '?',
            'tpin': '?',
            'ipa': ipa_digit,
            'ipa_digit': ipa_digit,
            'note': note + ' [IPA 形式无法反解析]',
            'placeholder': False,
        }
    ini, med, fin, tone = parts
    canon = _compose_tongwushang(ini, med, fin) + tone
    return {
        'variants': variants,
        'raw': canon,
        'tongwushang': canon,
        'tpin': to_tpin(ini, med, fin, tone),
        'ipa': to_ipa(ini, med, fin, tone),
        'ipa_digit': ipa_digit,
        'note': note,
        'placeholder': False,
    }


def query_character(ch: str, *, force_refresh: bool = False) -> list[dict]:
    """对单个汉字返回 reading 列表。

    流程：本地 JSON 缓存优先 → 未命中时抓 wugniu → 写回 JSON。
    条目 ``'placeholder': True`` 表示「暂空」（等待手动填补）；
    条目 ``'error'`` 表示网络等错误。
    """
    _load_cache()

    if not force_refresh:
        with _cache_lock:
            cached = _cache.get(ch)
        if cached is not None:
            if not cached:
                # 空列表 = 已知查过但没结果，显示为一条占位提示
                return [{
                    'variants': [ch],
                    'raw': '',
                    'tongwushang': '（暂空）',
                    'tpin': '（暂空）',
                    'ipa': '（暂空）',
                    'ipa_digit': None,
                    'note': '本地缓存为空；可在 readings.json 中手动补 IPA',
                    'placeholder': True,
                }]
            return [_view_from_entry(e, ch) for e in cached]

    # 从 wugniu 抓
    try:
        rows = fetch_readings(ch)
    except Exception as exc:
        return [{'error': f'{type(exc).__name__}: {exc}'}]

    # 按 (音, 備註) 去重并合并异体字
    merged: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for variant, py, note in rows:
        key = (py, note)
        if key not in merged:
            merged[key] = []
            order.append(key)
        if variant not in merged[key]:
            merged[key].append(variant)

    entries: list[dict] = [
        _entry_from_parsed(merged[(py, note)], py, note, parse_syllable(py))
        for py, note in order
    ]

    # 写回缓存
    with _cache_lock:
        _cache[ch] = entries
    try:
        _save_cache()
    except Exception as exc:
        print(f'[warn] 写本地缓存失败：{exc}', file=sys.stderr)

    if not entries:
        return [{
            'variants': [ch],
            'raw': '',
            'tongwushang': '（暂空）',
            'tpin': '（暂空）',
            'ipa': '（暂空）',
            'ipa_digit': None,
            'note': 'wugniu 未返回结果；可在 readings.json 中手动补 IPA',
            'placeholder': True,
        }]
    return [_view_from_entry(e, ch) for e in entries]


# =============================================================================
# GUI
# =============================================================================

# 包括基本区 + A 扩展 + 兼容汉字 + A/B/C/D/E/F 扩展（粗略覆盖）
_CJK_RE = re.compile(
    r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
    r'\U00020000-\U0002a6df\U0002a700-\U0002ebef\U0002f800-\U0002fa1f]'
)


def is_cjk(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


# ----- 字体选择 --------------------------------------------------------------
# 按质量优先挑选已安装的字体；越靠前越优先使用。

# 适合显示 IPA（变音符号、特殊元音）的比例字体
_IPA_FONT_PREF = [
    'Charis SIL',        # SIL 专门的 IPA 字体，最好
    'Charis SIL Compact',
    'Gentium Plus',
    'Doulos SIL',
    'Noto Sans',
    'Segoe UI',          # Windows 自带，覆盖广
    'Microsoft YaHei UI',
    'Microsoft YaHei',
]

# 中文+拉丁混排的字体（用于正文 / T拼 / 通吴上 / 中文字符）
_CJK_FONT_PREF = [
    'Microsoft YaHei UI',
    'Microsoft YaHei',
    'PingFang SC',
    'Source Han Sans SC',
    'Noto Sans CJK SC',
    'SimHei',
    'Segoe UI',
]

# 等宽字体（状态栏 / 提示）
_MONO_FONT_PREF = [
    'JetBrains Mono',
    'Cascadia Code',
    'Cascadia Mono',
    'Fira Code',
    'Source Code Pro',
    'Consolas',
    'Courier New',
]


def _pick_font(preferred: list[str], fallback: str = 'TkDefaultFont') -> str:
    """在当前 Tk 环境里挑一个真实安装了的字体族名。必须在 Tk 实例化后调用。"""
    try:
        installed = {name.casefold() for name in tkfont.families()}
    except tk.TclError:
        return fallback
    for name in preferred:
        if name.casefold() in installed:
            return name
    return fallback


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title('上海闲话字音查询  ·  IPA / T拼 / 通吴上')
        root.geometry('960x720')
        try:
            root.configure(bg='#fafafa')
        except tk.TclError:
            pass

        # 字体选择（Tk 已就绪）
        self.f_cjk = _pick_font(_CJK_FONT_PREF, 'TkDefaultFont')
        self.f_ipa = _pick_font(_IPA_FONT_PREF, self.f_cjk)
        self.f_mono = _pick_font(_MONO_FONT_PREF, 'TkFixedFont')

        # 统一 ttk 样式
        style = ttk.Style()
        try:
            style.theme_use('vista' if sys.platform.startswith('win') else style.theme_use())
        except tk.TclError:
            pass
        style.configure('TButton', font=(self.f_cjk, 10), padding=(10, 4))
        style.configure('TLabel', font=(self.f_cjk, 10))
        style.configure('Status.TLabel', font=(self.f_mono, 9), foreground='#666')
        style.configure('Header.TLabel', font=(self.f_cjk, 11, 'bold'), foreground='#333')

        outer = ttk.Frame(root, padding=(16, 14, 16, 12))
        outer.pack(fill='both', expand=True)

        ttk.Label(outer, text='输入文字（可一次输入一串汉字）',
                  style='Header.TLabel').pack(anchor='w', pady=(0, 4))
        self.entry = tk.Text(
            outer, height=3, font=(self.f_cjk, 14),
            wrap='word', relief='solid', borderwidth=1,
            padx=10, pady=6,
            highlightthickness=1, highlightcolor='#4a90e2',
            highlightbackground='#ccc',
        )
        self.entry.pack(fill='x', pady=(0, 10))
        self.entry.focus_set()

        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=(0, 10))
        self.query_btn = ttk.Button(bar, text='查询  (Ctrl+Enter)', command=self.on_query)
        self.query_btn.pack(side='left')
        self.refresh_btn = ttk.Button(
            bar, text='强制刷新（忽略缓存）',
            command=lambda: self.on_query(force=True),
        )
        self.refresh_btn.pack(side='left', padx=6)
        ttk.Button(bar, text='清空结果', command=self.clear).pack(side='left', padx=6)
        ttk.Button(
            bar, text='打开缓存文件夹',
            command=self._open_cache_dir,
        ).pack(side='left', padx=6)
        self.status = ttk.Label(bar, text='就绪', style='Status.TLabel')
        self.status.pack(side='left', padx=14)

        ttk.Label(outer, text='查询结果',
                  style='Header.TLabel').pack(anchor='w', pady=(0, 4))
        self.output = scrolledtext.ScrolledText(
            outer, wrap='word', font=(self.f_cjk, 13), height=24,
            state='disabled', relief='solid', borderwidth=1,
            padx=12, pady=10, spacing1=2, spacing3=2,
            background='#ffffff',
        )
        self.output.pack(fill='both', expand=True)

        # 字体/颜色标签
        self.output.tag_configure(
            'char', font=(self.f_cjk, 22, 'bold'), foreground='#1f2d3d',
            spacing1=10, spacing3=4,
        )
        self.output.tag_configure('section', foreground='#5a6372',
                                  font=(self.f_cjk, 11, 'bold'))
        self.output.tag_configure('meta', foreground='#8a8f99',
                                  font=(self.f_cjk, 11))
        self.output.tag_configure('label', foreground='#555',
                                  font=(self.f_cjk, 12))
        self.output.tag_configure('ipa', foreground='#155fae',
                                  font=(self.f_ipa, 14))
        self.output.tag_configure('tpin', foreground='#a34700',
                                  font=(self.f_ipa, 14))
        self.output.tag_configure('twu', foreground='#2f7d32',
                                  font=(self.f_mono, 13))
        self.output.tag_configure('note', foreground='#666',
                                  font=(self.f_cjk, 11, 'italic'))
        self.output.tag_configure('error', foreground='#b00020',
                                  font=(self.f_cjk, 12, 'bold'))
        self.output.tag_configure('todo', foreground='#b37400',
                                  font=(self.f_cjk, 13, 'bold'))
        self.output.tag_configure('sep', foreground='#d5d8dd',
                                  font=(self.f_cjk, 11))

        root.bind('<Control-Return>', lambda _e: self.on_query())

        self._pool = ThreadPoolExecutor(max_workers=6)

    # -- GUI helpers --------------------------------------------------------

    def _ui(self, fn, *args, **kwargs):
        """Schedule a UI update onto the Tk main thread."""
        self.root.after(0, lambda: fn(*args, **kwargs))

    def clear(self) -> None:
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.configure(state='disabled')

    def _append(self, text: str, *tags: str) -> None:
        self.output.configure(state='normal')
        self.output.insert('end', text, tags if tags else None)
        self.output.configure(state='disabled')
        self.output.see('end')

    # -- 查询 ---------------------------------------------------------------

    def on_query(self, force: bool = False) -> None:
        text = self.entry.get('1.0', 'end').strip()
        if not text:
            return
        self.query_btn.configure(state='disabled')
        self.refresh_btn.configure(state='disabled')
        self.status.configure(
            text='强制查询中 …' if force else '查询中 …'
        )
        self.clear()
        Thread(target=self._run, args=(text, force), daemon=True).start()

    def _open_cache_dir(self) -> None:
        try:
            import os
            os.startfile(CACHE_PATH.parent)  # type: ignore[attr-defined]
        except Exception as exc:
            self._append(f'无法打开缓存目录：{exc}\n', 'error')

    def _run(self, text: str, force: bool) -> None:
        try:
            order = [ch for ch in text if is_cjk(ch)]
            unique = list(dict.fromkeys(order))
            future_map = {
                ch: self._pool.submit(query_character, ch, force_refresh=force)
                for ch in unique
            }
            results: dict[str, list[dict]] = {}
            for ch, fut in future_map.items():
                results[ch] = fut.result()
            for i, ch in enumerate(order, 1):
                self._ui(self._print_char, i, ch, results[ch])
            if not order:
                self._ui(self._append, '（未识别到汉字）\n', 'error')
            else:
                self._ui(
                    self._append,
                    f'\n本地缓存文件：{CACHE_PATH}\n'
                    f'（「暂空」条目可手动编辑该文件中的 "ipa" 字段后再查询）\n',
                    'meta',
                )
        except Exception as exc:
            self._ui(self._append, f'查询异常：{type(exc).__name__}: {exc}\n', 'error')
        finally:
            self._ui(self.status.configure, text='完成')
            self._ui(self.query_btn.configure, state='normal')
            self._ui(self.refresh_btn.configure, state='normal')

    def _print_char(self, idx: int, ch: str, readings: list[dict]) -> None:
        self._append(f'{idx}. ', 'section')
        self._append(ch, 'char')
        if not readings:
            self._append('   （未找到读音）\n\n', 'error')
            return
        if 'error' in readings[0]:
            self._append(f"   查询出错：{readings[0]['error']}\n\n", 'error')
            return
        self._append(f'   共 {len(readings)} 个读音\n', 'meta')
        for i, r in enumerate(readings, 1):
            label = f'  [{i}]'
            others = [v for v in r.get('variants', []) if v != ch]
            if others:
                label += f'（异体：{"/".join(others)}）'
            self._append(label + ' ', 'label')
            self._append('IPA ', 'label')
            if r.get('placeholder'):
                self._append('（暂空）', 'todo')
                self._append('   T拼 ', 'label')
                self._append('（暂空）', 'todo')
                self._append('   通吴上 ', 'label')
                self._append('（暂空）', 'todo')
            else:
                self._append(f'[{r["ipa"]}]', 'ipa')
                self._append('   T拼 ', 'label')
                self._append(r['tpin'], 'tpin')
                self._append('   通吴上 ', 'label')
                self._append(r['tongwushang'], 'twu')
            if r['note']:
                self._append(f'   — {r["note"]}', 'note')
            self._append('\n')
        self._append('\n')


# =============================================================================
# CLI / self-test / entry point
# =============================================================================

def _selftest() -> None:
    """快速验证几组常见字的转写结果。"""
    cases = ['字', '行', '人', '一', '月', '上', '海', '闲', '话', '二', '儿', '王', '云']
    for ch in cases:
        rs = query_character(ch)
        print(f'=== {ch} ===')
        for r in rs:
            if 'error' in r:
                print('  error:', r['error'])
                continue
            others = [v for v in r.get('variants', []) if v != ch]
            vtag = f' (异体 {"/".join(others)})' if others else ''
            if r.get('placeholder'):
                print(f'  暂空（note={r["note"]!r}）{vtag}')
                continue
            print(
                f'  通吴上 {r["tongwushang"]:<10}  T拼 {r["tpin"]:<10}  '
                f'IPA [{r["ipa"]}]{vtag}  備註 {r["note"]!r}'
            )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        _selftest()
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--clear-cache':
        if CACHE_PATH.exists():
            CACHE_PATH.unlink()
            print(f'已删除 {CACHE_PATH}')
        else:
            print(f'{CACHE_PATH} 不存在')
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
