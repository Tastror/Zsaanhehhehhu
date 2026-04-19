"""
上海闲话字音查询 GUI
=====================

输入汉字序列，输出每个字对应的：
    (1) IPA
    (2) T拼（Tastror 拼音方案）
    (3) 吴学（吴语学堂拼音方案）
    (4) 吴协（吴语协会拼音方案）

数据来源：https://www.wugniu.com （吴学字音查詢）

工作原理
--------
wugniu.com 把 IPA / 吳拼（吴学） 列渲染成 SVG 反爬，但每一行结果里的音频文件名
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
# 吴学 <-> T拼 / IPA 对照表
# =============================================================================

# 声母：吴学 key -> (T拼, IPA)
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
    # 日/娘母腭化鼻音 /ɲ/（wugniu 用 gn-，MD 未直接列出；T拼用 n- 记，
    # 实际腭化由后接的 i-介音体现）
    'gn':  ('n',  'ɲ'),
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
    '':    ('',   ''),  # 零声母（影母）
}

# 介音：吴学 -> (T拼, IPA)
MEDIAL_MAP: dict[str, tuple[str, str]] = {
    '':   ('',  ''),
    'i':  ('i', 'j'),
    'u':  ('u', 'w'),
    'iu': ('ü', 'ɥ'),
}

# 主韵母：吴学 -> (T拼, IPA)
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
    'oe':  ('oe', 'ø'),
    # 鼻尾韵
    'an':  ('an',  'ã'),
    'aon': ('aan', 'ɑ̃'),
    'on':  ('ong', 'oŋ'),
    'en':  ('en',  'ən'),
    'in':  ('in',  'ɪɲ'),
    'iun': ('üin', 'yɪɲ'),
    # 入声
    'aq':  ('aq',  'aʔ'),
    'eq':  ('eq',  'əʔ'),
    'oq':  ('oq',  'oʔ'),
    'iq':  ('iq',  'iɪʔ'),
    'iuq': ('üiq', 'yɪʔ'),
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
# 吴学音节解析
# =============================================================================

_INITIALS_ORDERED = sorted(
    (k for k in INITIAL_MAP if k),
    key=lambda x: -len(x),
)

# 成音节（清化的用 h 前缀）
_SYLLABIC_BODIES = {'m', 'n', 'ng', 'hm', 'hn', 'hng'}


def _normalize_body(body: str) -> str:
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
    """解析一个音节字符串，返回 ``(wxue, initial, medial, final, tone)``；
    其中 ``wxue`` 是规范化后的 MD 形式写法。"""
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
    # 规范化重组成 MD 吴学形式（应用 y/w 回写规则）
    canon = _compose_wxue(initial, medial, final) + tone
    return canon, initial, medial, final, tone


def _compose_wxue(initial: str, medial: str, final: str) -> str:
    """把 (声母, 介音, 韵母) 按 MD 写法组合成吴学字符串（不含声调）。

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
# 转写：吴学 → T拼 / IPA
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


# =============================================================================
# 吴协（吴语协会拼音方案）转写
# =============================================================================
#
# 吴协 与 吴学 / 本程序内部规范形式的差异（见 ``上海闲话.md``）：
#
# * 声母：娘母 ``gn`` 写作 ``ny``；其余与吴学一致。
# * 韵母差异（内部规范形式用吴学写法，此处映射到吴协写法）：
#       aq → ah、eq → eh、oq → oh、iq → ih、iuq → iuih
#       iun → iuin、oe → ae、er → r
# * y/w 简写规则同吴学。
# * 声调使用汉字后标 平/上/去/入（正字法滞古，阴阳由声母清浊区分）：
#       阴平 / 阳平 → 平（调值 52 / 23）
#       阴上去 → 上/去
#       阳平上去 → 平/上/去
#       阴入 / 阳入 → 入
#   由于本程序已把阴上/阴去合并为 5、阳平/上/去 合并为 6，
#   无法恢复 平/上/去 区分，所以 5、6 直接标出歧义（"上/去" / "平/上/去"）。

# 声调：吴学 tone key -> 吴协 后标（汉字）
_TONE_DIGIT_WUXIE: dict[str, str] = {
    '1': '平',
    '5': '上/去',
    '6': '平/上/去',
    '7': '入',
    '8': '入',
}

# 内部（吴学）韵母 -> 吴协 韵母
_FINAL_WUXIE: dict[str, str] = {
    'aq':  'ah',
    'eq':  'eh',
    'oq':  'oh',
    'iq':  'ih',
    'iuq': 'iuih',
    'iun': 'iuin',
    'oe':  'ae',
    'er':  'r',
}


