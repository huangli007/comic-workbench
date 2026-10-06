#!/usr/bin/env python3
"""彩色画风端到端验证：切换画风 → 重新生成参考图（人物/场景）→ 带参考图出图 → 成页。

用法：先启动工作台，再
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy NO_PROXY="127.0.0.1,localhost" \
      python scripts/e2e_color.py [项目名]
不带参数则新建一个彩色项目跑全流程。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8770"
STYLE = "manhua_color"   # 国漫彩色

STORY = "雨夜，林夏撑着黑伞站在旧图书馆前。她轻声说：「你终于来了。」"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def wait(job_id, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for j in call("GET", "/api/jobs"):
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(3)
    raise TimeoutError(job_id)


name = sys.argv[1] if len(sys.argv) > 1 else None
if name:
    pid = [p for p in call("GET", "/api/projects") if p["name"] == name][0]["id"]
    print(f"复用项目 {name} ({pid})")
else:
    pid = call("POST", "/api/projects", {"name": "彩色漫画", "style": STYLE})["id"]
    print("① 建彩色项目", pid)
    j = wait(call("POST", f"/api/projects/{pid}/storyboard",
                  {"story": STORY, "pages": 1, "panels_per_page": 2, "use_llm": False})["id"])
    print("   拆解：", j["result_dict"])

print(f"② 切换画风为 {STYLE}")
call("PUT", f"/api/projects/{pid}", {"style": STYLE})

detail = call("GET", f"/api/projects/{pid}")
chars = detail["characters"]
scenes = detail["assets"]["scene"]
assert chars and scenes, "需要至少一个人物和一个场景"

print("③ 重新生成人物设定图（彩色）")
c = chars[0]
call("PUT", f"/api/characters/{c['id']}", {"name": c["name"],
     "appearance": c["appearance"] or "a teenage girl with short black bob hair, dark blue school uniform with ribbon"})
j = wait(call("POST", f"/api/characters/{c['id']}/sheet", {"count": 1, "view": "multi", "steps": 6})["id"])
print("   ", j["status"], (j["result_dict"] or {}).get("reference_image", j["error"][:200]))

print("④ 重新生成场景设定图（彩色）")
s = scenes[0]
call("PUT", f"/api/assets/{s['id']}", {"description": s["description"] or
     "an old two-storey library with cracked stone steps, wooden door, rain-soaked street, night"})
j = wait(call("POST", f"/api/assets/{s['id']}/sheet", {"count": 1, "view": "wide", "steps": 6})["id"])
print("   ", j["status"], (j["result_dict"] or {}).get("reference_image", j["error"][:200]))

print("⑤ 带彩色参考图重新出图")
detail = call("GET", f"/api/projects/{pid}")
panel = detail["pages"][0]["panels"][0]
call("PUT", f"/api/panels/{panel['id']}", {"characters": [c["id"]], "scene_id": s["id"]})
j = wait(call("POST", f"/api/panels/{panel['id']}/draft",
              {"count": 1, "provider": "", "use_references": True})["id"])
print("   ", j["status"], (j["result_dict"] or j["error"][:300]))

print("⑥ 选图 → 排版 → 导出 PDF")
detail = call("GET", f"/api/projects/{pid}")
for pg in detail["pages"]:
    for p in pg["panels"]:
        if p["candidates"]:
            call("POST", f"/api/candidates/{p['candidates'][-1]['id']}/select")
for pg in detail["pages"]:
    print("   渲染：", call("POST", f"/api/pages/{pg['id']}/render")["url"])
j = wait(call("POST", f"/api/projects/{pid}/export", {"fmt": "pdf"})["id"])
assert j["status"] == "done", j["error"]

detail = call("GET", f"/api/projects/{pid}")
panel = detail["pages"][0]["panels"][0]
refs = json.loads(panel["candidates"][-1]["params"])["references"]
print("\n✅ 彩色链路通过")
print("   画风：", detail["style"])
print("   提示词含彩色关键词：", "full color" in panel["prompt"].lower())
print("   参考图：")
for r in refs:
    print("     -", r)
