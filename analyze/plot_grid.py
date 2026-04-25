"""
plot_grid.py
============

画 声母 × 韵母 的"热力格子图"。

内部坐标：IPA 声母 × IPA 介音 × IPA 韵母。
图上坐标：T拼 声母 × T拼 韵母（顺序与 ``analyze.py`` 一致）
每个介音（Ø / i / u）一个子图。

颜色：

* 灰色 —— 被 ``zsaanhehhehhu._is_sensible_combo`` 判为不合法（系统性驱逐）
* 绿色 —— 规则合法、且 ``readings.json`` 里至少有一个声调下有字；
  颜色深浅随该 (声母, 介音, 韵母) 下**汉字总数**（跨声调求和）
  以对数刻度渐变：字越多越深。
* 红色 —— 规则合法，但数据里任何声调都没字（系统内空洞）

用法::

    python analyze/plot_grid.py                      # 保存总图 PNG + 5 张分声调 PNG
    python analyze/plot_grid.py --show               # 交互弹窗
    python analyze/plot_grid.py -o custom_path.png   # 自定义输出路径
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.colors as mcolors  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import numpy as np  # noqa: E402

import zsaanhehhehhu as sp  # noqa: E402


READINGS_PATH = _ROOT / 'readings.json'
DEFAULT_OUTPUT = _HERE / 'phonology_grid.png'
DEFAULT_TXT_OUTPUT = _HERE / 'phonology_grid.txt'

# 顺序取自 analyze.py / 上海闲话.md 的典型编排
INITIALS = [
    'p', 'pʰ', 'b', 'm', 'f', 'v',
    't', 'tʰ', 'd', 'n', 'ɲ', 'l',
    'ts', 'tsʰ', 's', 'z',
    'tɕ', 'tɕʰ', 'dʑ', 'ɕ', 'ʑ',
    'k', 'kʰ', 'ɡ', 'ŋ', 'h', 'ɦ',
    '',
]
FINALS = [
    'ᴀ', 'ɛ', 'ø',
    'ɔ', 'ɤ', 'o', 'u',
    'i', 'y', 'ɿ',
    'ᴀ̃', 'ɑ̃', 'əŋ', 'oŋ', 'iŋ', 'yiŋ',
    'ᴀʔ', 'əʔ', 'oʔ', 'iɪʔ', 'yɪʔ',
    'əɻ', 'm̩', 'n̩', 'ŋ̍',
]
MEDIALS = ['', 'j', 'w']
TONES = list(sp.TONE_MAP)
RU_FINALS = {'ᴀʔ', 'əʔ', 'oʔ', 'iɪʔ', 'yɪʔ'}
TONE_LABELS = {
    '1': '1 阴平',
    '5': '5 阴去',
    '6': '6 阳去',
    '7': '7 阴入',
    '8': '8 阳入',
}

_WX_INITIAL_BY_IPA = {ipa: wx for wx, (_tp, ipa) in sp.INITIAL_MAP.items()}
_WX_MEDIAL_BY_IPA = {ipa: wx for wx, (_tp, ipa) in sp.MEDIAL_MAP.items()}
_WX_FINAL_BY_IPA = {ipa: wx for wx, (_tp, ipa) in sp.FINAL_MAP.items()}


def _parts_to_ipa(combo: tuple[str, str, str, str]) -> tuple[str, str, str, str]:
    ini, med, fin, tone = combo
    return (
        sp.INITIAL_MAP[ini][1],
        sp.MEDIAL_MAP[med][1],
        sp.FINAL_MAP[fin][1],
        tone,
    )


def _to_wx_parts(ini: str, med: str, fin: str) -> tuple[str, str, str]:
    return (
        _WX_INITIAL_BY_IPA[ini],
        _WX_MEDIAL_BY_IPA[med],
        _WX_FINAL_BY_IPA[fin],
    )


# 坐标轴显示：T拼 标签。
# 注意：IPA `n`(南) 和 `ɲ`(娘) 在 T拼 下都写作 `n`，故分别标注 `n(南)` / `n(娘)`
# 以免 Y 轴两行重名；`Ø` 代表零声母 / 空介音。
def _ini_tpin_label(ini: str) -> str:
    if ini == '':
        return 'Ø'
    if ini == 'n':
        return 'n(南)'
    if ini == 'ɲ':
        return 'n(娘)'
    return sp.INITIAL_MAP[_WX_INITIAL_BY_IPA[ini]][0]


def _fin_tpin_label(fin: str) -> str:
    return sp.FINAL_MAP[_WX_FINAL_BY_IPA[fin]][0]


def _med_tpin_label(med: str) -> str:
    return 'Ø' if med == '' else sp.MEDIAL_MAP[_WX_MEDIAL_BY_IPA[med]][0]


# 三种状态
STATE_EXCLUDED = 0  # 灰 — 规则过滤掉
STATE_EMPTY    = 1  # 红 — 规则合法但无字
STATE_FILLED   = 2  # 绿 — 规则合法且有字

COLOR_EXCLUDED = '#d0d0d0'
COLOR_EMPTY    = '#e57373'
# 绿色渐变两端：浅（1 字）→ 深（最多字）。
COLOR_FILLED_LIGHT = '#d6eed9'
COLOR_FILLED_DARK  = '#1b5e20'
# 纯展示用（图例、txt 等默认绿）
COLOR_FILLED = '#66bb6a'


def _pretty(sym: str) -> str:
    return 'Ø' if sym == '' else sym


def _entry_chars(ch: str, entry: dict) -> set[str]:
    """返回一个 readings.json 条目代表的字形集合，用于去重计数。"""
    variants = entry.get('variants')
    if isinstance(variants, list):
        chars = {str(v) for v in variants if v}
        if chars:
            return chars
    return {ch}


def load_char_counts() -> tuple[
    dict[tuple[str, str, str], int],
    dict[str, dict[tuple[str, str, str], int]],
]:
    """从 readings.json 读出总计数，以及每个声调的 IPA (ini, med, fin) 汉字数。"""
    data = json.loads(READINGS_PATH.read_text(encoding='utf-8'))
    total_sets: dict[tuple[str, str, str], set[str]] = {}
    tone_sets: dict[str, dict[tuple[str, str, str], set[str]]] = {
        tone: {} for tone in TONES
    }

    for ch, entries in data.items():
        if not isinstance(ch, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parts = sp.ipa_digit_to_parts(entry.get('ipa') or '')
            if not parts:
                continue
            ini, med, fin, tone = _parts_to_ipa(parts)
            chars = _entry_chars(ch, entry)
            key = (ini, med, fin)
            total_sets.setdefault(key, set()).update(chars)
            if tone in tone_sets:
                tone_sets[tone].setdefault(key, set()).update(chars)

    totals = {key: len(chars) for key, chars in total_sets.items()}
    by_tone = {
        tone: {key: len(chars) for key, chars in counts.items()}
        for tone, counts in tone_sets.items()
    }
    return totals, by_tone


def _is_tone_sensible_combo(ini: str, med: str, fin: str, tone: str | None) -> bool:
    """总图只看音系组合；分声调图额外灰掉声调/入声不兼容的格子。"""
    wx_ini, wx_med, wx_fin = _to_wx_parts(ini, med, fin)
    if not sp._is_sensible_combo(wx_ini, wx_med, wx_fin):
        return False
    if tone is None:
        return True

    is_ru = fin in RU_FINALS
    if is_ru and tone not in {'7', '8'}:
        return False
    if (not is_ru) and tone in {'7', '8'}:
        return False
    return sp._is_tone_compatible_initial(wx_ini, tone, wx_med, wx_fin)


def build_grid(
    med: str,
    counts: dict[tuple[str, str, str], int],
    *,
    tone: str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (状态网格, 字数网格)。字数仅在 STATE_FILLED 格上有意义。"""
    state = np.zeros((len(INITIALS), len(FINALS)), dtype=int)
    cgrid = np.zeros((len(INITIALS), len(FINALS)), dtype=int)
    for i, ini in enumerate(INITIALS):
        for j, fin in enumerate(FINALS):
            if not _is_tone_sensible_combo(ini, med, fin, tone):
                state[i, j] = STATE_EXCLUDED
                continue
            c = counts.get((ini, med, fin), 0)
            if c > 0:
                state[i, j] = STATE_FILLED
                cgrid[i, j] = c
            else:
                state[i, j] = STATE_EMPTY
    return state, cgrid


