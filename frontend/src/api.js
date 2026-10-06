const BASE = ''

async function req(method, url, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const r = await fetch(BASE + url, opts)
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail || '' } catch (e) { /* ignore */ }
    throw new Error(`${r.status} ${r.statusText} ${detail}`)
  }
  return r.status === 204 ? null : r.json()
}

export const api = {
  // 项目
  listProjects: () => req('GET', '/api/projects'),
  createProject: (name, style) => req('POST', '/api/projects', { name, style }),
  getProject: (id) => req('GET', `/api/projects/${id}`),
  updateProject: (id, body) => req('PUT', `/api/projects/${id}`, body),
  upgradeStyle: (id, body) => req('POST', `/api/projects/${id}/upgrade-style`, body),
  translate: (id, langs) => req('POST', `/api/projects/${id}/translate`, { langs }),
  stats: () => req('GET', '/api/stats'),
  deleteProject: (id) => req('DELETE', `/api/projects/${id}`),
  deletePanel: (pid) => req('DELETE', `/api/panels/${pid}`),
  duplicatePanel: (pid) => req('POST', `/api/panels/${pid}/duplicate`),
  createPanel: (pageId, body) => req('POST', `/api/pages/${pageId}/panels`, body || {}),
  fonts: () => req('GET', '/api/fonts'),
  // 故事 → 分镜
  storyboard: (id, payload) => req('POST', `/api/projects/${id}/storyboard`, payload),
  // 角色
  listCharacters: (id) => req('GET', `/api/projects/${id}/characters`),
  createCharacter: (id, name, appearance) =>
    req('POST', `/api/projects/${id}/characters`, { name, appearance }),
  updateCharacter: (cid, name, appearance) =>
    req('PUT', `/api/characters/${cid}`, { name, appearance }),
  characterSheet: (cid, body) => req('POST', `/api/characters/${cid}/sheet`, body),
  // 资产库（场景 / 道具）
  listAssets: (id) => req('GET', `/api/projects/${id}/assets`),
  createAsset: (id, body) => req('POST', `/api/projects/${id}/assets`, body),
  updateAsset: (aid, body) => req('PUT', `/api/assets/${aid}`, body),
  deleteAsset: (aid) => req('DELETE', `/api/assets/${aid}`),
  trainLora: (cid, body) => req('POST', `/api/characters/${cid}/train-lora`, body),
  assetSheet: (aid, body) => req('POST', `/api/assets/${aid}/sheet`, body),
  assetKinds: () => req('GET', '/api/asset-kinds'),
  // 分镜格
  updatePanel: (pid, body) => req('PUT', `/api/panels/${pid}`, body),
  draftPanel: (pid, body) => req('POST', `/api/panels/${pid}/draft`, body),
  finalPanel: (pid, body) => req('POST', `/api/panels/${pid}/final`, body),
  selectCandidate: (cid) => req('POST', `/api/candidates/${cid}/select`),
  inpaint: (pid, body) => req('POST', `/api/panels/${pid}/inpaint`, body),
  externalExport: (pid, body) => req('POST', `/api/panels/${pid}/external-export`, body),
  externalImport: (pid) => req('POST', `/api/panels/${pid}/external-import`),
  renderPage: (pageId, body) => req('POST', `/api/pages/${pageId}/render`, body || {}),
  batchDraft: (pid, body) => req('POST', `/api/projects/${pid}/batch-draft`, body),
  scorePanel: (pid, body) => req('POST', `/api/panels/${pid}/score`, body),
  scoreProject: (pid, body) => req('POST', `/api/projects/${pid}/score-candidates`, body),
  panelRanking: (pid) => req('GET', `/api/panels/${pid}/ranking`),
  autoSelectBest: (pid) => req('POST', `/api/panels/${pid}/auto-select-best`),
  autoSelectBestProject: (pid) => req('POST', `/api/projects/${pid}/auto-select-best`, {}),
  listExports: (pid) => req('GET', `/api/projects/${pid}/exports`),
  regenerateAsset: (aid, body) => req('POST', `/api/assets/${aid}/regenerate`, body),
  regenerateCharacter: (cid, body) => req('POST', `/api/characters/${cid}/regenerate`, body),
  // 导出
  exportProject: (id, fmt) => req('POST', `/api/projects/${id}/export`, { fmt }),
  // 任务
  listJobs: (projectId) =>
    req('GET', `/api/jobs${projectId ? `?project_id=${projectId}` : ''}`),
  // 元数据
  providers: () => req('GET', '/api/providers'),
  styles: () => req('GET', '/api/styles'),   // {groups, default, styles[]}
}