def _compose_wuxie(initial: str, medial: str, final: str) -> str:
    """把 (声母, 介音, 韵母) 按吴协写法组合成拼写字符串（不含声调）。"""
    # 把内部（吴学）韵母翻译到吴协写法
    fin = _FINAL_WUXIE.get(final, final)

    # 成音节韵母
    if initial == '' and medial == '' and fin in {'m', 'n', 'ng', 'r'}:
        return fin
    if initial == 'h' and fin in {'m', 'n', 'ng'}:
        return 'h' + fin
    if initial == 'gn':
        return 'ny' + medial + fin

    if initial == 'gh':
        # 同吴学 y/w 简写规则
        if medial == '' and fin.startswith('iu'):
            return 'y' + fin[1:]
        if medial == 'i':
            return 'y' + fin
        if medial == 'iu':
            return 'y' + 'u' + fin
        if medial == '' and fin == 'i':
            return 'yi'
        if medial == '' and fin.startswith('i'):
            return 'y' + fin
        if medial == 'u':
            return 'w' + fin
        if medial == '' and fin == 'u':
            return 'wu'
        if medial == '' and fin.startswith('u'):
            return 'w' + fin[1:]
    return initial + medial + fin


def to_wuxie(initial: str, medial: str, final: str, tone: str) -> str:
    """吴协（吴语协会拼音）字符串。"""
    body = _compose_wuxie(initial, medial, final)
    return body + _TONE_DIGIT_WUXIE.get(tone, tone)


# 反向索引：IPA(数字调号) → (initial, medial, final, tone)。一次性构造。
_IPA_DIGIT_INDEX: dict[str, tuple[str, str, str, str]] = {}


