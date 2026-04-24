"""
query_all_chars.py
==================

把 ``legacy/syllable_coverage.json`` 里出现过的所有汉字挨个到 wugniu 查一遍，
结果写到 ``readings.json``（本地缓存）。

特性
----
* 多线程并发（默认 8）
* 断点续查：``readings.json`` 里已有的字直接跳过
* 周期性落盘 + Ctrl+C 优雅中断（已查结果会被保存）

用法::

  python legacy/query_all_chars.py                 # 默认 8 线程
  python legacy/query_all_chars.py --workers 16    # 提高并发
  python legacy/query_all_chars.py --limit 200     # 只查前 N 个（调试）
  python legacy/query_all_chars.py --retry-empty   # 把缓存里的空结果重新查
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import zsaanhehhehhu as sp  # noqa: E402

COVERAGE_PATH = Path(__file__).resolve().parent / 'syllable_coverage.json'
READINGS_PATH = ROOT / 'readings.json'


def _collect_chars() -> list[str]:
    """从 syllable_coverage.json 里收集所有出现过的汉字，按音节字典序 + 内部顺序。"""
    data = json.loads(COVERAGE_PATH.read_text(encoding='utf-8'))
    seen: set[str] = set()
    order: list[str] = []
    for syll in sorted(data):
        v = data[syll]
        if not isinstance(v, dict):
            continue
        for e in v.get('entries', []):
            ch = e.get('char')
            if not ch or ch in seen:
                continue
            seen.add(ch)
            order.append(ch)
    return order


def _load_readings() -> dict[str, list[dict]]:
    if not READINGS_PATH.exists():
        return {}
    try:
        data = json.loads(READINGS_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, list)}
    except Exception as exc:
        print(f'[warn] 读 {READINGS_PATH} 失败：{exc}', file=sys.stderr)
    return {}


def _save_readings(data: dict[str, list[dict]]) -> None:
    tmp = READINGS_PATH.with_suffix('.json.tmp')
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    # Windows 下偶发「拒绝访问」，重试几次
    for i in range(5):
        try:
            tmp.replace(READINGS_PATH)
            return
        except PermissionError:
            time.sleep(0.3 * (i + 1))
    tmp.replace(READINGS_PATH)


def _query_entries(ch: str) -> list[dict]:
    """抓 wugniu → 打包成 readings.json 条目列表。"""
    rows = sp.fetch_readings(ch)

    merged: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for variant, py, note in rows:
        key = (py, note)
        if key not in merged:
            merged[key] = []
            order.append(key)
        if variant not in merged[key]:
            merged[key].append(variant)

    return [
        sp._entry_from_parsed(merged[(py, note)], py, note, sp.parse_syllable(py))
        for py, note in order
    ]


def _is_empty(entries: list[dict]) -> bool:
    return not entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--workers', type=int, default=8, help='并发线程数（默认 8）')
    ap.add_argument('--limit', type=int, default=0,
                    help='只查前 N 个（调试用；0 = 全部）')
    ap.add_argument('--save-every', type=int, default=30,
                    help='每完成 N 个落盘一次（默认 30）')
    ap.add_argument('--retry-empty', action='store_true',
                    help='把缓存里查得为空的字重新查一次')
    args = ap.parse_args()

    all_chars = _collect_chars()
    if args.limit:
        all_chars = all_chars[:args.limit]

    readings = _load_readings()

    def needs_query(ch: str) -> bool:
        if ch not in readings:
            return True
        if args.retry_empty and _is_empty(readings[ch]):
            return True
        return False

    pending = [ch for ch in all_chars if needs_query(ch)]
    print(f'syllable_coverage 里的汉字 {len(all_chars)}，'
          f'已缓存 {len(all_chars) - len(pending)}，'
          f'本次待查 {len(pending)}，并发 {args.workers}')
    if not pending:
        print('全部已查过。')
        return

    stop = Event()
    lock = Lock()
    start = time.time()
    done_count = [0]
    err_count = [0]

    def worker(ch: str) -> None:
        if stop.is_set():
            return
        try:
            entries = _query_entries(ch)
            ok = True
        except Exception as exc:
            entries = []
            ok = False
            with lock:
                err_count[0] += 1
            err_msg = f'{type(exc).__name__}: {exc}'
        else:
            err_msg = ''

        with lock:
            if ok:
                readings[ch] = entries
            d_before = done_count[0]
            done_count[0] += 1
            d = done_count[0]
            if d % max(1, args.save_every) == 0:
                try:
                    _save_readings(readings)
                except Exception as exc:
                    print(f'[warn] 落盘失败：{exc}', file=sys.stderr)
            elapsed = time.time() - start
            rate = d / max(elapsed, 1e-6)
            eta_s = max(0, (len(pending) - d)) / max(rate, 1e-6)
            if ok:
                tag = f'{len(entries):>2} 读音' if entries else ' 空'
            else:
                tag = f'ERR {err_msg}'
            print(f'  [{d:>4}/{len(pending)}] {ch} → {tag:<14} '
                  f'| 速率 {rate:5.1f}/s  ETA {eta_s/60:5.1f}min')

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(worker, ch) for ch in pending]
            for f in futures:
                if stop.is_set():
                    break
                f.result()
    except KeyboardInterrupt:
        stop.set()
        print('\n[!] 收到 Ctrl+C，保存已完成的结果…', file=sys.stderr)
    finally:
        try:
            _save_readings(readings)
        except Exception as exc:
            print(f'[warn] 最终落盘失败：{exc}', file=sys.stderr)

    total = len(all_chars)
    queried = sum(1 for ch in all_chars if ch in readings)
    empty = sum(1 for ch in all_chars if ch in readings and _is_empty(readings[ch]))
    print(f'\n=== 统计（共 {total} 个字）===')
    print(f'  已查：   {queried}')
    print(f'  空结果： {empty}')
    print(f'  未查：   {total - queried}')
    print(f'  错误：   {err_count[0]}')
    print(f'输出：{READINGS_PATH}')


if __name__ == '__main__':
    main()
