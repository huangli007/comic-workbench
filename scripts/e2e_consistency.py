#!/usr/bin/env python3
"""MVP-2 角色一致性端到端真机验证。

流程：建项目 → 故事拆解 → 生成角色设定图(Character Bible) → 带参考图生成分镜格
     → 选择 → 排版 → 导出。

前提：工作台已在 127.0.0.1:8770 运行（./scripts/run.sh）。
沙箱注意：需摘掉 python 的 HTTP_PROXY（见 scripts/e2e_demo.py 头部说明）。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8770"

STORY = (
    "雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」"
    "陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」"
    "两人并肩走过空无一人的街道。林夏看着地面，沉默良久。"
)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def wait_job(job_id, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for j in call("GET", "/api/jobs"):
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(3)
    raise TimeoutError(f"job {job_id} 超时")


print("① 建项目 + 拆解分镜")
pid = call("POST", "/api/projects", {"name": "雨夜一致性", "style": "manga_bw"})["id"]
job = call("POST", f"/api/projects/{pid}/storyboard",
           {"story": STORY, "pages": 1, "panels_per_page": 3, "use_llm": False})
j = wait_job(job["id"])
assert j["status"] == "done", j["error"]
print("   ", j["result_dict"])

detail = call("GET", f"/api/projects/{pid}")
chars = detail["characters"]
print("   角色：", [(c["name"], c["id"]) for c in chars])
assert chars, "没有抽出角色"
lin = next((c for c in chars if c["name"] == "林夏"), chars[0])

print(f"② 生成角色设定图（{lin['name']}，三视图，768×1024/6 步，约 2-4 分钟）")
job = call("POST", f"/api/characters/{lin['id']}/sheet",
           {"count": 1, "view": "multi", "steps": 6})
j = wait_job(job["id"])
print("   ", j["status"], j["result_dict"] or j["error"][:300])
assert j["status"] == "done", j["error"]

# 补外貌设定（有描述才能进 prompt，也便于人工复核）
call("PUT", f"/api/characters/{lin['id']}",
     {"name": lin["name"], "appearance": "a teenage girl with short black bob hair, brown eyes, wearing a dark school blazer uniform with a ribbon"})

print("③ 带角色参考图生成分镜格（640×960/6 步，约 2 分钟/张）")
detail = call("GET", f"/api/projects/{pid}")
panel1 = detail["pages"][0]["panels"][0]
# 把主角色挂到格子上（故事拆解已按名字挂好，这里确保主角在场）
call("PUT", f"/api/panels/{panel1['id']}", {"characters": [lin["id"]]})

job = call("POST", f"/api/panels/{panel1['id']}/draft",
           {"count": 1, "provider": "", "use_references": True})
j = wait_job(job["id"])
print("   ", j["status"], j["result_dict"] or j["error"][:300])
assert j["status"] == "done", j["error"]

print("④ 选择候选 → 排版渲染 → 导出 PDF")
detail = call("GET", f"/api/projects/{pid}")
panels = [p for pg in detail["pages"] for p in pg["panels"]]
for p in panels:
    if p["candidates"]:
        call("POST", f"/api/candidates/{p['candidates'][0]['id']}/select")
page = detail["pages"][0]
print("   ", call("POST", f"/api/pages/{page['id']}/render")["url"])
job = call("POST", f"/api/projects/{pid}/export", {"fmt": "pdf"})
j = wait_job(job["id"])
assert j["status"] == "done", j["error"]
print("   ", j["result_dict"]["files"])

# 校验候选记录里确实带了参考图
detail = call("GET", f"/api/projects/{pid}")
refs_used = []
for pg in detail["pages"]:
    for p in pg["panels"]:
        for c in p["candidates"]:
            refs_used += json.loads(c["params"] or "{}").get("references", [])
print("\n✅ 角色一致性链路通过；候选使用的参考图：", refs_used)
assert refs_used, "候选记录中没有参考图 —— 一致性链路未生效"
