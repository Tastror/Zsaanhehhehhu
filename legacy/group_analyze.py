"""
group_analyze.py
================

按 "声母自然类" 聚合分析 ``syllable_coverage.json``。

用户给定的自然类（每组内部规则应完全一致）：

    G1 = {p, ph, b}          唇塞
    G2 = {m}                 唇鼻
    G3 = {f, v}              唇齿擦
    G4 = {t, th, d}          齿塞
    G5 = {n, gn}             齿鼻 + 齿腭鼻（互补）
    G6 = {l}                 边
    G7 = {ts, tsh, s, z}     齿擦/齿塞擦
    G8 = {c, ch, j, sh, zh}  腭塞擦/腭擦
    G9 = {k, kh, g, h}       软腭塞 + 晓
    G10 = {ng}               疑母
    G11 = {gh}               浊喉擦（匣）
    G12 = {Ø}                零声母

对每个 (group, med, fin) 三元组：
- 若组内 **至少一个** 声母数据里有字 —— 记为 A (accidental-OK)：
  整组按"规则合法"；组内其他无字的声母算"偶然缺失"。
- 若组内 **所有** 声母都无字 —— 记为 X (systematic-gap)：
  整组按"规则不合法"。

然后把每个 (med, fin) 列：组是 A 还是 X 打成网格，人眼找规则。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import zsaanhehhehhu as sp  # noqa: E402


COVERAGE_PATH = _HERE / 'syllable_coverage.json'
OUT_PATH = _HERE / 'group_grid.txt'


# 用户给定的自然类
GROUPS: list[tuple[str, list[str]]] = [
    ('G1  p/ph/b      ', ['p', 'ph', 'b']),
    ('G2  m           ', ['m']),
    ('G3  f/v         ', ['f', 'v']),
    ('G4  t/th/d      ', ['t', 'th', 'd']),
    ('G5  n/gn        ', ['n', 'gn']),
    ('G6  l           ', ['l']),
    ('G7  ts/tsh/s/z  ', ['ts', 'tsh', 's', 'z']),
    ('G8  c/ch/j/sh/zh', ['c', 'ch', 'j', 'sh', 'zh']),
    ('G9  k/kh/g/h    ', ['k', 'kh', 'g', 'h']),
    ('G10 ng          ', ['ng']),
    ('G11 gh          ', ['gh']),
    ('G12 Ø           ', ['']),
]

MEDIAL_ORDER = ['', 'i', 'u']
FINAL_ORDER = [
    'a', 'o', 'i', 'y', 'u', 'iu',
    'e', 'au', 'eu', 'oe',
    'an', 'aon', 'on', 'en', 'in', 'iun',
    'aq', 'eq', 'oq', 'iq', 'iuq',
    'er', 'm', 'n', 'ng',
]


def load_has_char() -> dict[tuple[str, str, str], bool]:
    """(ini, med, fin) -> True iff 数据里该组合至少有一个字例。"""
    data = json.loads(COVERAGE_PATH.read_text(encoding='utf-8'))
    out: dict[tuple[str, str, str], bool] = {}
    for wx, info in data.items():
        if not isinstance(info, dict) or 'error' in info:
            continue
        if info.get('count', 0) <= 0:
            continue
        p = sp.parse_syllable(wx)
        if not p:
            continue
        _canon, ini, med, fin, _tone = p
        out[(ini, med, fin)] = True
    return out


def main():
    has_char = load_has_char()

    lines: list[str] = []
    lines.append('组级网格：每格 A=组内至少1个声母有字；X=组内全无字；')
    lines.append('  每组后面括号里写"有/无字"的声母分布（有字的加方括号）。')
    lines.append('')

    # 表头
    fin_hdr = '      ' + '  '.join(f'{f:>4}' for f in FINAL_ORDER)
    for med in MEDIAL_ORDER:
        med_label = 'Ø' if med == '' else med
        lines.append('')
        lines.append(f'===== 介音 = {med_label} =====')
        lines.append(fin_hdr)
        for gname, gmembers in GROUPS:
            row = [gname]
            for fin in FINAL_ORDER:
                any_has = any(has_char.get((i, med, fin), False) for i in gmembers)
                cell = ' A  ' if any_has else ' X  '
                row.append(cell)
            lines.append(' '.join(row))

    # 系统性缺失一览 (X 格)
    lines.append('')
    lines.append('')
    lines.append('===== 系统性缺失：组全无字 (X 格) =====')
    for med in MEDIAL_ORDER:
        med_label = 'Ø' if med == '' else med
        x_rows: list[str] = []
        for gname, gmembers in GROUPS:
            x_fins = []
            for fin in FINAL_ORDER:
                any_has = any(has_char.get((i, med, fin), False) for i in gmembers)
                if not any_has:
                    x_fins.append(fin)
            if x_fins:
                x_rows.append(f'  {gname}  med={med_label}  X-fins: {", ".join(x_fins)}')
        if x_rows:
            lines.append(f'--- med = {med_label} ---')
            lines.extend(x_rows)
            lines.append('')

    # 偶然缺失一览（组内 A，但组内部分声母无字的明细）
    lines.append('')
    lines.append('===== 偶然缺失：组内 A 但部分声母无字 =====')
    for med in MEDIAL_ORDER:
        med_label = 'Ø' if med == '' else med
        acc_rows: list[str] = []
        for gname, gmembers in GROUPS:
            for fin in FINAL_ORDER:
                have = [i for i in gmembers if has_char.get((i, med, fin), False)]
                miss = [i for i in gmembers if not has_char.get((i, med, fin), False)]
                if have and miss:
                    acc_rows.append(
                        f'  {gname}  med={med_label} fin={fin}: '
                        f'有字=[{",".join(i or "Ø" for i in have)}], '
                        f'无字=[{",".join(i or "Ø" for i in miss)}]'
                    )
        if acc_rows:
            lines.append(f'--- med = {med_label} ---')
            lines.extend(acc_rows)
            lines.append('')

    # 每个 (group, med) 的全韵母分布
    lines.append('')
    lines.append('===== 每个 (group, med) 的 fin 分布 =====')
    for med in MEDIAL_ORDER:
        med_label = 'Ø' if med == '' else med
        for gname, gmembers in GROUPS:
            fin_ok = [f for f in FINAL_ORDER
                      if any(has_char.get((i, med, f), False) for i in gmembers)]
            fin_empty = [f for f in FINAL_ORDER
                         if not any(has_char.get((i, med, f), False) for i in gmembers)]
            if fin_ok:
                lines.append(
                    f'{gname}  med={med_label}: 有={fin_ok}  无={fin_empty}'
                )

    OUT_PATH.write_text('\n'.join(lines), encoding='utf-8')
    print(f'写入 {OUT_PATH}')
    print(f'共 {len(has_char)} 条 (ini, med, fin) 有字组合')


if __name__ == '__main__':
    main()
