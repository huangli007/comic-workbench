#!/usr/bin/env python3
"""把老黑白项目一键升级彩色（真实生成），完成后渲染 + 导出 PDF。"""
import json, sys, time, urllib.request

B = "http://127.0.0.1:8770"
STYLE = "manhua_color"

def call(m, p, b=None):
    d = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(B + p, data=d, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=600).read().decode())

def wait(jid, t=3600):
    t0 = time.time()
    while time.time() - t0 < t:
        for j in call("GET", "/api/jobs"):
            if j["id"] == jid and j["status"] in ("done", "failed"):
                return j
        time.sleep(5)
    raise TimeoutError(jid)

targets = [p for p in call("GET", "/api/projects")
           if p["name"] in ("雨夜", "雨夜一致性") and p["style"] != STYLE]
print("待升级项目:", [p["name"] for p in targets])

for proj in targets:
    pid = proj["id"]
    print(f"\n=== 升级「{proj['name']}」→ {STYLE}（资产 {proj.get('asset_count')} · 分镜 {proj['panel_count']}）===")
    t0 = time.time()
    j = wait(call("POST", f"/api/projects/{pid}/upgrade-style", {"style": STYLE})["id"])
    if j["status"] != "done":
        print("  失败:", j["error"][:300]); continue
    r = j["result_dict"]
    print(f"  完成 {time.time()-t0:.0f}s | 设定图 {r['character_sheets']}+{r['asset_sheets']} | 重跑分镜 {r['panels_rerun']}")

    # 选每格最高分（或最新）候选 → 渲染 → 导出
    d = call("GET", f"/api/projects/{pid}")
    for pg in d["pages"]:
        for p in pg["panels"]:
            if p["candidates"]:
                best = max(p["candidates"], key=lambda c: (c.get("score") or 0))
                call("POST", f"/api/candidates/{best['id']}/select")
    for pg in d["pages"]:
        call("POST", f"/api/pages/{pg['id']}/render", {"lang": ""})
    j = wait(call("POST", f"/api/projects/{pid}/export", {"fmt": "pdf", "lang": ""})["id"])
    print("  导出:", j["result_dict"]["files"])

print("\n全部升级完成")
