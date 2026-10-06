#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HF 元数据 + ModelScope 内容的混合下载器（国内提速用）。

背景：hf-mirror 对某些大 repo 限速到 ~87KB/s（15GB 要 50 小时，不可用），
而 ModelScope 同 repo 镜像速度 ~5MB/s。但 mflux / huggingface_hub 只认标准 HF 缓存布局，
所以这里：
    · 文件清单与 sha256（blob 名）从 HF API 取（轻量、秒回）
    · 文件内容从 ModelScope 下载
    · 落盘时手工构造 HF 缓存布局（blobs/<sha256> + snapshots/<commit>/<file> 符号链接 + refs/main）
这样 huggingface_hub 的离线完整性检查会认为缓存完整，mflux 可直接加载。

用法：
    python scripts/ms_hf_download.py <repo> [--files a.safetensors,b.safetensors] [--jobs 3]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
MS_ENDPOINT = os.environ.get("MS_ENDPOINT", "https://modelscope.cn")
HUB = Path.home() / ".cache" / "huggingface" / "hub"


def human(n: float) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def hf_files(repo: str) -> tuple[str, list[dict]]:
    url = f"{HF_ENDPOINT}/api/models/{repo}?blobs=true"
    out = subprocess.run(["curl", "-sL", "-m", "60", url], capture_output=True, text=True)
    info = json.loads(out.stdout)
    commit = info["sha"]
    files = []
    for s in info.get("siblings", []):
        etag = (s.get("lfs") or {}).get("sha256") or s.get("blobId")
        if not etag:
            continue
        files.append({"name": s["rfilename"], "size": s.get("size") or 0, "etag": etag})
    return commit, files


def ms_url(repo: str, name: str) -> str:
    return (f"{MS_ENDPOINT}/api/v1/models/{repo}/repo"
            f"?Revision=master&FilePath={urllib.parse.quote(name)}")


def download(url: str, dest: Path, size: int, retries: int = 6) -> bool:
    """curl 断点续传到目标路径；返回是否大小正确。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries + 1):
        cmd = ["curl", "-sL", "--retry", "3", "--retry-delay", "3",
               "--connect-timeout", "30", "--max-time", "7200",
               "-C", "-", "-o", str(dest), url]
        subprocess.run(cmd)
        got = dest.stat().st_size if dest.exists() else 0
        if size == 0 or got == size:
            return True
        print(f"      retry {attempt}/{retries}  {human(got)}/{human(size)}", flush=True)
        time.sleep(3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--files", default="", help="逗号分隔的文件名白名单（默认全部）")
    ap.add_argument("--jobs", type=int, default=3, help="并行下载数")
    ap.add_argument("--min-size-mb", type=float, default=0, help="只下大于该大小的文件")
    args = ap.parse_args()

    repo = args.repo
    only = [x.strip() for x in args.files.split(",") if x.strip()]
    commit, files = hf_files(repo)
    if only:
        files = [f for f in files if f["name"] in only]
    if args.min_size_mb:
        files = [f for f in files if f["size"] >= args.min_size_mb * 1024 * 1024]

    safe = "models--" + repo.replace("/", "--")
    base = HUB / safe
    blobs = base / "blobs"
    snap = base / "snapshots" / commit
    blobs.mkdir(parents=True, exist_ok=True)
    snap.mkdir(parents=True, exist_ok=True)

    todo = []
    for f in files:
        blob = blobs / f["etag"]
        target = snap / f["name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if blob.exists() and blob.stat().st_size == f["size"]:
            if not target.exists():
                target.symlink_to(os.path.relpath(blob, target.parent))
            print(f"  cached  {human(f['size']):>10}  {f['name']}", flush=True)
        else:
            if blob.exists() and blob.stat().st_size != f["size"]:
                blob.unlink()          # 残缺分片：删掉重下（curl -C - 会续传，但大小对不上说明坏了）
            todo.append((f, blob, target))

    print(f"\n>>> 需要下载 {len(todo)} 个文件，共 {human(sum(f['size'] for f, _, _ in todo))}"
          f"（并行 {args.jobs}）\n", flush=True)

    lock = threading.Lock()
    done = {"bytes": 0, "files": 0, "failed": []}
    t0 = time.time()

    def worker(items):
        for f, blob, target in items:
            print(f"  get     {human(f['size']):>10}  {f['name']}", flush=True)
            ok = download(ms_url(repo, f["name"]), blob, f["size"])
            with lock:
                if ok:
                    if not target.exists():
                        target.symlink_to(os.path.relpath(blob, target.parent))
                    done["bytes"] += f["size"]
                    done["files"] += 1
                    el = time.time() - t0
                    print(f"  ok      {f['name']}  [{done['files']}/{len(todo)}]"
                          f"  累计 {human(done['bytes'])}  {el:.0f}s", flush=True)
                else:
                    done["failed"].append(f["name"])
                    print(f"  FAIL    {f['name']}", flush=True)

    # 大文件优先，均衡分配到各 worker（贪心装箱）
    todo.sort(key=lambda x: -x[0]["size"])
    buckets = [[] for _ in range(max(1, args.jobs))]
    loads = [0] * len(buckets)
    for item in todo:
        i = loads.index(min(loads))
        buckets[i].append(item)
        loads[i] += item[0]["size"]

    threads = [threading.Thread(target=worker, args=(b,)) for b in buckets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # refs/main
    refs = base / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(commit)

    el = time.time() - t0
    print(f"\n完成：{done['files']}/{len(todo)} 个文件，{human(done['bytes'])}，用时 {el:.0f}s"
          f"（均速 {human(done['bytes'] / max(el, 1))}/s）")
    if done["failed"]:
        print(f"失败：{done['failed']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