def _make_filled_cmap() -> mcolors.LinearSegmentedColormap:
    """浅绿 → 深绿 的线性渐变 colormap。"""
    return mcolors.LinearSegmentedColormap.from_list(
        'filled_green', [COLOR_FILLED_LIGHT, COLOR_FILLED_DARK],
    )


def _cell_rgba(
    state: int, count: int, max_count: int,
    cmap_filled: mcolors.Colormap,
) -> tuple[float, float, float, float]:
    """按单元格状态 + 字数返回 RGBA。"""
    if state == STATE_EXCLUDED:
        return mcolors.to_rgba(COLOR_EXCLUDED)
    if state == STATE_EMPTY:
        return mcolors.to_rgba(COLOR_EMPTY)
    # STATE_FILLED —— 对数刻度映射到 [0, 1]
    if max_count <= 1:
        t = 1.0
    else:
        t = np.log1p(count) / np.log1p(max_count)
    return cmap_filled(t)


def setup_cjk_font() -> None:
    """尝试挂一个能显示中文的字体（Windows 下 Microsoft YaHei 基本都有）。"""
    candidates = [
        'Microsoft YaHei', 'Microsoft JhengHei',
        'SimHei', 'SimSun',
        'Noto Sans CJK SC', 'Source Han Sans SC',
        'PingFang SC', 'Arial Unicode MS',
    ]
    from matplotlib import font_manager
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            plt.rcParams['font.sans-serif'] = [name] + plt.rcParams.get(
                'font.sans-serif', [])
            plt.rcParams['axes.unicode_minus'] = False
            return


