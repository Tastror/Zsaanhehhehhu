"""
analyze.py
==========

从根目录 ``readings.json`` 统计上海话实际存在的音节组合：

  (1) (声母, 是否入声, 声调) 三元组
  (2) (介音, 韵母) 二元组
  (3) 系统性缺失的 (声母, 介音, 韵母) 三元组
      —— 若发现缺失与韵母无关，折叠成 (声母, 介音)

"只考虑系统丢失"：只有当 (声母, 介音, 韵母) 在所有声调上都没有字例
时才算缺失；某个具体音节碰巧没字不算。

本脚本原先生成了仓库里的 ``analyze/analyze.txt``。运行方式：

    python analyze/analyze.py         # 从仓库根目录跑
    python analyze.py                 # 从 analyze/ 目录跑

两种方式都应当可用。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import zsaanhehhehhu as sp  # noqa: E402


READINGS_PATH = _ROOT / 'readings.json'
DEFAULT_OUTPUT = _HERE / 'analyze.txt'


def _entry_chars(ch: str, entry: dict) -> set[str]:
    """返回一个 readings.json 条目代表的字形集合，用于去重计数。"""
    variants = entry.get('variants')
    if isinstance(variants, list):
        chars = {str(v) for v in variants if v}
        if chars:
            return chars
    return {ch}


def _parts_to_ipa(combo: tuple[str, str, str, str]) -> tuple[str, str, str, str]:
    ini, med, fin, tone = combo
    return (
        sp.INITIAL_MAP[ini][1],
        sp.MEDIAL_MAP[med][1],
        sp.FINAL_MAP[fin][1],
        tone,
    )


def _load_reading_counts() -> dict[tuple[str, str, str, str], int]:
    """从 readings.json 建立 (IPA声母, IPA介音, IPA韵母, tone) -> 字数。"""
    data = json.loads(READINGS_PATH.read_text(encoding='utf-8'))
    char_sets: dict[tuple[str, str, str, str], set[str]] = {}
    for ch, entries in data.items():
        if not isinstance(ch, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parts = sp.ipa_digit_to_parts(entry.get('ipa') or '')
            if not parts:
                continue
            char_sets.setdefault(_parts_to_ipa(parts), set()).update(_entry_chars(ch, entry))
    return {parts: len(chars) for parts, chars in char_sets.items()}


def load_parsed() -> list[tuple[str, str, str, str, str, int]]:
    """返回 (ipa, ini, med, fin, tone, count) 列表；前三个部件均为 IPA。"""
    counts = _load_reading_counts()
    canonical, _aliased = sp._enumerate_canonical_syllables()
    out: list[tuple[str, str, str, str, str, int]] = []
    for _wx, _wxie, _tp, ipa, _ipad, combo in canonical:
        ini, med, fin, tone = _parts_to_ipa(combo)
        out.append((ipa, ini, med, fin, tone, counts.get((ini, med, fin, tone), 0)))
    return out


def analyze():
    rows = load_parsed()

    # ------------------------------------------------------------------
    # (1) (声母, 是否入声, 声调) 可能三元组
    # ------------------------------------------------------------------
    ini_ru_tone: set[tuple[str, bool, str]] = set()
    # 为了对比，也记录"理论上该组合存在、但数据里 0 字例"的三元组
    all_possible_by_filter: dict[tuple[str, bool, str], int] = defaultdict(int)
    found: dict[tuple[str, bool, str], int] = defaultdict(int)

    for _wx, ini, med, fin, tone, cnt in rows:
        is_ru = tone in {'7', '8'}
        key = (ini, is_ru, tone)
        all_possible_by_filter[key] += 1
        if cnt > 0:
            ini_ru_tone.add(key)
            found[key] += 1

    # ------------------------------------------------------------------
    # (2) (介音, 韵母) 可能二元组
    # ------------------------------------------------------------------
    med_fin: set[tuple[str, str]] = set()
    for _wx, ini, med, fin, tone, cnt in rows:
        if cnt > 0:
            med_fin.add((med, fin))

    # ------------------------------------------------------------------
    # (3) 系统性缺失的 (声母, 介音, 韵母)
    # ------------------------------------------------------------------
    imf_any: dict[tuple[str, str, str], bool] = defaultdict(lambda: False)
    imf_seen_tones: dict[tuple[str, str, str], list[tuple[str, int]]] = defaultdict(list)
    for _wx, ini, med, fin, tone, cnt in rows:
        key = (ini, med, fin)
        imf_seen_tones[key].append((tone, cnt))
        if cnt > 0:
            imf_any[key] = True

    # 缺失的 (ini, med, fin)：数据里所有声调都 0 字例
    missing_imf = [
        key for key, _seen in imf_seen_tones.items()
        if not imf_any[key]
    ]

    # 聚合：对每对 (ini, med)，它的所有 fin 是否全部缺失？
    by_im_all_fins: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_im_miss_fins: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (ini, med, fin) in imf_seen_tones:
        by_im_all_fins[(ini, med)].append(fin)
    for (ini, med, fin) in missing_imf:
        by_im_miss_fins[(ini, med)].append(fin)

    im_fully_missing: list[tuple[str, str]] = []
    imf_residual_missing: list[tuple[str, str, str]] = []
    for im, fins in sorted(by_im_all_fins.items()):
        miss = by_im_miss_fins.get(im, [])
        if miss and set(miss) == set(fins):
            im_fully_missing.append(im)
        else:
            for f in miss:
                imf_residual_missing.append((im[0], im[1], f))

    return {
        'rows': rows,
        'ini_ru_tone_possible': ini_ru_tone,
        'ini_ru_tone_all_enumerated': dict(all_possible_by_filter),
        'ini_ru_tone_found_counts': dict(found),
        'med_fin_possible': med_fin,
        'missing_imf': missing_imf,
        'im_fully_missing': im_fully_missing,
        'imf_residual_missing': imf_residual_missing,
        'imf_seen_tones': dict(imf_seen_tones),
    }


# =============================================================================
# 打印辅助
# =============================================================================

_INITIAL_ORDER = [
    'p', 'pʰ', 'b', 'm', 'f', 'v',
    't', 'tʰ', 'd', 'n', 'ɲ', 'l',
    'ts', 'tsʰ', 's', 'z',
    'tɕ', 'tɕʰ', 'dʑ', 'ɕ', 'ʑ',
    'k', 'kʰ', 'ɡ', 'ŋ', 'h', 'ɦ',
    '',
]
_MEDIAL_ORDER = ['', 'j', 'w', 'ɥ']
_FINAL_ORDER = [
    'a', 'o', 'i', 'ɿ', 'u', 'y', 'ɛ', 'ɔ', 'ɤ', 'ø',
    'ã', 'ɑ̃', 'oŋ', 'ən', 'ɪɲ', 'yɪɲ',
    'aʔ', 'əʔ', 'oʔ', 'iɪʔ', 'yɪʔ',
    'əɻ', 'm̩', 'n̩', 'ŋ̍',
]
_TONE_ORDER = ['1', '5', '6', '7', '8']


def _ini_sort_key(x):
    try:
        return _INITIAL_ORDER.index(x)
    except ValueError:
        return 999


def _med_sort_key(x):
    try:
        return _MEDIAL_ORDER.index(x)
    except ValueError:
        return 999


def _fin_sort_key(x):
    try:
        return _FINAL_ORDER.index(x)
    except ValueError:
        return 999


def _label(sym: str) -> str:
    return 'Ø' if sym == '' else sym


def format_report(a, *, residual_limit: int | None = None) -> str:
    """residual_limit=None 时写全部 (声母,介音,韵母) 残留缺失；设为整数则截断。"""
    lines: list[str] = []
    lines.append('')
    lines.append('=== (1) (声母, 是否入声, 声调) 可能三元组 ===')
    lines.append(
        f'有字三元组 {len(a["ini_ru_tone_possible"])} 个 / '
        f'理论三元组 {len(a["ini_ru_tone_all_enumerated"])} 个'
    )
    all_tones_nonru = ['1', '5', '6']
    all_tones_ru = ['7', '8']

    def tone_cell(ini: str, is_ru: bool, tone: str) -> str:
        key = (ini, is_ru, tone)
        total = a['ini_ru_tone_all_enumerated'].get(key, 0)
        if total == 0:
            return '.'
        found = a['ini_ru_tone_found_counts'].get(key, 0)
        missing = total - found
        if missing == 0:
            return str(found)
        return f'{found}/{missing}'

    cell_w = max(
        5,
        max(
            len(tone_cell(ini, is_ru, tone))
            for ini, is_ru, tone in a['ini_ru_tone_all_enumerated']
        ),
    )
    header = (
        '声母     | 非入声: '
        + ' '.join(f'{t:>{cell_w}}' for t in all_tones_nonru)
        + '   入声: '
        + ' '.join(f'{t:>{cell_w}}' for t in all_tones_ru)
    )
    lines.append(header)
    lines.append('-' * len(header))
    for ini in sorted({k[0] for k in a['ini_ru_tone_all_enumerated']}, key=_ini_sort_key):
        label = _label(ini)
        cells_nonru = [f'{tone_cell(ini, False, t):>{cell_w}}' for t in all_tones_nonru]
        cells_ru = [f'{tone_cell(ini, True, t):>{cell_w}}' for t in all_tones_ru]
        lines.append(f'{label:<8} | 非入声: {" ".join(cells_nonru)}   入声: {" ".join(cells_ru)}')
    lines.append(
        '（每格 = 有字/无字的 (介音,韵母) 格数；无字为 0 时只写有字数；'
        '. = 该 (声母, 入声状态, 声调) 无理论组合）'
    )

    lines.append('')
    lines.append('=== (2) (介音, 韵母) 可能二元组 ===')
    lines.append(f'共 {len(a["med_fin_possible"])} 对')
    meds = sorted({k[0] for k in a['med_fin_possible']}, key=_med_sort_key)
    fins = sorted({k[1] for k in a['med_fin_possible']}, key=_fin_sort_key)
    header = '介音\\韵母 ' + ' '.join(f'{f:>4}' for f in fins)
    lines.append(header)
    for m in meds:
        mlabel = _label(m)
        cells = [(' ✓  ' if (m, f) in a['med_fin_possible'] else '  . ') for f in fins]
        lines.append(f'{mlabel:<9} ' + ' '.join(cells))

    lines.append('')
    lines.append('=== (3) 系统性缺失：(声母, 介音, 韵母) ===')
    lines.append(f'  完全缺失的 (声母, 介音) —— 该介音下所有韵母都无字例：{len(a["im_fully_missing"])} 对')
    for ini, med in sorted(a['im_fully_missing'], key=lambda x: (_ini_sort_key(x[0]), _med_sort_key(x[1]))):
        lines.append(f'    ({_label(ini)}, {_label(med)})')

    lines.append('')
    lines.append(f'  (声母, 介音, 韵母) 缺失（但同 (声母, 介音) 下其他韵母有字）：{len(a["imf_residual_missing"])} 条')
    shown = 0
    total = len(a['imf_residual_missing'])
    for ini, med, fin in sorted(
        a['imf_residual_missing'],
        key=lambda x: (_ini_sort_key(x[0]), _med_sort_key(x[1]), _fin_sort_key(x[2])),
    ):
        if residual_limit is not None and shown >= residual_limit:
            lines.append(f'    ... 还有 {total - shown} 条（用 --limit 0 或 -A 看全部）')
            break
        lines.append(f'    ({_label(ini)}, {_label(med)}, {fin})')
        shown += 1

    return '\n'.join(lines) + '\n'


def print_report(a, *, residual_limit: int | None = None) -> None:
    print(format_report(a, residual_limit=residual_limit), end='')


def main(argv: list[str] | None = None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else '')
    p.add_argument(
        '-o', '--output', type=Path, default=DEFAULT_OUTPUT,
        help=f'输出文件路径（默认 {DEFAULT_OUTPUT}）。',
    )
    p.add_argument(
        '--limit', type=int, default=0,
        help='第 (3) 段 residual 列表最多写多少条；0 表示全部（默认）。',
    )
    args = p.parse_args(argv)

    a = analyze()
    text = format_report(a, residual_limit=None if args.limit == 0 else args.limit)
    args.output.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
