#!/usr/bin/env python3
"""端到端真实验证脚本：故事 → 分镜 → MLX 真实出图 → 选择 → 排版 → 导出。

前提：工作台已在 127.0.0.1:8770 运行（./scripts/run.sh）。
注意：本机沙箱给 python 注入了 HTTP_PROXY，打 localhost 会被拦成 502，
     需这样运行：
     env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
         NO_PROXY="127.0.0.1,localhost" python scripts/e2e_demo.py
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8770"

STORY = (
    "雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」"
    "陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」"
    "两人并肩走过空无一人的街道。林夏看着地面，沉默良久。"
    "在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」"
    "陈默震惊地看着她，雨水顺着他的头发流下。"
    "林夏转身离去，只留下陈默一个人站在雨中。"
)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def wait_job(job_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        jobs = call("GET", "/api/jobs")
        for j in jobs:
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(2)
    raise TimeoutError(f"job {job_id} 超时")


print("① 建项目")
proj = call("POST", "/api/projects", {"name": "雨夜", "style": "manga_bw"})
pid = proj["id"]
print("   project:", pid)

print("② 故事 → 分镜（启发式，秒出）")
job = call("POST", f"/api/projects/{pid}/storyboard",
           {"story": STORY, "pages": 2, "panels_per_page": 3, "use_llm": False})
j = wait_job(job["id"])
assert j["status"] == "done", j["error"]
print("   ", j["result_dict"])

detail = call("GET", f"/api/projects/{pid}")
panels = [p for pg in detail["pages"] for p in pg["panels"]]
print(f"   共 {len(panels)} 格；第一格：{panels[0]['action'][:30]}… 对白={panels[0]['dialogue_list']}")

print("③ 真实 MLX 生成候选图（3 格 × 1 张，640×960）")
targets = panels[:3]
for p in targets:
    job = call("POST", f"/api/panels/{p['id']}/draft",
               {"count": 1, "provider": "mlx", "steps": 6})
    j = wait_job(job["id"])
    status = j["status"]
    print(f"   panel {p['index']}: {status} {j['result_dict'] or j['error'][:200]}")
    assert status == "done", j["error"]

print("④ 选择每格首个候选")
detail = call("GET", f"/api/projects/{pid}")
panels = [p for pg in detail["pages"] for p in pg["panels"]]
for p in panels[:3]:
    cand = p["candidates"][0]
    call("POST", f"/api/candidates/{cand['id']}/select")
print("   已选 3 格")

print("⑤ 渲染全部页面（排版 + CJK 气泡）")
for pg in detail["pages"]:
    r = call("POST", f"/api/pages/{pg['id']}/render")
    print("   ", r["url"])

print("⑥ 导出 PDF / CBZ / PNG")
for fmt in ("pdf", "cbz", "png"):
    job = call("POST", f"/api/projects/{pid}/export", {"fmt": fmt})
    j = wait_job(job["id"], timeout=300)
    print(f"   {fmt}: {j['status']} → {j['result_dict'].get('files')}")
    assert j["status"] == "done", j["error"]

print("\n✅ 全链路通过")