_SYM_EXCLUDED = '-'   # 灰：规则排除
_SYM_FILLED   = 'O'   # 绿：有字
_SYM_EMPTY    = 'x'   # 红：规则内无字


def dump_txt(path: Path, counts: dict[tuple[str, str, str], int]) -> None:
    """把 3 张 声母 × 韵母 网格写成人类可读的 ASCII 表格（T拼 标签）。

    绿色格子里填 (count) 以对应热力图的深浅；内部使用 IPA，T拼的
    `n(南)` / `n(娘)` 分别对应 IPA `n` / `ɲ`。
    """
    ini_labels = [_ini_tpin_label(i) for i in INITIALS]
    fin_labels = [_fin_tpin_label(f) for f in FINALS]

    lines: list[str] = []
    lines.append(
        f'图例：{_SYM_EXCLUDED} = 规则排除（灰）   '
        f'数字 = 有字（绿，数字 = 汉字总数）   '
        f'{_SYM_EMPTY} = 规则内无字（红）'
    )
    lines.append('标签：T拼（声母 n(南)=IPA n，n(娘)=IPA ɲ）。')
    lines.append('')
    col_w = max(max(len(f) for f in fin_labels), 3) + 1
    ini_w = max(len(s) for s in ini_labels)

    for med in MEDIALS:
        lines.append(f'===== 介音 = {_med_tpin_label(med)} =====')
        header = ' ' * (ini_w + 2) + ''.join(f'{f:>{col_w}}' for f in fin_labels)
        lines.append(header)
        n_excl = n_fill = n_empty = 0
        for ini, ini_label in zip(INITIALS, ini_labels):
            row_cells: list[str] = []
            for fin in FINALS:
                if not _is_tone_sensible_combo(ini, med, fin, None):
                    row_cells.append(_SYM_EXCLUDED)
                    n_excl += 1
                else:
                    c = counts.get((ini, med, fin), 0)
                    if c > 0:
                        row_cells.append(str(c))
                        n_fill += 1
                    else:
                        row_cells.append(_SYM_EMPTY)
                        n_empty += 1
            row_str = ''.join(f'{c:>{col_w}}' for c in row_cells)
            lines.append(f'{ini_label:<{ini_w}}  {row_str}')
        lines.append(
            f'小计：灰 {n_excl}  绿 {n_fill}  红 {n_empty}  '
            f'(合计 {n_excl + n_fill + n_empty})'
        )
        lines.append('')

    # 全局红色细目（规则内无字），方便在文本里快速扫描
    lines.append('===== 规则内无字 (声母, 介音, 韵母) 明细（T拼）=====')
    triples_by_im: dict[tuple[str, str], list[str]] = {}
    for ini in INITIALS:
        for med in MEDIALS:
            for fin in FINALS:
                if (
                    _is_tone_sensible_combo(ini, med, fin, None)
                    and counts.get((ini, med, fin), 0) == 0
                ):
                    triples_by_im.setdefault((ini, med), []).append(fin)
    for (ini, med), fins in sorted(
        triples_by_im.items(),
        key=lambda kv: (INITIALS.index(kv[0][0]), MEDIALS.index(kv[0][1])),
    ):
        fin_strs = [_fin_tpin_label(f) for f in fins]
        lines.append(
            f'  ({_ini_tpin_label(ini)}, {_med_tpin_label(med)})  →  '
            f'{{{", ".join(fin_strs)}}}   [{len(fins)}]'
        )
    lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'已保存 txt 到 {path}')