def _build_ipa_index() -> None:
    for ini in INITIAL_MAP:
        for med in MEDIAL_MAP:
            for fin in FINAL_MAP:
                for tone in TONE_MAP:
                    # 过滤掉声调与韵类不匹配的组合：入声只配 7/8、非入声只配 1/5/6
                    is_ru = fin in {'aq', 'eq', 'oq', 'iq', 'iuq'}
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
# 文件即可，后续程序能自动反解析出 T拼 / 吴学。

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
            'wxue': '（暂空）',
            'wuxie': '（暂空）',
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
            'wxue': '?',
            'wuxie': '?',
            'tpin': '?',
            'ipa': ipa_digit,
            'ipa_digit': ipa_digit,
            'note': note + ' [IPA 形式无法反解析]',
            'placeholder': False,
        }
    ini, med, fin, tone = parts
    canon = _compose_wxue(ini, med, fin) + tone
    return {
        'variants': variants,
        'raw': canon,
        'wxue': canon,
        'wuxie': to_wuxie(ini, med, fin, tone),
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
                    'wxue': '（暂空）',
                    'wuxie': '（暂空）',
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
            'wxue': '（暂空）',
            'wuxie': '（暂空）',
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

# 适合显示 IPA / T拼（变音符号、特殊元音、ê + 组合调号 ê̄ 之类）的比例字体。
# 排序原则：专业 IPA/语言学字体 > 通用高质量 Latin 字体（带组合音标）> Windows 自带兜底。
_IPA_FONT_PREF = [
    # SIL 专业 IPA / 语言学字体（免费，推荐安装；对组合音标最友好）
    'Charis SIL',
    'Charis SIL Compact',
    'Gentium Plus',
    'Gentium Book Plus',
    'Doulos SIL',
    # Windows 自带：优先使用 Times New Roman（按用户偏好）
    'Times New Roman',
    # 其他带良好组合音标支持的开源字体
    'DejaVu Serif',
    'DejaVu Sans',
    'Noto Serif',
    'Noto Sans',
    # Windows 自带里 Latin + 变音符号排版质量较高的字体
    'Cambria',
    'Sitka Text',
    'Constantia',
    'Palatino Linotype',
    'Segoe UI Historic', # 比 Segoe UI 多一些组合变音符号覆盖
    'Segoe UI',          # 最终兜底
    'Microsoft YaHei UI',
    'Microsoft YaHei',
]

# 中文+拉丁混排的字体（用于正文 / T拼 / 吴学 / 中文字符）
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
        root.title('上海闲话字音查询  ·  IPA / T拼 / 吴学 / 吴协')
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
            bar, text='强制查询（忽略缓存）',
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
        self.output.tag_configure('wxue', foreground='#2f7d32',
                                  font=(self.f_ipa, 14))
        self.output.tag_configure('wxie', foreground='#6a3fa0',
                                  font=(self.f_ipa, 14))
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
                self._append('   吴学 ', 'label')
                self._append('（暂空）', 'todo')
                self._append('   吴协 ', 'label')
                self._append('（暂空）', 'todo')
            else:
                self._append(f'[{r["ipa"]}]', 'ipa')
                self._append('   T拼 ', 'label')
                self._append(r['tpin'], 'tpin')
                self._append('   吴学 ', 'label')
                self._append(r['wxue'], 'wxue')
                self._append('   吴协 ', 'label')
                self._append(r['wuxie'], 'wxie')
            if r['note']:
                self._append(f'   — {r["note"]}', 'note')
            self._append('\n')
        self._append('\n')


# =============================================================================
# CLI / self-test / entry point
# =============================================================================

# 音系声母分类（用于 _is_sensible_combo 和 _is_tone_compatible_initial）
# ----------------------------------------------------------------------

# 清声母（全清 + 次清）：吴语里历史上带阴调（1/5/7）
_CLEAR_INITIALS: frozenset[str] = frozenset({
    'p', 'ph', 't', 'th', 'ts', 'tsh',
    'c', 'ch', 'k', 'kh',
    'f', 's', 'sh', 'h',
})
# 全浊：带阳调（6/8）
_VOICED_INITIALS: frozenset[str] = frozenset({
    'b', 'd', 'z', 'j', 'g', 'v', 'zh', 'gh',
})
# 次浊（鼻音/边音/腭化鼻音）与零声母：阴阳皆可
# _SONORANT_INITIALS = {'m', 'n', 'ng', 'l', 'gn'}

# 腭音声母（发音部位本身腭化 /tɕ tɕʰ dʑ ɕ ʑ ɲ/）
_PALATAL_INITIALS: frozenset[str] = frozenset({'c', 'ch', 'j', 'sh', 'zh', 'gn'})

# 齿音浊清塞擦/擦音（/ts tsʰ s z/）——舌尖元音 y /ɿ/ 只跟在这 4 个声母后
_DENTAL_SIBILANTS: frozenset[str] = frozenset({'ts', 'tsh', 's', 'z'})

# i-介音被腭化"吸走"的声母（这些声母 + i 在现代上海话里走向腭音声母，
# 所以 (ini, i, fin) 组合直接不出现）
_I_MED_BLOCKING_INITIALS: frozenset[str] = frozenset({
    'f', 'n',                   # 非母 / 泥母被 i 拉向 v ~ gn
    'ts', 'tsh', 's', 'z',      # 精组 + i → c/ch/sh/j
    'k', 'kh', 'g',             # 见组 + i → c/ch/j
    'ng', 'h',                  # 疑母/晓母 + i → gn/sh
})
# u-介音只允许出现在这一小组声母后（舌根 + 喉音 + 零声母）
_U_MED_ALLOWED_INITIALS: frozenset[str] = frozenset({'k', 'kh', 'g', 'h', 'gh', ''})

# i-介音可搭配的韵母（基于 wugniu 语料归纳）
_I_MED_COMPATIBLE_FINALS: frozenset[str] = frozenset({
    'a', 'e', 'au', 'eu', 'oe', 'an', 'aon', 'on', 'aq', 'oq',
})
# u-介音可搭配的韵母
_U_MED_COMPATIBLE_FINALS: frozenset[str] = frozenset({
    'a', 'e', 'oe', 'an', 'aon', 'en', 'aq', 'eq',
})

# i-起始韵母（腭音声母 + 空介音时必须走这一组；同时也是"撮口"韵）
_I_STARTING_FINALS: frozenset[str] = frozenset({
    'i', 'in', 'iq', 'iu', 'iun', 'iuq',
})
# 撮口韵（/y yn yɁ/）——唇音 + 空介音不配 撮口
_CLOSED_FRONT_ROUND_FINALS: frozenset[str] = frozenset({'iu', 'iun', 'iuq'})
_LABIAL_INITIALS: frozenset[str] = frozenset({'p', 'ph', 'b', 'm', 'f', 'v'})


def _is_sensible_combo(ini: str, med: str, fin: str) -> bool:
    """过滤掉音系上不合理的声母/介音/韵母组合。

    规则依据详见 ``上海闲话.md`` 末尾「音系结构（基于 wugniu 数据归纳）」。
    """
    if ini == '' and med == '' and fin == '':
        return False
    # 成音节 m/n/ng：只允许空声母或 h（清化）；不能带介音
    if fin in {'m', 'n', 'ng'}:
        if med != '' or ini not in {'', 'h'}:
            return False
    # 成音节 er：不能带介音；允许零声母或 gh（而/兒 文读 gher）
    if fin == 'er':
        if med != '' or ini not in {'', 'gh'}:
            return False

    # ------------------------------------------------------------------
    # 介音层面
    # ------------------------------------------------------------------
    # iu 介音在上海话里从不出现（撮口一律作韵母）
    if med == 'iu':
        return False
    # 齿化/见组+i 被腭化吸走，这些声母不带 i 介音
    if med == 'i' and ini in _I_MED_BLOCKING_INITIALS:
        return False
    # u 介音（合口）只在 见/晓/零声母 后出现
    if med == 'u' and ini not in _U_MED_ALLOWED_INITIALS:
        return False

    # ------------------------------------------------------------------
    # 介音-韵母 兼容
    # ------------------------------------------------------------------
    if med == 'i' and fin not in _I_MED_COMPATIBLE_FINALS:
        return False
    if med == 'u' and fin not in _U_MED_COMPATIBLE_FINALS:
        return False

    # ------------------------------------------------------------------
    # 声母-介音-韵母 的其他系统约束
    # ------------------------------------------------------------------
    # 腭音声母 + 空介音 → 必须接 i-起始韵母（否则腭音无从实现）
    if ini in _PALATAL_INITIALS and med == '' and fin not in _I_STARTING_FINALS:
        return False
    # 舌尖元音 y /ɿ/ 只能跟在齿音 {ts, tsh, s, z} 后
    if fin == 'y' and ini not in _DENTAL_SIBILANTS:
        return False
    # 唇音 + 空介音 不配 撮口韵 /y yn yɁ/（类似 labial-round 异化）
    if ini in _LABIAL_INITIALS and med == '' and fin in _CLOSED_FRONT_ROUND_FINALS:
        return False

    return True


def _is_tone_compatible_initial(ini: str, tone: str) -> bool:
    """阴阳调-声母 制约：清声母只配阴调、全浊只配阳调。

    次浊（m/n/ng/l/gn）与零声母不受限制。
    """
    if tone in {'1', '5', '7'} and ini in _VOICED_INITIALS:
        return False
    if tone in {'6', '8'} and ini in _CLEAR_INITIALS:
        return False
    return True


# 枚举结果一条：(吴学, 吴协, T拼, IPA, IPA数字调号, (ini, med, fin, tone))
_SyllableRow = tuple[str, str, str, str, str, tuple[str, str, str, str]]


def _enumerate_canonical_syllables() -> tuple[
    list[_SyllableRow],
    list[tuple[tuple[str, str, str, str], str, object]],
]:
    """遍历 (声母, 介音, 韵母, 声调) 笛卡尔积，返回：

      * ``canonical``：表层合法音节列表（吴学写法能被 parse 完整还原为
        原组合），按 吴学 字典序排序；
      * ``aliased``：非表层（同形但音系不成立）组合列表，每项是
        ``(原组合, 吴学写法, parse 结果)``。
    """
    ru_finals = {'aq', 'eq', 'oq', 'iq', 'iuq'}
    canonical: list[_SyllableRow] = []
    aliased: list[tuple[tuple[str, str, str, str], str, object]] = []

    for ini in INITIAL_MAP:
        for med in MEDIAL_MAP:
            for fin in FINAL_MAP:
                if not _is_sensible_combo(ini, med, fin):
                    continue
                for tone in TONE_MAP:
                    is_ru = fin in ru_finals
                    if is_ru and tone not in {'7', '8'}:
                        continue
                    if (not is_ru) and tone in {'7', '8'}:
                        continue
                    if not _is_tone_compatible_initial(ini, tone):
                        continue

                    wx = _compose_wxue(ini, med, fin) + tone
                    ps = parse_syllable(wx)
                    if not (ps and ps[1:] == (ini, med, fin, tone)):
                        aliased.append(((ini, med, fin, tone), wx, ps))
                        continue

                    wxie = to_wuxie(ini, med, fin, tone)
                    tp = to_tpin(ini, med, fin, tone)
                    ipa = to_ipa(ini, med, fin, tone)
                    ipad = to_ipa_digit(ini, med, fin, tone)
                    canonical.append((wx, wxie, tp, ipa, ipad, (ini, med, fin, tone)))

    canonical.sort(key=lambda r: r[0])
    return canonical, aliased


def _testphonology(verbose: bool = True) -> None:
    """枚举所有合法 (声母, 介音, 韵母, 声调) 组合，检查两类双向反解析：

      (A) 吴学字符串 ↔ 内部解码：``_compose_wxue(ini,med,fin)+tone``
          → ``parse_syllable`` 能完全还原为同一组 (ini,med,fin,tone)。
      (B) IPA 数字调号 ↔ 内部解码：``to_ipa_digit`` → ``ipa_digit_to_parts``
          也能完全还原。

    注：吴学写法本身存在一些表层歧义（例如 ``piu`` 既可解作 p+''+iu，
    也可解作 p+i+u，但后者在上海话里音系上不成立），这些「非表层」组合
    单独分类报告，不算转写 bug。
    """
    canonical, aliased = _enumerate_canonical_syllables()
    fail_ipa: list[tuple[str, str, object]] = []

    rows_out = []
    for wx, wxie, tp, ipa, ipad, combo in canonical:
        parts = ipa_digit_to_parts(ipad)
        ok = parts == combo
        if not ok:
            fail_ipa.append((wx, ipad, parts))
        rows_out.append((wx, wxie, tp, ipa, ok))

    if verbose:
        print(f'{"吴学":<12}{"吴协":<20}{"T拼":<14}IPA')
        print('-' * 72)
        for wx, wxie, tp, ipa, ok_ipa in rows_out:
            mark = '' if ok_ipa else '  ✗IPA'
            print(f'{wx:<12}{wxie:<20}{tp:<14}[{ipa}]{mark}')
        print()

    total = len(rows_out)
    print(f'枚举到 {total} 个表层合法音节；另有 {len(aliased)} 个非表层（同形）组合。')
    print(f'  吴学 → 内部 双向反解析：通过 {total}/{total}')
    print(f'  IPA  → 内部 双向反解析：通过 {total - len(fail_ipa)}/{total}')

    if fail_ipa:
        print(f'\nIPA 反解析失败 {len(fail_ipa)} 条（显示前 30）：')
        for wx, ipad, parts in fail_ipa[:30]:
            print(f'  {wx!r:<14} IPA={ipad!r:<14} → {parts}')

    if aliased and verbose:
        print(f'\n非表层（同形）组合示例（共 {len(aliased)} 条，显示前 10）：')
        for combo, wx, parsed in aliased[:10]:
            print(f'  {combo} 写作 {wx!r}，但 parse 为 {parsed}')

    if not fail_ipa:
        print('\n全部通过 ✓')


def _testhanzi(verbose: bool = True) -> None:
    """枚举所有合法音节，在本地 ``readings.json`` 中给每一个音节配一个示例汉字。

    如果某个音节在本地缓存里没有任何对应字，标「（无）」——提示该音节
    虽然音系合法，但现有数据里尚无字例；可以去 wugniu 补充。
    """
    _load_cache()

    # 建立 IPA 数字调号 → 汉字示例 的反查表
    ipa_to_chars: dict[str, list[tuple[str, str]]] = {}
    for ch, entries in _cache.items():
        for e in entries:
            ipa = e.get('ipa')
            if not ipa:
                continue
            note = e.get('note') or ''
            ipa_to_chars.setdefault(ipa, []).append((ch, note))

    canonical, _aliased = _enumerate_canonical_syllables()

    found = 0
    missing_syllables: list[str] = []

    if verbose:
        print(f'{"吴学":<12}{"吴协":<20}{"T拼":<14}{"IPA":<14}示例字')
        print('-' * 90)

    for wx, wxie, tp, ipa, ipad, _combo in canonical:
        chars = ipa_to_chars.get(ipad, [])
        if chars:
            found += 1
            ch, note = chars[0]
            sample = ch
            if note:
                sample += f' ({note})'
            if len(chars) > 1:
                sample += f'  +{len(chars) - 1}'
        else:
            sample = '（无）'
            missing_syllables.append(wx)
        if verbose:
            print(f'{wx:<12}{wxie:<20}{tp:<14}[{ipa}]'.ljust(74) + f'  {sample}')

    total = len(canonical)
    print(f'\n共 {total} 个表层音节，{found} 有本地字例 / {total - found} 无字例')


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == '--testphonology':
        _testphonology()
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--testhanzi':
        _testhanzi()
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
