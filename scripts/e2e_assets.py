#!/usr/bin/env python3
"""资产库一致性端到端真机验证（人物 + 场景 + 道具）。

流程：建项目 → 故事拆解（自动建场景资产）→ 生成场景设定图 → 建道具 + 生成道具设定图
     → 分镜引用资产 → 带参考图出图 → 排版 → 导出 PDF。

前提：工作台已在 127.0.0.1:8770 运行（./scripts/run.sh）。
沙箱注意：需摘掉 python 的 HTTP_PROXY（见 scripts/e2e_demo.py 头部说明）。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8770"

STORY = (
    "雨夜，林夏撑着黑伞站在旧图书馆前。她轻声说：「你终于来了。」"
    "她收起黑伞，推开了吱呀作响的木门。"
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


print("① 建项目 + 故事拆解（地点会自动建为场景资产）")
pid = call("POST", "/api/projects", {"name": "资产库验证", "style": "manga_bw"})["id"]
j = wait_job(call("POST", f"/api/projects/{pid}/storyboard",
                  {"story": STORY, "pages": 1, "panels_per_page": 2, "use_llm": False})["id"])
assert j["status"] == "done", j["error"]
print("   拆解结果：", j["result_dict"])

detail = call("GET", f"/api/projects/{pid}")
scenes = detail["assets"]["scene"]
chars = detail["characters"]
print(f"   人物 {len(chars)} 个；场景 {len(scenes)} 个：", [s["name"] for s in scenes])
assert scenes, "地点应自动建为场景资产"

print("② 补人物外貌 + 生成人物设定图")
if chars:
    c0 = chars[0]
    call("PUT", f"/api/characters/{c0['id']}",
         {"name": c0["name"],
          "appearance": "a teenage girl with short black bob hair, dark school uniform with ribbon"})
    j = wait_job(call("POST", f"/api/characters/{c0['id']}/sheet",
                      {"count": 1, "view": "multi", "steps": 6})["id"])
    print("   人物设定图：", j["status"], (j["result_dict"] or {}).get("reference_image", j["error"][:200]))

print("③ 场景设定图（全景）")
scene = scenes[0]
call("PUT", f"/api/assets/{scene['id']}",
     {"description": "an old two-storey library with cracked stone steps, wooden door, "
                     "rain-soaked street in front, night"})
j = wait_job(call("POST", f"/api/assets/{scene['id']}/sheet",
                  {"count": 1, "view": "wide", "steps": 6})["id"])
print("   场景设定图：", j["status"], (j["result_dict"] or {}).get("reference_image", j["error"][:200]))

print("④ 新增道具「黑伞」并生成道具设定图")
prop = call("POST", f"/api/projects/{pid}/assets",
            {"kind": "prop", "name": "黑伞",
             "description": "a black folding umbrella with a curved wooden handle"})
j = wait_job(call("POST", f"/api/assets/{prop['id']}/sheet",
                  {"count": 1, "view": "multi", "steps": 6})["id"])
print("   道具设定图：", j["status"], (j["result_dict"] or {}).get("reference_image", j["error"][:200]))

print("⑤ 分镜引用资产（人物 + 场景 + 道具）→ 带参考图出图")
detail = call("GET", f"/api/projects/{pid}")
panel = detail["pages"][0]["panels"][0]
body = {"prop_ids": [prop["id"]], "scene_id": scene["id"]}
if chars:
    body["characters"] = [chars[0]["id"]]
call("PUT", f"/api/panels/{panel['id']}", body)
j = wait_job(call("POST", f"/api/panels/{panel['id']}/draft",
                  {"count": 1, "provider": "", "use_references": True})["id"])
print("   出图：", j["status"], (j["result_dict"] or j["error"][:300]))

print("⑥ 选图 → 排版 → 导出 PDF")
detail = call("GET", f"/api/projects/{pid}")
for pg in detail["pages"]:
    for p in pg["panels"]:
        if p["candidates"]:
            call("POST", f"/api/candidates/{p['candidates'][0]['id']}/select")
for pg in detail["pages"]:
    print("   渲染：", call("POST", f"/api/pages/{pg['id']}/render")["url"])
j = wait_job(call("POST", f"/api/projects/{pid}/export", {"fmt": "pdf"})["id"])
assert j["status"] == "done", j["error"]

# 复核：候选记录里应该同时出现 人物 + 场景 + 道具 的参考图
detail = call("GET", f"/api/projects/{pid}")
panel = detail["pages"][0]["panels"][0]
refs = json.loads(panel["candidates"][0]["params"])["references"]
print("\n✅ 资产库链路通过")
print("   分镜引用：场景 =", panel["scene_name"], "| 道具 =", panel["prop_names"])
print("   候选参考图：")
for r in refs:
    print("     -", r)
assets = call("GET", f"/api/projects/{pid}/assets")
print("   引用计数：", {a["name"]: a["usage"] for a in assets["scene"] + assets["prop"]})
assert len(refs) >= 2, "应至少挂上场景/道具之一的参考图"