def _plot_one(
    output: Path | None,
    show: bool,
    counts: dict[tuple[str, str, str], int],
    *,
    tone: str | None = None,
) -> None:
    setup_cjk_font()

    max_count = max(counts.values()) if counts else 1

    # 每个 med 一张格子：先算 state + cgrid，再组一张 RGBA 图。
    cmap_filled = _make_filled_cmap()

    fig, axes = plt.subplots(
        1, len(MEDIALS),
        figsize=(4 + 0.45 * len(FINALS) * len(MEDIALS), 0.38 * len(INITIALS) + 2),
        sharey=True,
    )
    if len(MEDIALS) == 1:
        axes = [axes]

    stats = []
    for ax, med in zip(axes, MEDIALS):
        state, cgrid = build_grid(med, counts, tone=tone)
        rgba = np.zeros((len(INITIALS), len(FINALS), 4), dtype=float)
        for i in range(len(INITIALS)):
            for j in range(len(FINALS)):
                rgba[i, j] = _cell_rgba(
                    int(state[i, j]), int(cgrid[i, j]),
                    max_count, cmap_filled,
                )
        ax.imshow(rgba, aspect='auto', interpolation='nearest')

        # 在有字格子上标注字数（白字描黑边保证在深绿 / 浅绿下都可读）
        from matplotlib import patheffects
        for i in range(len(INITIALS)):
            for j in range(len(FINALS)):
                if state[i, j] != STATE_FILLED:
                    continue
                c = int(cgrid[i, j])
                # 浅绿背景下（字少）用深色字；深绿背景下（字多）用白字
                t = np.log1p(c) / np.log1p(max_count) if max_count > 1 else 1.0
                txt_color = 'white' if t > 0.45 else '#1b3a1e'
                ax.text(
                    j, i, str(c),
                    ha='center', va='center',
                    fontsize=7, color=txt_color,
                    path_effects=[
                        patheffects.withStroke(
                            linewidth=0.6,
                            foreground='black' if txt_color == 'white' else 'white',
                        )
                    ],
                )

        # 网格线
        ax.set_xticks(np.arange(-0.5, len(FINALS), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(INITIALS), 1), minor=True)
        ax.grid(which='minor', color='white', linewidth=0.8)
        ax.tick_params(which='minor', length=0)
        # 主刻度（T拼 标签）
        ax.set_xticks(range(len(FINALS)))
        ax.set_xticklabels(
            [_fin_tpin_label(f) for f in FINALS],
            rotation=45, ha='right', fontsize=9,
        )
        ax.set_yticks(range(len(INITIALS)))
        ax.set_yticklabels(
            [_ini_tpin_label(i) for i in INITIALS], fontsize=9,
        )
        ax.set_xlabel('韵母（T拼）')
        ax.set_title(f'介音: {_med_tpin_label(med)}', fontsize=11)

        n_excl = int((state == STATE_EXCLUDED).sum())
        n_fill = int((state == STATE_FILLED).sum())
        n_empty = int((state == STATE_EMPTY).sum())
        stats.append((med, n_excl, n_fill, n_empty))

    axes[0].set_ylabel('声母（T拼）')

    excluded_label = '规则排除（灰）' if tone is None else '规则/声调排除（灰）'
    filled_light_label = '有字（浅绿 = 1 字）'
    filled_dark_label = f'有字（深绿 = {max_count} 字，log 刻度）'
    empty_label = '规则内无字（红）' if tone is None else '本声调规则内无字（红）'
    legend_handles = [
        Patch(facecolor=COLOR_EXCLUDED, edgecolor='black', linewidth=0.3,
              label=excluded_label),
        Patch(facecolor=COLOR_FILLED_LIGHT, edgecolor='black', linewidth=0.3,
              label=filled_light_label),
        Patch(facecolor=COLOR_FILLED_DARK, edgecolor='black', linewidth=0.3,
              label=filled_dark_label),
        Patch(facecolor=COLOR_EMPTY, edgecolor='black', linewidth=0.3,
              label=empty_label),
    ]
    fig.legend(handles=legend_handles, loc='lower center',
               ncol=4, frameon=False, bbox_to_anchor=(0.5, -0.02))

    stat_lines = [
        f'med={_med_tpin_label(m)}: 灰 {g}  绿 {f}  红 {e}'
        for (m, g, f, e) in stats
    ]
    if tone is None:
        title = '上海话音节结构热力图   |   ' + '    '.join(stat_lines)
    else:
        title = (
            f'上海话音节结构热力图（声调 {TONE_LABELS.get(tone, tone)}）'
            '   |   ' + '    '.join(stat_lines)
        )
    fig.suptitle(title, fontsize=12)

    fig.tight_layout(rect=(0, 0.03, 1, 0.96))

    if output is not None:
        fig.savefig(output, dpi=160, bbox_inches='tight')
        print(f'已保存到 {output}')
    if show:
        plt.show()
    plt.close(fig)


