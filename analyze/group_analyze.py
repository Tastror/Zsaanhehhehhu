"""
group_analyze.py
================

按 "声母自然类" 聚合分析根目录 ``readings.json``。

用户给定的自然类（每组内部规则应完全一致）：

    G1 = {p, pʰ, b}          唇塞
    G2 = {m}                 唇鼻
    G3 = {f, v}              唇齿擦
    G4 = {t, tʰ, d}          齿塞
    G5 = {n, ɲ}              齿鼻 + 齿腭鼻（互补）
    G6 = {l}                 边
    G7 = {ts, tsʰ, s, z}     齿擦/齿塞擦
    G8 = {tɕ, tɕʰ, dʑ, ɕ, ʑ} 腭塞擦/腭擦
    G9 = {k, kʰ, ɡ, h}       软腭塞 + 晓
    G10 = {ŋ}                疑母
    G11 = {ɦ}                浊喉擦（匣）
    G12 = {Ø}                零声母

对每个 (group, med, fin) 三元组：
- 若组内 **至少一个** 声母数据里有字 —— 记为 A (accidental-OK)：
  整组按"规则合法"；组内其他无字的声母算"偶然缺失"。
- 若组内 **所有** 声母都无字 —— 记为 . (systematic-gap)：
  整组按"规则不合法"。

然后把每个 (med, fin) 列：组是 A 还是 . 打成网格，人眼找规则。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import zsaanghehhehhu as sp  # noqa: E402


READINGS_PATH = _ROOT / 'readings.json'
OUT_PATH = _HERE / 'group_grid.txt'


# 用户给定的自然类
GROUPS: list[tuple[str, list[str]]] = [
    ('G1  p/pʰ/b      ', ['p', 'pʰ', 'b']),
    ('G2  m           ', ['m']),
    ('G3  f/v         ', ['f', 'v']),
    ('G4  t/tʰ/d      ', ['t', 'tʰ', 'd']),
    ('G5  n/ɲ         ', ['n', 'ɲ']),
    ('G6  l           ', ['l']),
    ('G7  ts/tsʰ/s/z  ', ['ts', 'tsʰ', 's', 'z']),
    ('G8  tɕ/tɕʰ/dʑ/ɕ/ʑ', ['tɕ', 'tɕʰ', 'dʑ', 'ɕ', 'ʑ']),
    ('G9  k/kʰ/ɡ/h    ', ['k', 'kʰ', 'ɡ', 'h']),
    ('G10 ŋ           ', ['ŋ']),
    ('G11 ɦ           ', ['ɦ']),
    ('G12 Ø           ', ['']),
]

MEDIAL_ORDER = ['', 'j', 'w']
FINAL_ORDER = [
    'ᴀ', 'o', 'i', 'ɿ', 'u', 'y',
    'ɛ', 'ɔ', 'ɤ', 'ø',
    'ᴀ̃', 'ɑ̃', 'oŋ', 'əŋ', 'iŋ', 'yiŋ',
    'ᴀʔ', 'əʔ', 'oʔ', 'iɪʔ', 'yɪʔ',
    'əɻ', 'm̩', 'n̩', 'ŋ̍',
]


def _parts_to_ipa(combo: tuple[str, str, str, str]) -> tuple[str, str, str, str]:
    ini, med, fin, tone = combo
    return (
        sp.INITIAL_MAP[ini][1],
        sp.MEDIAL_MAP[med][1],
        sp.FINAL_MAP[fin][1],
        tone,
    )


def _label(sym: str) -> str:
    return 'Ø' if sym == '' else sym


def load_has_char() -> dict[tuple[str, str, str], bool]:
    """(ini, med, fin) -> True iff 数据里该组合至少有一个字例。"""
    data = json.loads(READINGS_PATH.read_text(encoding='utf-8'))
    out: dict[tuple[str, str, str], bool] = {}
    for _ch, entries in data.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parts = sp.ipa_digit_to_parts(entry.get('ipa') or '')
            if not parts:
                continue
            ini, med, fin, _tone = _parts_to_ipa(parts)
            out[(ini, med, fin)] = True
    return out


def main():
    has_char = load_has_char()

    lines: list[str] = []
    lines.append('组级网格：每格 A=组内至少1个声母有字；.=组内全无字；')
    lines.append('  每组后面括号里写"有/无字"的声母分布（有字的加方括号）。')
    lines.append('')

    group_w = max(len(gname) for gname, _gmembers in GROUPS)
    fin_hdr = ' ' * (group_w + 1) + '  '.join(f'{f:>4}' for f in FINAL_ORDER)
    for med in MEDIAL_ORDER:
        med_label = _label(med)
        lines.append('')
        lines.append(f'===== 介音 = {med_label} =====')
        lines.append(fin_hdr)
        for gname, gmembers in GROUPS:
            row = [f'{gname:<{group_w}}']
            for fin in FINAL_ORDER:
                any_has = any(has_char.get((i, med, fin), False) for i in gmembers)
                cell = ' A  ' if any_has else ' .  '
                row.append(cell)
            lines.append(' '.join(row))

    # 系统性缺失一览 (. 格)
    lines.append('')
    lines.append('')
    lines.append('===== 系统性缺失：组全无字 (. 格) =====')
    for med in MEDIAL_ORDER:
        med_label = _label(med)
        x_rows: list[str] = []
        for gname, gmembers in GROUPS:
            x_fins = []
            for fin in FINAL_ORDER:
                any_has = any(has_char.get((i, med, fin), False) for i in gmembers)
                if not any_has:
                    x_fins.append(fin)
            if x_fins:
                x_rows.append(f'  {gname}  med={med_label}  .-fins: {", ".join(x_fins)}')
        if x_rows:
            lines.append(f'--- med = {med_label} ---')
            lines.extend(x_rows)
            lines.append('')

    # 偶然缺失一览（组内 A，但组内部分声母无字的明细）
    lines.append('')
    lines.append('===== 偶然缺失：组内 A 但部分声母无字 =====')
    for med in MEDIAL_ORDER:
        med_label = _label(med)
        acc_rows: list[str] = []
        for gname, gmembers in GROUPS:
            for fin in FINAL_ORDER:
                have = [i for i in gmembers if has_char.get((i, med, fin), False)]
                miss = [i for i in gmembers if not has_char.get((i, med, fin), False)]
                if have and miss:
                    acc_rows.append(
                        f'  {gname}  med={med_label} fin={fin}: '
                        f'有字=[{",".join(_label(i) for i in have)}], '
                        f'无字=[{",".join(_label(i) for i in miss)}]'
                    )
        if acc_rows:
            lines.append(f'--- med = {med_label} ---')
            lines.extend(acc_rows)
            lines.append('')

    # 每个 (group, med) 的全韵母分布
    lines.append('')
    lines.append('===== 每个 (group, med) 的 fin 分布 =====')
    for med in MEDIAL_ORDER:
        med_label = _label(med)
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
    print(f'共 {len(has_char)} 条 (IPA声母, IPA介音, IPA韵母) 有字组合')


if __name__ == '__main__':
    main()
