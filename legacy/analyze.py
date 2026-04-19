"""
analyze.py
==========

读 ``syllable_coverage.json``（同目录），按三种维度统计上海话实际存在的音节组合：

  (1) (声母, 是否入声, 声调) 三元组
  (2) (介音, 韵母) 二元组
  (3) 系统性缺失的 (声母, 介音, 韵母) 三元组
      —— 若发现缺失与韵母无关，折叠成 (声母, 介音)

"只考虑系统丢失"：只有当 (声母, 介音, 韵母) 在所有声调上都没有字例
时才算缺失；某个具体音节碰巧没字不算。

本脚本原先生成了仓库里的 ``legacy/analyze.txt``。之后被挪进 ``legacy/``
作存档用。运行方式：

    python legacy/analyze.py          # 从仓库根目录跑
    python analyze.py                 # 从 legacy/ 目录跑

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

import shanghai_pinyin as sp  # noqa: E402


COVERAGE_PATH = _HERE / 'syllable_coverage.json'


def load_parsed() -> list[tuple[str, str, str, str, str, int]]:
    """返回 (wx, ini, med, fin, tone, count) 列表。错误/未查询记为 count=-1。"""
    data = json.loads(COVERAGE_PATH.read_text(encoding='utf-8'))
    out = []
    for wx, info in data.items():
        p = sp.parse_syllable(wx)
        if not p:
            continue
        _canon, ini, med, fin, tone = p
        if not isinstance(info, dict) or 'error' in info:
            count = -1
        else:
            count = info.get('count', 0)
        out.append((wx, ini, med, fin, tone, count))
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
    'p', 'ph', 'b', 'm', 'f', 'v',
    't', 'th', 'd', 'n', 'l', 'gn',
    'ts', 'tsh', 's', 'z',
    'c', 'ch', 'j', 'sh', 'zh',
    'k', 'kh', 'g', 'ng', 'h', 'gh',
    '',
]
_MEDIAL_ORDER = ['', 'i', 'u', 'iu']
_FINAL_ORDER = [
    'a', 'o', 'i', 'y', 'u', 'iu', 'e', 'au', 'eu', 'oe',
    'an', 'aon', 'on', 'en', 'in', 'iun',
    'aq', 'eq', 'oq', 'iq', 'iuq',
    'er', 'm', 'n', 'ng',
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


def print_report(a, *, residual_limit: int | None = None):
    """residual_limit=None 时打印全部 (声母,介音,韵母) 残留缺失；设为整数则截断。"""
    print(f'\n=== (1) (声母, 是否入声, 声调) 可能三元组 ===')
    print(f'共 {len(a["ini_ru_tone_possible"])} 个')
    all_tones_nonru = ['1', '5', '6']
    all_tones_ru = ['7', '8']
    header = '声母     | 非入声: ' + ' '.join(f'{t:>3}' for t in all_tones_nonru) + '   入声: ' + ' '.join(f'{t:>3}' for t in all_tones_ru)
    print(header)
    print('-' * len(header))
    for ini in sorted({k[0] for k in a['ini_ru_tone_possible']}, key=_ini_sort_key):
        label = repr(ini) if ini == '' else ini
        cells_nonru = []
        for t in all_tones_nonru:
            if (ini, False, t) in a['ini_ru_tone_possible']:
                cnt = a['ini_ru_tone_found_counts'].get((ini, False, t), 0)
                cells_nonru.append(f' ✓{cnt:>2}'[:3])
            else:
                cells_nonru.append('  .')
        cells_ru = []
        for t in all_tones_ru:
            if (ini, True, t) in a['ini_ru_tone_possible']:
                cnt = a['ini_ru_tone_found_counts'].get((ini, True, t), 0)
                cells_ru.append(f' ✓{cnt:>2}'[:3])
            else:
                cells_ru.append('  .')
        print(f'{label:<8} | 非入声: {" ".join(cells_nonru)}   入声: {" ".join(cells_ru)}')
    print('（数字 = 该 (声母, 入声状态, 声调) 下至少 1 字的 (介音,韵母) 格数；. 表示 0）')

    print(f'\n=== (2) (介音, 韵母) 可能二元组 ===')
    print(f'共 {len(a["med_fin_possible"])} 对')
    meds = sorted({k[0] for k in a['med_fin_possible']}, key=_med_sort_key)
    fins = sorted({k[1] for k in a['med_fin_possible']}, key=_fin_sort_key)
    header = '介音\\韵母 ' + ' '.join(f'{f:>4}' for f in fins)
    print(header)
    for m in meds:
        mlabel = repr(m) if m == '' else m
        cells = [(' ✓  ' if (m, f) in a['med_fin_possible'] else '  . ') for f in fins]
        print(f'{mlabel:<9} ' + ' '.join(cells))

    print(f'\n=== (3) 系统性缺失：(声母, 介音, 韵母) ===')
    print(f'  完全缺失的 (声母, 介音) —— 该介音下所有韵母都无字例：{len(a["im_fully_missing"])} 对')
    for ini, med in sorted(a['im_fully_missing'], key=lambda x: (_ini_sort_key(x[0]), _med_sort_key(x[1]))):
        print(f'    ({repr(ini) if ini == "" else ini}, {repr(med) if med == "" else med})')

    print(f'\n  (声母, 介音, 韵母) 缺失（但同 (声母, 介音) 下其他韵母有字）：{len(a["imf_residual_missing"])} 条')
    shown = 0
    total = len(a['imf_residual_missing'])
    for ini, med, fin in sorted(
        a['imf_residual_missing'],
        key=lambda x: (_ini_sort_key(x[0]), _med_sort_key(x[1]), _fin_sort_key(x[2])),
    ):
        if residual_limit is not None and shown >= residual_limit:
            print(f'    ... 还有 {total - shown} 条（用 --limit 0 或 -A 看全部）')
            break
        print(f'    ({repr(ini) if ini == "" else ini}, {repr(med) if med == "" else med}, {fin})')
        shown += 1


def main(argv: list[str] | None = None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else '')
    p.add_argument(
        '--limit', type=int, default=0,
        help='第 (3) 段 residual 列表最多打印多少条；0 表示全部（默认）。',
    )
    args = p.parse_args(argv)

    a = analyze()
    print_report(a, residual_limit=None if args.limit == 0 else args.limit)


if __name__ == '__main__':
    main()
