"""
plot_grid.py
============

画一张 声母 × 韵母 的"热力格子图"。

横轴：韵母（按吴学记号，顺序与 ``analyze.py`` 一致）
纵轴：声母（按吴学记号）
每个介音（Ø / i / u）一个子图。

颜色：

* 灰色 —— 被 ``shanghai_pinyin._is_sensible_combo`` 判为不合法（系统性驱逐）
* 绿色 —— 规则合法、且 ``syllable_coverage.json`` 里至少有一个声调下有字
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

# 三种状态
STATE_EXCLUDED = 0  # 灰 — 规则过滤掉
STATE_EMPTY    = 1  # 红 — 规则合法但无字
STATE_FILLED   = 2  # 绿 — 规则合法且有字

COLOR_EXCLUDED = '#d0d0d0'
COLOR_EMPTY    = '#e57373'
COLOR_FILLED   = '#66bb6a'


def _pretty(sym: str) -> str:
    return 'Ø' if sym == '' else sym


def load_has_char() -> dict[tuple[str, str, str], bool]:
    """从 syllable_coverage.json 读出 (ini, med, fin) 是否至少有一个声调有字。"""
    data = json.loads(COVERAGE_PATH.read_text(encoding='utf-8'))
    out: dict[tuple[str, str, str], bool] = {}
    for wx, info in data.items():
        parsed = sp.parse_syllable(wx)
        if not parsed:
            continue
        _canon, ini, med, fin, _tone = parsed
        if not isinstance(info, dict):
            continue
        if info.get('count', 0) > 0:
            out[(ini, med, fin)] = True
    return out


def build_grid(med: str, has_char: dict[tuple[str, str, str], bool]) -> np.ndarray:
    grid = np.zeros((len(INITIALS), len(FINALS)), dtype=int)
    for i, ini in enumerate(INITIALS):
        for j, fin in enumerate(FINALS):
            if not sp._is_sensible_combo(ini, med, fin):
                grid[i, j] = STATE_EXCLUDED
            elif has_char.get((ini, med, fin)):
                grid[i, j] = STATE_FILLED
            else:
                grid[i, j] = STATE_EMPTY
    return grid


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


def dump_txt(path: Path, has_char: dict[tuple[str, str, str], bool]) -> None:
    """把 3 张 声母 × 韵母 网格写成人类可读的 ASCII 表格。"""
    lines: list[str] = []
    lines.append(
        f'图例：{_SYM_EXCLUDED} = 规则排除（灰）   '
        f'{_SYM_FILLED} = 有字（绿）   '
        f'{_SYM_EMPTY} = 规则内无字（红）'
    )
    lines.append('')
    col_w = max(len(f) for f in FINALS) + 1
    ini_w = max(len(_pretty(i)) for i in INITIALS)

    for med in MEDIALS:
        lines.append(f'===== 介音 = {_pretty(med)} =====')
        header = ' ' * (ini_w + 2) + ''.join(f'{f:>{col_w}}' for f in FINALS)
        lines.append(header)
        n_excl = n_fill = n_empty = 0
        for ini in INITIALS:
            row_cells = []
            for fin in FINALS:
                if not sp._is_sensible_combo(ini, med, fin):
                    row_cells.append(_SYM_EXCLUDED)
                    n_excl += 1
                elif has_char.get((ini, med, fin)):
                    row_cells.append(_SYM_FILLED)
                    n_fill += 1
                else:
                    row_cells.append(_SYM_EMPTY)
                    n_empty += 1
            # 行字串形式：每格右对齐到 col_w 宽
            row_str = ''.join(f'{c:>{col_w}}' for c in row_cells)
            lines.append(f'{_pretty(ini):<{ini_w}}  {row_str}')
        lines.append(
            f'小计：灰 {n_excl}  绿 {n_fill}  红 {n_empty}  '
            f'(合计 {n_excl + n_fill + n_empty})'
        )
        lines.append('')

    # 全局红色细目（规则内无字），方便在文本里快速扫描
    lines.append('===== 规则内无字 (声母, 介音, 韵母) 明细 =====')
    triples_by_im: dict[tuple[str, str], list[str]] = {}
    for ini in INITIALS:
        for med in MEDIALS:
            for fin in FINALS:
                if (
                    sp._is_sensible_combo(ini, med, fin)
                    and not has_char.get((ini, med, fin))
                ):
                    triples_by_im.setdefault((ini, med), []).append(fin)
    for (ini, med), fins in sorted(
        triples_by_im.items(),
        key=lambda kv: (INITIALS.index(kv[0][0]), MEDIALS.index(kv[0][1])),
    ):
        lines.append(
            f'  ({_pretty(ini)}, {_pretty(med)})  →  {{{", ".join(fins)}}}'
            f'   [{len(fins)}]'
        )
    lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'已保存 txt 到 {path}')


def plot(output: Path | None, show: bool) -> None:
    setup_cjk_font()

    has_char = load_has_char()

    cmap = mcolors.ListedColormap([COLOR_EXCLUDED, COLOR_EMPTY, COLOR_FILLED])
    norm = mcolors.BoundaryNorm([-0.5, 0.5, 1.5, 2.5], cmap.N)

    fig, axes = plt.subplots(
        1, len(MEDIALS),
        figsize=(4 + 0.45 * len(FINALS) * len(MEDIALS), 0.38 * len(INITIALS) + 2),
        sharey=True,
    )
    if len(MEDIALS) == 1:
        axes = [axes]

    stats = []
    for ax, med in zip(axes, MEDIALS):
        grid = build_grid(med, has_char)
        ax.imshow(grid, cmap=cmap, norm=norm, aspect='auto',
                  interpolation='nearest')
        # 网格线
        ax.set_xticks(np.arange(-0.5, len(FINALS), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(INITIALS), 1), minor=True)
        ax.grid(which='minor', color='white', linewidth=0.8)
        ax.tick_params(which='minor', length=0)
        # 主刻度
        ax.set_xticks(range(len(FINALS)))
        ax.set_xticklabels(FINALS, rotation=45, ha='right', fontsize=9)
        ax.set_yticks(range(len(INITIALS)))
        ax.set_yticklabels([_pretty(i) for i in INITIALS], fontsize=9)
        ax.set_xlabel('韵母')
        ax.set_title(f'介音: {_pretty(med)}', fontsize=11)

        n_excl = int((grid == STATE_EXCLUDED).sum())
        n_fill = int((grid == STATE_FILLED).sum())
        n_empty = int((grid == STATE_EMPTY).sum())
        stats.append((med, n_excl, n_fill, n_empty))

    axes[0].set_ylabel('声母')

    legend_handles = [
        Patch(facecolor=COLOR_EXCLUDED, edgecolor='black', linewidth=0.3,
              label='规则排除（灰）'),
        Patch(facecolor=COLOR_FILLED, edgecolor='black', linewidth=0.3,
              label='有字（绿）'),
        Patch(facecolor=COLOR_EMPTY, edgecolor='black', linewidth=0.3,
              label='规则内无字（红）'),
    ]
    fig.legend(handles=legend_handles, loc='lower center',
               ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.02))

    stat_lines = [
        f'med={_pretty(m)}: 灰 {g}  绿 {f}  红 {e}'
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
        dump_txt(txt_output, load_has_char())

    plot(output=output, show=args.show)


if __name__ == '__main__':
    main()
