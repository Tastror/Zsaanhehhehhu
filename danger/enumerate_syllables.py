"""
enumerate_syllables.py
======================

枚举 ``zsaanghehhehhu._enumerate_canonical_syllables()`` 给出的 5000+ 个表层
合法音节，拿每个吴学拼音到 https://www.wugniu.com 反查对应字。用于摸清哪些
音节在 wugniu 语料里实际没有字例，进而判断是否还有不合法的音系组合。

特性
----
* 多线程并发（默认 8）
* 处理分页（wugniu 每页 10 条）
* 断点续查：重复运行自动跳过 ``syllable_coverage.json`` 里已经有结果的音节
* 周期性落盘 + 优雅中断（Ctrl+C 当前已查内容仍会被保存）

输出：``syllable_coverage.json``（与本脚本同目录）

::

  {
    "a1": {
      "count": 3,
      "entries": [
        {"char": "啊", "note": "", "py_audio": "a1"},
        {"char": "挨", "note": "", "py_audio": "a1"},
        ...
      ]
    },
    "xxx9": {"count": 0, "entries": []},
    "some_broken": {"error": "HTTPError: ..."}
  }

用法::

  python enumerate_syllables.py                 # 默认 8 线程、断点续查
  python enumerate_syllables.py --workers 16    # 提高并发
  python enumerate_syllables.py --limit 200     # 只查前 200 个（调试）
  python enumerate_syllables.py --no-resume     # 忽略已有结果从头查
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import zsaanghehhehhu as sp


OUTPUT_PATH = Path(__file__).resolve().parent / 'syllable_coverage.json'

_UA = 'Mozilla/5.0 (ShanghaiPinyinSyllableEnumerator/1.0)'

# 解析分页：从 <a href="...page=N"> 里抓最大页码
_PAGE_RE = re.compile(r'[?&]page=(\d+)')


def _fetch(wx: str, page: int, *, timeout: float = 15.0, retries: int = 2) -> str:
    url = (
        'https://www.wugniu.com/search?char='
        + urllib.parse.quote(wx)
        + f'&table=shanghai&page={page}'
    )
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    assert last_err is not None
    raise last_err


def _extract_rows(html: str) -> list[dict]:
    out = []
    for m in sp._ROW_RE.finditer(html):
        out.append({
            'char': urllib.parse.unquote(m.group('ch')),
            'note': m.group('note').strip(),
            'py_audio': m.group('py'),
        })
    return out


def query_syllable(wx: str, *, timeout: float = 15.0) -> dict:
    """查某个吴学拼音在 wugniu 里对应的所有字（自动翻页合并）。"""
    html1 = _fetch(wx, 1, timeout=timeout)
    pages = {int(m.group(1)) for m in _PAGE_RE.finditer(html1)}
    max_page = max(pages) if pages else 1

    entries = _extract_rows(html1)
    for p in range(2, max_page + 1):
        html = _fetch(wx, p, timeout=timeout)
        entries.extend(_extract_rows(html))

    # 按 (char, note) 去重（稳定保留首次出现顺序）
    seen: set[tuple[str, str]] = set()
    dedup = []
    for e in entries:
        key = (e['char'], e['note'])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(e)
    return {'count': len(dedup), 'entries': dedup}


# =============================================================================
# JSON I/O
# =============================================================================

def load_existing() -> dict:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        data = json.loads(OUTPUT_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        print(f'[warn] 读取 {OUTPUT_PATH} 失败：{exc}', file=sys.stderr)
    return {}


def save(data: dict) -> None:
    tmp = OUTPUT_PATH.with_suffix('.json.tmp')
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    tmp.replace(OUTPUT_PATH)


# =============================================================================
# 主流程
# =============================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--workers', type=int, default=8, help='并发线程数（默认 8）')
    ap.add_argument('--no-resume', action='store_true',
                    help='忽略已有 syllable_coverage.json，从头查')
    ap.add_argument('--limit', type=int, default=0,
                    help='只查前 N 个音节（调试用；0 = 全部）')
    ap.add_argument('--save-every', type=int, default=50,
                    help='每完成 N 个音节落盘一次（默认 50）')
    ap.add_argument('--retry-errors', action='store_true',
                    help='把之前标记为 error 的音节重新查一次')
    args = ap.parse_args()

    canonical, _aliased = sp._enumerate_canonical_syllables()
    all_wx = [row[0] for row in canonical]
    if args.limit:
        all_wx = all_wx[:args.limit]

    data: dict = {} if args.no_resume else load_existing()

    def needs_query(wx: str) -> bool:
        if wx not in data:
            return True
        if args.retry_errors and isinstance(data.get(wx), dict) and 'error' in data[wx]:
            return True
        return False

    pending = [wx for wx in all_wx if needs_query(wx)]
    print(f'合法音节 {len(all_wx)}，已查 {len(all_wx) - len(pending)}，'
          f'本次待查 {len(pending)}，并发 {args.workers}')
    if not pending:
        print('全部已查过。')
        _print_stats(data, all_wx)
        return

    stop = Event()
    lock = Lock()
    start = time.time()
    done_count = [0]
    err_count = [0]

    def worker(wx: str) -> None:
        if stop.is_set():
            return
        try:
            result = query_syllable(wx)
        except Exception as exc:
            result = {'error': f'{type(exc).__name__}: {exc}'}
            with lock:
                err_count[0] += 1

        with lock:
            data[wx] = result
            done_count[0] += 1
            d = done_count[0]
            if d % max(1, args.save_every) == 0:
                save(data)
            elapsed = time.time() - start
            rate = d / max(elapsed, 1e-6)
            eta_s = max(0, (len(pending) - d)) / max(rate, 1e-6)
            count = result.get('count', '?') if 'error' not in result else 'ERR'
            print(f'  [{d:>4}/{len(pending)}] {wx:>10} → {count:>3} 字  '
                  f'| 速率 {rate:5.1f}/s  ETA {eta_s/60:5.1f}min')

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            # 提交所有任务；如果 Ctrl+C，尽快退出
            futures = [pool.submit(worker, wx) for wx in pending]
            for f in futures:
                if stop.is_set():
                    break
                f.result()
    except KeyboardInterrupt:
        stop.set()
        print('\n[!] 收到 Ctrl+C，保存已完成的结果…', file=sys.stderr)
    finally:
        save(data)

    _print_stats(data, all_wx)
    print(f'\n本次错误 {err_count[0]} 条。若需重试，加 --retry-errors。')


def _print_stats(data: dict, all_wx: list[str]) -> None:
    queried = sum(1 for wx in all_wx if wx in data)
    empty = sum(
        1 for wx in all_wx
        if wx in data and isinstance(data[wx], dict)
        and 'error' not in data[wx] and data[wx].get('count', 0) == 0
    )
    err = sum(
        1 for wx in all_wx
        if wx in data and isinstance(data[wx], dict) and 'error' in data[wx]
    )
    nonempty = queried - empty - err
    total_chars = sum(
        data[wx].get('count', 0)
        for wx in all_wx
        if wx in data and isinstance(data[wx], dict) and 'error' not in data[wx]
    )
    print(f'\n=== 覆盖统计（共 {len(all_wx)} 个音节）===')
    print(f'  已查询：       {queried}')
    print(f'  有字例：       {nonempty}')
    print(f'  无字例（空）： {empty}')
    print(f'  错误：         {err}')
    print(f'  累计命中字数： {total_chars}')
    print(f'  未查询：       {len(all_wx) - queried}')
    print(f'输出：{OUTPUT_PATH}')


if __name__ == '__main__':
    main()
