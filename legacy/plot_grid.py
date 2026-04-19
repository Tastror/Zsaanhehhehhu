"""
plot_grid.py
============

画一张 声母 × 韵母 的"热力格子图"。

横轴：韵母（按吴学记号，顺序与 ``analyze.py`` 一致）
纵轴：声母（按吴学记号）
每个介音（Ø / i / u）一个子图。

颜色：

* 灰色 —— 被 ``shanghai_pinyin._is_sensible_combo`` 判为不合法（系统性驱逐）
* 绿色 —— 规则合法、且 ``syllable_coverage.json`` 里至少有一个声调下有字；
  颜色深浅随该 (声母, 介音, 韵母) 下**汉字总数**（跨声调求和）
  以对数刻度渐变：字越多越深。
* 红色 —— 规则合法，但数据里任何声调都没字（系统内空洞）

用法::

    python legacy/plot_grid.py                       # 保存 PNG
    python legacy/plot_grid.py --show                # 交互弹窗
    python legacy/plot_grid.py -o custom_path.png    # 自定义输出路径
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

import shanghai_pinyin as sp  # noqa: E402


COVERAGE_PATH = _HERE / 'syllable_coverage.json'
DEFAULT_OUTPUT = _HERE / 'phonology_grid.png'
DEFAULT_TXT_OUTPUT = _HERE / 'phonology_grid.txt'

# 顺序取自 analyze.py / 上海闲话.md 的典型编排
INITIALS = [
    'p', 'ph', 'b', 'm', 'f', 'v',
    't', 'th', 'd', 'n', 'gn', 'l',
    'ts', 'tsh', 's', 'z',
    'c', 'ch', 'j', 'sh', 'zh',
    'k', 'kh', 'g', 'ng', 'h', 'gh',
    '',
]
FINALS = [
    'a', 'o', 'i', 'y', 'u', 'iu',
    'e', 'au', 'eu', 'oe',
    'an', 'aon', 'on', 'en', 'in', 'iun',
    'aq', 'eq', 'oq', 'iq', 'iuq',
    'er', 'm', 'n', 'ng',
]
MEDIALS = ['', 'i', 'u']


# 坐标轴显示：T拼 标签。
# 注意：吴学 `n`(南) 和 `gn`(娘) 在 T拼 下都写作 `n`，故分别标注 `n(南)` / `n(娘)`
# 以免 Y 轴两行重名；`Ø` 代表零声母 / 空介音。
def _ini_tpin_label(ini: str) -> str:
    if ini == '':
        return 'Ø'
    if ini == 'n':
        return 'n(南)'
    if ini == 'gn':
        return 'n(娘)'
    return sp.INITIAL_MAP[ini][0]


def _fin_tpin_label(fin: str) -> str:
    return sp.FINAL_MAP[fin][0]


def _med_tpin_label(med: str) -> str:
    return 'Ø' if med == '' else sp.MEDIAL_MAP[med][0]


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


def load_char_counts() -> dict[tuple[str, str, str], int]:
    """从 syllable_coverage.json 读出 (ini, med, fin) 下跨声调的汉字总数。"""
    data = json.loads(COVERAGE_PATH.read_text(encoding='utf-8'))
    out: dict[tuple[str, str, str], int] = {}
    for wx, info in data.items():
        parsed = sp.parse_syllable(wx)
        if not parsed:
            continue
        _canon, ini, med, fin, _tone = parsed
        if not isinstance(info, dict):
            continue
        cnt = int(info.get('count', 0) or 0)
        if cnt <= 0:
            continue
        out[(ini, med, fin)] = out.get((ini, med, fin), 0) + cnt
    return out


def build_grid(
    med: str, counts: dict[tuple[str, str, str], int]
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (状态网格, 字数网格)。字数仅在 STATE_FILLED 格上有意义。"""
    state = np.zeros((len(INITIALS), len(FINALS)), dtype=int)
    cgrid = np.zeros((len(INITIALS), len(FINALS)), dtype=int)
    for i, ini in enumerate(INITIALS):
        for j, fin in enumerate(FINALS):
            if not sp._is_sensible_combo(ini, med, fin):
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

    绿色格子里填 (count) 以对应热力图的深浅；声母 `n(泥)` / `n(娘)`
    对应吴学 `n` / `gn`。
    """
    ini_labels = [_ini_tpin_label(i) for i in INITIALS]
    fin_labels = [_fin_tpin_label(f) for f in FINALS]

    lines: list[str] = []
    lines.append(
        f'图例：{_SYM_EXCLUDED} = 规则排除（灰）   '
        f'数字 = 有字（绿，数字 = 汉字总数）   '
        f'{_SYM_EMPTY} = 规则内无字（红）'
    )
    lines.append('标签：T拼（声母 n(泥)=吴学 n，n(娘)=吴学 gn）。')
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
                if not sp._is_sensible_combo(ini, med, fin):
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
                    sp._is_sensible_combo(ini, med, fin)
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


def plot(output: Path | None, show: bool) -> None:
    setup_cjk_font()

    counts = load_char_counts()
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
        state, cgrid = build_grid(med, counts)
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

    legend_handles = [
        Patch(facecolor=COLOR_EXCLUDED, edgecolor='black', linewidth=0.3,
              label='规则排除（灰）'),
        Patch(facecolor=COLOR_FILLED_LIGHT, edgecolor='black', linewidth=0.3,
              label=f'有字（浅绿 = 1 字）'),
        Patch(facecolor=COLOR_FILLED_DARK, edgecolor='black', linewidth=0.3,
              label=f'有字（深绿 = {max_count} 字，log 刻度）'),
        Patch(facecolor=COLOR_EMPTY, edgecolor='black', linewidth=0.3,
              label='规则内无字（红）'),
    ]
    fig.legend(handles=legend_handles, loc='lower center',
               ncol=4, frameon=False, bbox_to_anchor=(0.5, -0.02))

    stat_lines = [
        f'med={_med_tpin_label(m)}: 灰 {g}  绿 {f}  红 {e}'
        for (m, g, f, e) in stats
    ]
    fig.suptitle(
        '上海话音节结构热力图   |   ' + '    '.join(stat_lines),
        fontsize=12,
    )

    fig.tight_layout(rect=(0, 0.03, 1, 0.96))

    if output is not None:
        fig.savefig(output, dpi=160, bbox_inches='tight')
        print(f'已保存到 {output}')
    if show:
        plt.show()
    plt.close(fig)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else '')
    parser.add_argument(
        '-o', '--output', type=Path, default=DEFAULT_OUTPUT,
        help=f'PNG 输出路径（默认 {DEFAULT_OUTPUT.name}）；传入空串则不保存。',
    )
    parser.add_argument(
        '--txt', type=Path, default=DEFAULT_TXT_OUTPUT,
        help=f'TXT 输出路径（默认 {DEFAULT_TXT_OUTPUT.name}）；传入空串则不保存 txt。',
    )
    parser.add_argument(
        '--show', action='store_true', help='显示交互窗口（默认仅保存 PNG）。',
    )
    args = parser.parse_args(argv)

    output: Path | None = args.output
    if output is not None and str(output) == '':
        output = None

    txt_output: Path | None = args.txt
    if txt_output is not None and str(txt_output) == '':
        txt_output = None

    if txt_output is not None:
        dump_txt(txt_output, load_char_counts())

    plot(output=output, show=args.show)


if __name__ == '__main__':
    main()
