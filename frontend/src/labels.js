// 全站中文标签映射：接口仍传英文枚举，界面只显示中文
export const CAMERA = {
  wide_shot: '远景', medium_shot: '中景', close_up: '特写', extreme_close_up: '大特写',
  low_angle: '仰视', high_angle: '俯视', over_shoulder: '过肩', birds_eye: '鸟瞰', pov: '主观视角',
}
export const EMOTION = {
  calm: '平静', happy: '开心', sad: '悲伤', angry: '愤怒',
  shocked: '震惊', fearful: '恐惧', silent: '沉默', smiling: '微笑', crying: '哭泣', tearful: '含泪',
}
export const DIALOGUE_TYPE = {
  speech: '对白', thought: '内心独白', shout: '喊叫', whisper: '低语',
  narration: '旁白', caption: '说明框', sfx: '拟声词',
}
export const LIGHTING = { day: '白天', night: '夜晚', sunset: '黄昏', indoor: '室内', dramatic: '戏剧光' }
export const PANEL_STATUS = {
  draft: '草稿', generating: '生成中', review: '待挑选',
  approved: '已选定', final: '完稿', exported: '已导出',
}
export const JOB_TYPE = {
  storyboard: '故事拆解', character_sheet: '角色设定图', panel_draft: '分镜草稿',
  panel_final: '高清重绘', panel_inpaint: '局部重绘', page_render: '页面渲染', export: '导出',
}
export const JOB_STATUS = { pending: '排队中', running: '执行中', done: '已完成', failed: '失败' }
export const STAGE = { draft: '草稿', final: '高清', inpaint: '局部重绘', manual: '人工修改' }
export const STYLE = {
  manhua_color: '国漫彩色（默认）', manga_color: '日漫彩色', webtoon: '韩式条漫',
  cinematic: '电影感插画', manga_bw: '黑白漫画',
}
export const COLOR_STYLES = ['manhua_color', 'manga_color', 'webtoon', 'cinematic']
export const PROVIDER = {
  mlx: '本机 MLX（文生图）',
  'mlx-edit': '本机 MLX（参考图条件）',
  comfyui: 'ComfyUI',
  mock: '测试用假引擎',
}
export const ASSET_KIND = { character: '人物', scene: '场景', prop: '道具' }
export const FONT = { heiti: '黑体（默认）', songti: '宋体', light: '细黑体', unicode: '系统通用字体' }

export function label(map, key, fallback = '—') {
  if (key === undefined || key === null || key === '') return fallback
  return map[key] || key
}