def _tone_output_path(output: Path, tone: str) -> Path:
    suffix = output.suffix or '.png'
    return output.with_name(f'{output.stem}_tone{tone}{suffix}')


def _optional_path(value: str) -> Path | None:
    return None if value == '' else Path(value)


def plot(output: Path | None, show: bool, *, tone_plots: bool = True) -> None:
    total_counts, counts_by_tone = load_char_counts()

    _plot_one(output=output, show=show, counts=total_counts)

    if not tone_plots:
        return
    for tone in TONES:
        tone_output = _tone_output_path(output, tone) if output is not None else None
        _plot_one(
            output=tone_output,
            show=show,
            counts=counts_by_tone.get(tone, {}),
            tone=tone,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else '')
    parser.add_argument(
        '-o', '--output', default=str(DEFAULT_OUTPUT),
        help=f'PNG 输出路径（默认 {DEFAULT_OUTPUT.name}）；传入空串则不保存。',
    )
    parser.add_argument(
        '--txt', default=str(DEFAULT_TXT_OUTPUT),
        help=f'TXT 输出路径（默认 {DEFAULT_TXT_OUTPUT.name}）；传入空串则不保存 txt。',
    )
    parser.add_argument(
        '--show', action='store_true', help='显示交互窗口（默认仅保存 PNG）。',
    )
    parser.add_argument(
        '--no-tone-plots', action='store_true',
        help='只画总图，不额外保存 5 张分声调图。',
    )
    args = parser.parse_args(argv)

    output = _optional_path(args.output)
    txt_output = _optional_path(args.txt)

    if txt_output is not None:
        total_counts, _counts_by_tone = load_char_counts()
        dump_txt(txt_output, total_counts)

    plot(output=output, show=args.show, tone_plots=not args.no_tone_plots)


if __name__ == '__main__':
    main()
