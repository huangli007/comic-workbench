<template>
  <div class="page">
    <el-page-header @back="router.push('/')" :content="project?.name || '加载中…'">
      <template #extra>
        <el-space>
          <span class="muted">画风</span>
          <el-select v-model="style" size="small" style="width:230px" filterable @change="saveStyle">
            <el-option-group v-for="g in styleGroups" :key="g.name" :label="g.name">
              <el-option v-for="s in g.items" :key="s.key"
                         :label="s.label + (s.color ? '' : '（黑白）')" :value="s.key">
                <span>{{ s.label }}</span>
                <span class="muted" style="float:right;margin-left:8px">{{ s.color ? '彩色' : '黑白' }}</span>
              </el-option>
            </el-option-group>
          </el-select>
          <el-tag effect="plain" :type="isColor ? 'success' : 'info'">
            {{ isColor ? '彩色' : '黑白' }}
          </el-tag>
          <el-tooltip v-if="currentStyleDesc" :content="currentStyleDesc" placement="bottom">
            <span class="muted" style="cursor:help">风格说明</span>
          </el-tooltip>
          <span class="muted">生成引擎</span>
          <el-select v-model="engine" size="small" style="width:210px">
            <el-option label="自动（有参考图则用参考图）" value="auto" />
            <el-option label="本机 MLX · 文生图" value="mlx" />
            <el-option label="本机 MLX · 参考图条件" value="mlx-edit" />
            <el-option label="ComfyUI" value="comfyui" />
            <el-option label="测试用假引擎" value="mock" />
          </el-select>
          <template v-if="engine === 'comfyui' && checkpoints.length">
            <span class="muted">模型</span>
            <el-select v-model="ckpt" size="small" style="width:230px" placeholder="选择 ComfyUI 模型">
              <el-option v-for="c in checkpoints" :key="c" :label="c" :value="c" />
            </el-select>
          </template>
        </el-space>
      </template>
    </el-page-header>

    <el-tabs v-model="tab" style="margin-top:12px" class="panel-card">
      <!-- ═══ 故事 → 分镜 ═══ -->
      <el-tab-pane label="故事拆解" name="story">
        <div style="padding:16px">
          <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
                    title="粘贴一段中文故事，自动拆解为结构化分镜（人物 / 镜头 / 动作 / 情绪 / 对白），可直接用于出图。" />
          <el-input v-model="story" type="textarea" :rows="5"
                    placeholder="例如：雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」……" />
          <div style="margin-top:10px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
            <span class="muted">篇幅</span>
            <el-input-number v-model="pages" :min="1" :max="20" />
            <span class="muted">页 ×</span>
            <el-input-number v-model="perPage" :min="1" :max="6" />
            <span class="muted">格/页</span>
            <el-checkbox v-model="useLlm">用本机大模型拆解（关闭则用规则拆解，秒出）</el-checkbox>
            <el-button type="primary" :loading="busy" @click="doStoryboard">生成分镜</el-button>
            <el-button @click="refresh">刷新</el-button>
          </div>
          <el-alert type="warning" :closable="false" style="margin-top:12px"
                    title="提示：重新生成分镜会重建分镜格，但已生成设定图的角色会按名字保留；候选图文件不会被删除。" />
        </div>
      </el-tab-pane>

      <!-- ═══ 分镜格编辑 ═══ -->
      <el-tab-pane :label="`分镜格（${panels.length}）`" name="panels">
        <div style="padding:16px">
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
            <el-button size="small" type="primary" plain @click="renderAllPages">渲染全部页面（排版 + 中文气泡）</el-button>
            <el-divider direction="vertical" />
            <span class="muted">批量出图</span>
            <el-select v-model="batchScope" size="small" style="width:150px">
              <el-option label="整个项目" value="" />
              <el-option v-for="pg in project?.pages || []" :key="pg.id"
                         :label="`第 ${pg.index} 页`" :value="pg.id" />
            </el-select>
            <el-select v-model="batchCount" size="small" style="width:80px">
              <el-option :value="1" label="1 张" /><el-option :value="2" label="2 张" />
              <el-option :value="3" label="3 张" />
            </el-select>
            <el-checkbox v-model="batchOnlyMissing" size="small">仅未出图的格</el-checkbox>
            <el-checkbox v-model="useRefs" size="small">带参考图</el-checkbox>
            <el-button size="small" type="success" :loading="busy" @click="doBatchDraft">批量生成候选</el-button>
            <el-button size="small" @click="doAddPanel">+ 新增空格</el-button>
            <el-divider direction="vertical" />
            <span class="muted">质量评分</span>
            <el-checkbox v-model="useVlm" size="small">VLM 点评（慢，每张数十秒）</el-checkbox>
            <el-button size="small" :loading="busy" @click="doBatchScore">批量评分</el-button>
            <el-button size="small" type="warning" plain :loading="busy"
                       @click="doAutoSelectAll">整项目自动选最佳</el-button>
          </div>
          <div class="muted" style="margin-bottom:12px">
            在分镜图上拖拽可框选区域 → 局部重绘；未框选时可整图送到外部编辑器精修
          </div>
          <el-row :gutter="16">
            <el-col v-for="pnl in panels" :key="pnl.id" :span="8" style="margin-bottom:16px">
              <el-card shadow="hover" class="panel-card">
                <template #header>
                  <div style="display:flex;justify-content:space-between;align-items:center">
                    <span>第 {{ pnl.index }} 格 <span class="muted">第 {{ pageIndexOf(pnl) }} 页</span></span>
                    <el-space size={4}>
                      <el-tag size="small" :type="statusType(pnl.status)">{{ label(PANEL_STATUS, pnl.status) }}</el-tag>
                      <el-button size="small" text @click="doDuplicate(pnl)" title="复制这一格">复制</el-button>
                      <el-button size="small" text type="danger" @click="doDeletePanel(pnl)" title="删除这一格">删除</el-button>
                    </el-space>
                  </div>
                </template>

                <div v-if="currentImage(pnl)" class="img-wrap"
                     @mousedown="startSel(pnl, $event)" @mousemove="moveSel(pnl, $event)"
                     @mouseup="endSel(pnl)" @mouseleave="endSel(pnl)">
                  <img :src="currentImage(pnl)" class="panel-img" draggable="false" />
                  <div v-if="selOf(pnl)" class="sel-rect" :style="rectStyle(pnl)"></div>
                </div>
                <div v-else style="height:140px;background:#f0f0f0;border-radius:4px;display:flex;align-items:center;justify-content:center" class="muted">
                  还没有图像，点下方「生成候选」
                </div>

                <div style="margin-top:10px">
                  <div class="muted">镜头 / 情绪</div>
                  <div style="display:flex;gap:6px;margin:4px 0">
                    <el-select v-model="pnl.camera" size="small" @change="save(pnl)" style="flex:1">
                      <el-option v-for="(v, k) in CAMERA" :key="k" :label="v" :value="k" />
                    </el-select>
                    <el-select v-model="pnl.emotion" size="small" @change="save(pnl)" style="flex:1">
                      <el-option v-for="(v, k) in EMOTION" :key="k" :label="v" :value="k" />
                    </el-select>
                  </div>
                  <el-input v-model="pnl.action" size="small" @blur="save(pnl)"
                            placeholder="画面动作（中文，决定画面内容）" />

                  <div class="muted" style="margin-top:8px">资产库引用（一致性）</div>
                  <div style="display:flex;gap:6px;margin:4px 0">
                    <el-select v-model="pnl.scene_id" size="small" clearable placeholder="场景"
                               @change="save(pnl)" style="flex:1">
                      <el-option v-for="a in sceneAssets" :key="a.id" :label="a.name" :value="a.id" />
                    </el-select>
                    <el-select v-model="pnl.prop_ids_list" size="small" multiple collapse-tags
                               placeholder="道具（可多选）" @change="save(pnl)" style="flex:1">
                      <el-option v-for="a in propAssets" :key="a.id" :label="a.name" :value="a.id" />
                    </el-select>
                  </div>
                  <el-input v-model="pnl.location" size="small"
                            @blur="save(pnl)" placeholder="地点文字（未建场景资产时兜底）" />

                  <el-collapse style="margin-top:8px">
                    <el-collapse-item title="对白 / 文字框" :name="pnl.id">
                      <div v-for="(d, di) in pnl.dialogue_list" :key="di" class="dialogue-item">
                        <el-input v-model="d.speaker" size="small" placeholder="说话者"
                                  style="width:100px;margin-bottom:4px" @blur="saveDialogue(pnl)" />
                        <el-input v-model="d.text" size="small" placeholder="中文台词（排版时生成文字框）"
                                  @blur="saveDialogue(pnl)" />
                        <div style="display:flex;gap:6px;margin-top:4px">
                          <el-select v-model="d.type" size="small" @change="saveDialogue(pnl)" style="flex:1">
                            <el-option v-for="(v, k) in DIALOGUE_TYPE" :key="k" :label="v" :value="k" />
                          </el-select>
                          <el-button size="small" type="danger" text
                                     @click="pnl.dialogue_list.splice(di,1); saveDialogue(pnl)">删除</el-button>
                        </div>
                      </div>
                      <el-button size="small" text type="primary"
                                 @click="pnl.dialogue_list.push({speaker:'',text:'',type:'speech'}); saveDialogue(pnl)">
                        + 添加对白
                      </el-button>
                      <div class="muted" style="margin-top:6px">
                        文字框由排版引擎绘制（不进图像模型），字体与字号在「排版 / 导出」页设置
                      </div>
                    </el-collapse-item>
                    <el-collapse-item title="提示词 / 生成参数" :name="pnl.id + 'p'">
                      <el-input v-model="pnl.prompt" type="textarea" :rows="3" size="small"
                                placeholder="留空则按画风+人物+动作+镜头模板自动拼装" @blur="save(pnl)" />
                      <div class="muted" style="margin-top:6px">随机种子：{{ pnl.seed }}</div>
                    </el-collapse-item>
                  </el-collapse>

                  <div style="margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                    <el-button size="small" type="primary" :loading="busy"
                               @click="doDraft(pnl)">生成候选图</el-button>
                    <el-select v-model="draftCount" size="small" style="width:76px">
                      <el-option :value="1" label="1 张" /><el-option :value="2" label="2 张" />
                      <el-option :value="3" label="3 张" /><el-option :value="4" label="4 张" />
                    </el-select>
                    <el-button size="small" type="success" plain :loading="busy"
                               @click="doFinal(pnl)">高清重绘</el-button>
                    <el-checkbox v-model="useRefs" size="small">带角色参考图</el-checkbox>
                  </div>

                  <div style="margin-top:6px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                    <el-input v-model="inpaintPrompt[pnl.id]" size="small" style="width:150px"
                              placeholder="选区内画什么（可留空）" />
                    <el-button size="small" :disabled="!selOf(pnl)" :loading="busy"
                               @click="doInpaint(pnl)">局部重绘</el-button>
                    <el-button size="small" text @click="clearSel(pnl)">清除选区</el-button>
                    <el-button size="small" plain :loading="busy"
                               @click="doExternalExport(pnl)">外部编辑</el-button>
                    <el-button size="small" plain :loading="busy"
                               @click="doExternalImport(pnl)">回填</el-button>
                    <el-button size="small" :loading="busy" @click="doScorePanel(pnl)">评分排序</el-button>
                    <el-button size="small" type="warning" plain :loading="busy"
                               @click="doAutoSelect(pnl)">自动选最佳</el-button>
                  </div>

                  <div v-if="pnl.candidates?.length" style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
                    <div v-for="c in pnl.candidates" :key="c.id" style="text-align:center;position:relative">
                      <img :src="c.url" class="candidate-thumb"
                           :class="{ selected: pnl.selected_candidate_id === c.id }"
                           @click="pick(pnl, c)"
                           :title="scoreTitle(c) || `种子 ${c.seed} · ${label(STAGE, c.stage)}`" />
                      <div v-if="c.score" class="score-badge" :class="scoreClass(c.score)">
                        {{ Math.round(c.score) }}
                      </div>
                      <div v-if="pnl.best_candidate_id === c.id" class="best-flag">最佳</div>
                      <div class="muted" style="font-size:10px">{{ label(STAGE, c.stage) }}</div>
                    </div>
                  </div>
                </div>
              </el-card>
            </el-col>
          </el-row>
          <el-empty v-if="!panels.length" description="还没有分镜格，请先到「故事拆解」生成" />
        </div>
      </el-tab-pane>

      <!-- ═══ 资产库（人物 / 场景 / 道具）═══ -->
      <el-tab-pane :label="`资产库（${assetCount}）`" name="assets">
        <div style="padding:16px">
          <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
                    title="一致性资产：人物 / 场景 / 道具。每项都可沉淀「中文名 + 英文设定 + 设定图」，分镜引用后会自动把设定写进提示词、把设定图作为参考图，跨格保持一致。" />

          <!-- 人物 -->
          <div class="asset-section-title">
            人物
            <span class="muted">（跨格同一张脸 / 同一套衣服）</span>
          </div>
          <el-row :gutter="16">
            <el-col v-for="ch in project?.characters || []" :key="ch.id" :span="8" style="margin-bottom:16px">
              <el-card shadow="hover">
                <template #header>
                  <div style="display:flex;justify-content:space-between;align-items:center">
                    <span>{{ ch.name }}</span>
                    <el-tag size="small" :type="ch.reference_urls?.length ? 'success' : 'info'">
                      {{ ch.reference_urls?.length ? `参考图 ${ch.reference_urls.length} 张` : '暂无参考图' }}
                    </el-tag>
                  </div>
                </template>
                <div v-if="ch.reference_urls?.length" style="display:flex;gap:6px;flex-wrap:wrap">
                  <img v-for="(u, i) in ch.reference_urls" :key="i" :src="u"
                       style="width:104px;border:1px solid #e4e7ed;border-radius:4px" />
                </div>
                <div v-else class="muted" style="height:60px;display:flex;align-items:center">尚未生成设定图</div>
                <el-input v-model="ch.appearance" size="small" type="textarea" :rows="2"
                          style="margin-top:8px" placeholder="英文外貌描述：black long hair, brown eyes, school uniform"
                          @blur="saveChar(ch)" />
                <div style="margin-top:8px;display:flex;gap:6px;align-items:center">
                  <el-select v-model="sheetView" size="small" style="width:120px">
                    <el-option label="三视图" value="multi" />
                    <el-option label="正面全身" value="front" />
                    <el-option label="半身像" value="bust" />
                  </el-select>
                  <el-button size="small" type="primary" :loading="busy"
                             @click="doSheet(ch)">生成设定图</el-button>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
                  <span class="muted">分镜引用：{{ ch.usage ?? 0 }} 处</span>
                  <el-button size="small" text type="primary" :disabled="!(ch.usage > 0)"
                             :loading="busy" @click="doRegenerate('character', ch)">重跑引用分镜</el-button>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:2px">
                  <span class="muted">LoRA：
                    <el-tag size="small" :type="ch.lora_path ? 'success' : 'info'" effect="plain">
                      {{ ch.lora_path ? '已训练' : '未训练' }}
                    </el-tag>
                  </span>
                  <el-button size="small" text :loading="busy" @click="doTrainLora(ch)">训练 LoRA</el-button>
                </div>
              </el-card>
            </el-col>
          </el-row>
          <div style="display:flex;gap:8px;margin-bottom:20px">
            <el-input v-model="newCharName" placeholder="人物名（如：林夏）" style="width:180px" size="small" />
            <el-input v-model="newCharApp" placeholder="英文外貌描述（进提示词）" size="small" />
            <el-button size="small" type="primary" @click="addChar">添加人物</el-button>
          </div>

          <!-- 场景 / 道具 -->
          <template v-for="grp in assetGroups" :key="grp.kind">
            <div class="asset-section-title">
              {{ grp.kind_label }}
              <span class="muted">{{ grp.hint }}</span>
            </div>
            <el-row :gutter="16">
              <el-col v-for="a in grp.items" :key="a.id" :span="8" style="margin-bottom:16px">
                <el-card shadow="hover">
                  <template #header>
                    <div style="display:flex;justify-content:space-between;align-items:center">
                      <span>{{ a.name }}</span>
                      <div>
                        <el-tag size="small" :type="a.reference_urls?.length ? 'success' : 'info'" style="margin-right:6px">
                          {{ a.reference_urls?.length ? `参考图 ${a.reference_urls.length} 张` : '暂无参考图' }}
                        </el-tag>
                        <el-button size="small" type="danger" text @click="removeAsset(a)">删除</el-button>
                      </div>
                    </div>
                  </template>
                  <div v-if="a.reference_urls?.length" style="display:flex;gap:6px;flex-wrap:wrap">
                    <img v-for="(u, i) in a.reference_urls" :key="i" :src="u"
                         style="width:104px;border:1px solid #e4e7ed;border-radius:4px" />
                  </div>
                  <div v-else class="muted" style="height:60px;display:flex;align-items:center">
                    {{ grp.kind === 'scene' ? '尚未生成场景设定图' : '尚未生成道具设定图' }}
                  </div>
                  <el-input v-model="a.description" size="small" type="textarea" :rows="2"
                            style="margin-top:8px" :placeholder="grp.placeholder"
                            @blur="saveAsset(a)" />
                  <div style="margin-top:8px;display:flex;gap:6px;align-items:center">
                    <el-select v-model="sheetViews[a.id]" size="small" style="width:120px">
                      <el-option v-for="v in grp.views" :key="v.value" :label="v.label" :value="v.value" />
                    </el-select>
                    <el-button size="small" type="primary" :loading="busy"
                               @click="doAssetSheet(a)">生成设定图</el-button>
                  </div>
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
                    <span class="muted">分镜引用：{{ a.usage ?? 0 }} 处</span>
                    <el-button size="small" text type="primary" :disabled="!(a.usage > 0)"
                               :loading="busy" @click="doRegenerate(a.kind, a)">重跑引用分镜</el-button>
                  </div>
                </el-card>
              </el-col>
            </el-row>
            <div style="display:flex;gap:8px;margin-bottom:20px">
              <el-input v-model="newAsset[grp.kind].name" :placeholder="`${grp.kind_label}名（如：${grp.example}）`"
                        style="width:220px" size="small" />
              <el-input v-model="newAsset[grp.kind].description" placeholder="英文设定描述（进提示词，可留空后补）" size="small" />
              <el-button size="small" type="primary" @click="addAsset(grp.kind)">添加{{ grp.kind_label }}</el-button>
            </div>
          </template>
        </div>
      </el-tab-pane>

      <!-- ═══ 任务队列 ═══ -->
      <el-tab-pane :label="`任务队列（${jobs.length}）`" name="jobs">
        <div style="padding:16px">
          <el-button size="small" @click="loadJobs" style="margin-bottom:10px">刷新队列</el-button>
          <el-table :data="jobs">
            <el-table-column label="任务类型" width="130">
              <template #default="{ row }">{{ label(JOB_TYPE, row.type) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="jobType(row.status)">{{ label(JOB_STATUS, row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="进度" width="180">
              <template #default="{ row }">
                <span class="muted">
                  {{ row.progress_dict?.current || 0 }} / {{ row.progress_dict?.total || 0 }}
                  {{ row.progress_dict?.label || '' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="结果 / 错误信息">
              <template #default="{ row }">
                <span v-if="row.error" style="color:#f56c6c;font-size:12px">{{ row.error.split('\n')[0] }}</span>
                <span v-else class="muted">{{ friendlyResult(row) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- ═══ 排版 / 导出 ═══ -->
      <el-tab-pane label="排版 / 导出" name="export">
        <div style="padding:16px">
          <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
                    title="中文文字框由本地排版引擎绘制：字体、字号可调；支持对白 / 内心独白 / 喊叫 / 低语 / 旁白 / 说明框 / 拟声词七种样式。" />
          <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
            <span class="muted">文字框字体</span>
            <el-select v-model="bubbleStyle" size="small" style="width:150px">
              <el-option v-for="f in fontOptions" :key="f.key" :label="f.label" :value="f.key" />
            </el-select>
            <span class="muted">字号</span>
            <el-slider v-model="fontScale" :min="0.6" :max="1.8" :step="0.1" style="width:160px" />
            <el-button size="small" @click="saveFontSettings">保存为项目默认</el-button>
            <el-button size="small" type="primary" @click="renderAllPages">渲染全部页面</el-button>
          </div>
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
            <span class="muted">对白语言</span>
            <el-select v-model="exportLang" size="small" style="width:170px">
              <el-option label="中文（原文）" value="" />
              <el-option label="繁體中文" value="zh-Hant" />
              <el-option label="English" value="en" />
              <el-option label="日本語" value="ja" />
            </el-select>
            <el-button size="small" :loading="busy"
                       :disabled="!exportLang"
                       @click="doTranslate">翻译对白（本机 LLM）</el-button>
            <span class="muted">译文缺失的格自动回退中文原文</span>
          </div>
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap">
            <span class="muted">导出</span>
            <el-button @click="doExport('png')">导出 PNG 图片</el-button>
            <el-button @click="doExport('pdf')">导出 PDF 文档</el-button>
            <el-button @click="doExport('cbz')">导出 CBZ 漫画包</el-button>
          </div>
          <div v-if="exportsList.length" style="margin-bottom:16px">
            <div class="asset-section-title">已导出文件（点击下载）</div>
            <el-table :data="exportsList" size="small" style="max-width:760px">
              <el-table-column label="格式" width="90">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ row.fmt.toUpperCase() }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="文件名" />
              <el-table-column label="大小" width="100">
                <template #default="{ row }">{{ humanSize(row.size) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="100">
                <template #default="{ row }">
                  <a :href="row.url" download><el-button size="small" text type="primary">下载</el-button></a>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <div style="display:flex;gap:16px;flex-wrap:wrap">
            <div v-for="pg in project?.pages || []" :key="pg.id">
              <div class="muted" style="margin-bottom:4px">第 {{ pg.index }} 页</div>
              <img :src="pg.page_png + '?t=' + ts" style="width:300px;border:1px solid #ddd;border-radius:4px"
                   @error="(e) => (e.target.style.opacity = 0.15)" />
            </div>
          </div>
          <el-empty v-if="!(project?.pages || []).length" description="还没有页面，先生成分镜格" />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { CAMERA, EMOTION, DIALOGUE_TYPE, PANEL_STATUS, JOB_TYPE, JOB_STATUS, STAGE, STYLE, COLOR_STYLES, label } from '../labels'

const props = defineProps({ id: String })
const router = useRouter()
const route = useRoute()
const tab = ref(route.query.tab || 'story')
const project = ref(null)
const story = ref('')
const pages = ref(3)
const perPage = ref(3)
const useLlm = ref(false)
const busy = ref(false)
const jobs = ref([])
const ts = ref(Date.now())
const draftCount = ref(3)
const newCharName = ref('')
const newCharApp = ref('')
const sheetView = ref('multi')
const useRefs = ref(false)          // 勾选后草稿也带角色参考图
const engine = ref('auto')          // 生成引擎
const bubbleStyle = ref('heiti')    // 文字框字体预设
const fontScale = ref(1.0)          // 文字框字号缩放
const fontOptions = ref([{ key: 'heiti', label: '黑体（默认）' }])
const checkpoints = ref([])         // ComfyUI 可用模型
const ckpt = ref('')
const style = ref('manhua_color')   // 画风（可随时切换）
const styleGroups = ref([])         // 风格库（按分组，来自 /api/styles）
const styleDescMap = ref({})        // key -> 一句话说明
const isColor = computed(() => {
  const found = styleGroups.value.flatMap((g) => g.items).find((s) => s.key === style.value)
  return found ? found.color : COLOR_STYLES.includes(style.value)
})
const currentStyleDesc = computed(() => styleDescMap.value[style.value] || '')

// 选区状态：{ [panelId]: {x,y,w,h} }（0~1 相对坐标）
const selections = ref({})
const inpaintPrompt = ref({})
const dragging = ref({ panelId: '', x0: 0, y0: 0 })

const panels = computed(() => {
  const out = []
  for (const pg of project.value?.pages || []) for (const p of pg.panels) out.push(p)
  return out
})

// ── 资产库 ──
const assetGroups = computed(() => [
  {
    kind: 'scene', kind_label: '场景',
    hint: '（同一地点跨格保持一致，如：校门口 / 旧图书馆）',
    example: '校门口',
    placeholder: '英文场景描述：a school gate at night, stone pillars, wet asphalt',
    views: [
      { label: '全景', value: 'wide' },
      { label: '室内', value: 'interior' },
      { label: '外景', value: 'exterior' },
    ],
    items: project.value?.assets?.scene || [],
  },
  {
    kind: 'prop', kind_label: '道具',
    hint: '（同一件道具跨格保持一致，如：黑伞 / 旧怀表）',
    example: '黑伞',
    placeholder: '英文道具描述：a black folding umbrella with wooden handle',
    views: [
      { label: '三视图', value: 'multi' },
      { label: '正面', value: 'front' },
      { label: '细节', value: 'detail' },
    ],
    items: project.value?.assets?.prop || [],
  },
])
const sceneAssets = computed(() => project.value?.assets?.scene || [])
const propAssets = computed(() => project.value?.assets?.prop || [])
const assetCount = computed(
  () => (project.value?.characters?.length || 0) + sceneAssets.value.length + propAssets.value.length
)
const sheetViews = ref({})          // { assetId: view }
const batchScope = ref('')          // '' = 整个项目
const batchCount = ref(1)
const batchOnlyMissing = ref(true)
const exportsList = ref([])         // 已导出文件
const useVlm = ref(false)           // 评分时是否用本地 VLM 点评
const exportLang = ref('')          // 导出/翻译的对白语言
const loraDialog = ref(false)       // 训练 LoRA 对话框
const loraTarget = ref(null)
const loraForm = ref({ name: '', epochs: 30, rank: 16, lowRam: true })
const newAsset = ref({ scene: { name: '', description: '' }, prop: { name: '', description: '' } })

function pageIndexOf(pnl) {
  const idx = (project.value?.pages || []).findIndex((pg) => pg.panels.some((p) => p.id === pnl.id))
  return idx + 1
}
function statusType(s) {
  return { draft: 'info', generating: 'warning', review: 'warning', approved: 'success', final: 'success', exported: '' }[s] || 'info'
}
function jobType(s) {
  return { pending: 'info', running: 'warning', done: 'success', failed: 'danger' }[s] || 'info'
}
function currentImage(pnl) {
  const sel = (pnl.candidates || []).find((c) => c.id === pnl.selected_candidate_id)
  const pick = sel || [...(pnl.candidates || [])].reverse()[0]
  return pick ? pick.url : ''
}
function providerFor(useRefsFlag) {
  if (engine.value !== 'auto') return engine.value   // 显式选了引擎就用它
  return useRefsFlag ? '' : 'mlx'                    // auto：带参考图时交给后端选 mlx-edit
}
function modelArg() {
  return engine.value === 'comfyui' ? (ckpt.value || '') : ''
}
// 任务结果中文化摘要（原本是英文 JSON）
function friendlyResult(row) {
  const r = row.result_dict || {}
  const parts = []
  if (r.pages !== undefined) parts.push(`页数 ${r.pages}`)
  if (r.panels !== undefined) parts.push(`分镜格 ${r.panels}`)
  if (r.characters !== undefined) parts.push(`人物 ${r.characters}`)
  if (r.scenes !== undefined) parts.push(`场景 ${r.scenes}`)
  if (r.used_llm !== undefined) parts.push(r.used_llm ? '大模型拆解' : '规则拆解')
  if (r.candidates) parts.push(`生成候选 ${r.candidates.length} 张`)
  if (r.files) parts.push(`导出文件 ${r.files.length} 个`)
  if (r.page_png) parts.push('页面已渲染')
  if (r.character_id) parts.push('人物设定图已生成')
  if (r.asset_id) parts.push('资产设定图已生成')
  if (r.stage) parts.push(`阶段：${label(STAGE, r.stage)}`)
  return parts.length ? parts.join(' · ') : '—'
}

async function refresh() {
  project.value = await api.getProject(props.id)
  style.value = project.value.style || 'manhua_color'
  bubbleStyle.value = project.value.bubble_style || 'heiti'
  fontScale.value = project.value.font_scale || 1.0
  ts.value = Date.now()
  await loadJobs()
}
async function doBatchDraft() {
  busy.value = true
  try {
    const r = await api.batchDraft(props.id, {
      count: batchCount.value,
      page_id: batchScope.value,
      only_missing: batchOnlyMissing.value,
      use_references: useRefs.value,
      provider: '',
    })
    if (!r.enqueued) ElMessage.info(r.message || '没有需要生成的格子')
    else ElMessage.success(`已入队 ${r.enqueued} 个分镜（串行执行，可在任务队列看进度）`)
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}

async function doRegenerate(kind, item) {
  busy.value = true
  try {
    const fn = kind === 'character' ? api.regenerateCharacter : api.regenerateAsset
    const r = await fn(item.id, { count: 1, use_references: true })
    if (!r.enqueued) ElMessage.info(r.message || '没有分镜引用该资产')
    else ElMessage.success(`已入队重跑 ${r.enqueued} 个引用分镜`)
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}

async function doScorePanel(pnl) {
  busy.value = true
  try {
    await api.scorePanel(pnl.id, { use_vlm: useVlm.value, only_unscored: true })
    ElMessage.success(useVlm.value ? '已入队评分 + VLM 点评（较慢）' : '已入队评分排序')
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}

async function doBatchScore() {
  busy.value = true
  try {
    await api.scoreProject(props.id, { use_vlm: useVlm.value, only_unscored: true })
    ElMessage.success(useVlm.value ? '已入队批量评分 + VLM 点评' : '已入队批量评分')
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}

async function doAutoSelect(pnl) {
  busy.value = true
  try {
    const r = await api.autoSelectBest(pnl.id)
    ElMessage.success(`已选最佳候选（${Math.round(r.score)} 分）`)
    await refresh()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally { busy.value = false }
}

async function doAutoSelectAll() {
  busy.value = true
  try {
    const r = await api.autoSelectBestProject(props.id)
    ElMessage.success(`已自动选中 ${r.changed} 格最佳候选${r.skipped_unscored ? `（${r.skipped_unscored} 格未评分已跳过）` : ''}`)
    await refresh()
  } finally { busy.value = false }
}

function scoreClass(score) {
  if (score >= 75) return 'good'
  if (score >= 55) return 'mid'
  return 'bad'
}

function scoreTitle(c) {
  if (!c.score) return ''
  try {
    const d = JSON.parse(c.score_detail || '{}')
    const issues = (d.issues || []).join('；')
    const comment = d.comment || ''
    return `评分 ${Math.round(c.score)}${comment ? ' · ' + comment : ''}${issues ? '\n问题：' + issues : ''}`
  } catch (e) {
    return `评分 ${Math.round(c.score)}`
  }
}

async function doTrainLora(ch) {
  loraForm.value = { name: ch.name, epochs: 30, rank: 16, lowRam: true }
  loraDialog.value = true
  loraTarget.value = ch
}

async function confirmTrainLora() {
  const ch = loraTarget.value
  if (!ch) return
  const f = loraForm.value
  loraDialog.value = false
  busy.value = true
  try {
    await api.trainLora(ch.id, {
      epochs: f.epochs,
      rank: f.rank,
      lr: 1e-4,
      // 省内存模式：量化训练（QLoRA 式）+ 限制样本长边，16GB 机器建议开
      quantize: f.lowRam ? 8 : null,
      max_resolution: f.lowRam ? 768 : 1024,
    })
    ElMessage.success('LoRA 训练任务已入队（耗时较长，可在任务队列看进度）')
    tab.value = 'jobs'
    pollUntilIdle()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally { busy.value = false }
}

async function loadExports() {
  exportsList.value = await api.listExports(props.id)
}

function humanSize(n) {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n > 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

async function saveStyle() {
  await api.updateProject(props.id, { style: style.value })
  try {
    await ElMessageBox.confirm(
      `画风已切换为「${label(STYLE, style.value)}」。是否立即重新生成全部设定图与分镜？（真实出图时每张约 1~2 分钟，总时长 = 资产数 + 分镜数）`,
      '一键升级画风', { confirmButtonText: '立即重新生成', cancelButtonText: '先不，稍后手动生成', type: 'warning' })
    const r = await api.upgradeStyle(props.id, { style: style.value })
    ElMessage.success('画风升级任务已入队（设定图 → 分镜 → 渲染）')
    tab.value = 'jobs'
    pollUntilIdle()
  } catch (e) {
    if (e !== 'cancel' && e?.message) ElMessage.error(String(e.message || e))
    else ElMessage.info('已保存画风；之后新生成的部分会使用新画风')
    await refresh()
  }
}
async function loadJobs() {
  jobs.value = await api.listJobs(props.id)
}

async function doStoryboard() {
  if (!story.value.trim()) return ElMessage.warning('请先输入故事内容')
  busy.value = true
  try {
    await api.storyboard(props.id, {
      story: story.value, pages: pages.value,
      panels_per_page: perPage.value, use_llm: useLlm.value,
    })
    ElMessage.success('故事拆解任务已入队，可在「任务队列」查看进度')
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}

async function save(pnl) {
  await api.updatePanel(pnl.id, {
    action: pnl.action, camera: pnl.camera, emotion: pnl.emotion,
    location: pnl.location, prompt: pnl.prompt,
    scene_id: pnl.scene_id, prop_ids: pnl.prop_ids_list,
  })
}
async function saveDialogue(pnl) {
  await api.updatePanel(pnl.id, { dialogue: pnl.dialogue_list })
}
async function saveChar(row) {
  await api.updateCharacter(row.id, row.name, row.appearance)
}
async function addChar() {
  if (!newCharName.value.trim()) return ElMessage.warning('请输入角色名')
  await api.createCharacter(props.id, newCharName.value.trim(), newCharApp.value)
  newCharName.value = ''
  newCharApp.value = ''
  await refresh()
}
async function doDraft(pnl) {
  busy.value = true
  try {
    await api.draftPanel(pnl.id, {
      count: draftCount.value, provider: providerFor(useRefs.value),
      model: modelArg(), use_references: useRefs.value,
    })
    ElMessage.success('已入队生成候选图')
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}
async function doFinal(pnl) {
  busy.value = true
  try {
    await api.finalPanel(pnl.id, {
      count: 1, provider: providerFor(engine.value === 'comfyui' ? false : true),
      model: modelArg(), use_references: engine.value !== 'comfyui',
      steps: engine.value === 'comfyui' ? 24 : 8,
    })
    ElMessage.success('已入队高清重绘（自动带角色参考图）')
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}
async function doSheet(ch) {
  busy.value = true
  try {
    await api.characterSheet(ch.id, { count: 1, view: sheetView.value })
    ElMessage.success(`已入队生成「${ch.name}」的设定图`)
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}
// ── 资产库操作 ──
async function addAsset(kind) {
  const form = newAsset.value[kind]
  if (!form.name.trim()) return ElMessage.warning(`请输入${kind === 'scene' ? '场景' : '道具'}名`)
  await api.createAsset(props.id, { kind, name: form.name.trim(), description: form.description })
  newAsset.value[kind] = { name: '', description: '' }
  await refresh()
  ElMessage.success('资产已添加')
}
async function saveAsset(a) {
  await api.updateAsset(a.id, { name: a.name, description: a.description })
}
async function removeAsset(a) {
  try {
    await ElMessageBox.confirm(`删除资产「${a.name}」？分镜上的引用会被摘掉（分镜本身保留）。`, '确认', { type: 'warning' })
  } catch (e) { return }
  await api.deleteAsset(a.id)
  await refresh()
  ElMessage.success('已删除')
}
async function doAssetSheet(a) {
  busy.value = true
  try {
    await api.assetSheet(a.id, { count: 1, view: sheetViews.value[a.id] || '' })
    ElMessage.success(`已入队生成「${a.name}」的设定图`)
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}
async function pick(pnl, cand) {
  await api.selectCandidate(cand.id)
  await refresh()
}
async function doInpaint(pnl) {
  const s = selOf(pnl)
  if (!s) return ElMessage.warning('请先在图上拖拽框选要重绘的区域')
  busy.value = true
  try {
    await api.inpaint(pnl.id, {
      rect: { x: s.x, y: s.y, w: s.w, h: s.h },
      prompt: (inpaintPrompt.value[pnl.id] || '').trim(),
      provider: '',
    })
    ElMessage.success('已入队局部重绘')
    clearSel(pnl)
    tab.value = 'jobs'
    pollUntilIdle()
  } finally { busy.value = false }
}
async function doExternalExport(pnl) {
  busy.value = true
  try {
    const r = await api.externalExport(pnl.id, { open_editor: true })
    ElMessage.success(r.opened || `工作图已导出：${r.work_path}`)
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally { busy.value = false }
}
async function doExternalImport(pnl) {
  busy.value = true
  try {
    await api.externalImport(pnl.id)
    ElMessage.success('已回填为新的手动候选')
    await refresh()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally { busy.value = false }
}
async function renderAllPages() {
  for (const pg of project.value?.pages || []) {
    await api.renderPage(pg.id, { font_preset: bubbleStyle.value, size_scale: fontScale.value })
  }
  await refresh()
  ElMessage.success('页面已渲染（中文文字框已更新）')
  tab.value = 'export'
}
async function saveFontSettings() {
  await api.updateProject(props.id, { bubble_style: bubbleStyle.value, font_scale: fontScale.value })
  ElMessage.success('已保存为项目默认文字框设置')
}
async function doExport(fmt) {
  await api.exportProject(props.id, fmt)
  ElMessage.success(`已入队导出 ${fmt.toUpperCase()}`)
  tab.value = 'jobs'
  pollUntilIdle()
}

// ── 选区交互 ──
function relPos(el, ev) {
  const r = el.getBoundingClientRect()
  return {
    x: Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
    y: Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)),
  }
}
function startSel(pnl, ev) {
  const p = relPos(ev.currentTarget, ev)
  dragging.value = { panelId: pnl.id, x0: p.x, y0: p.y }
  selections.value[pnl.id] = { x: p.x, y: p.y, w: 0, h: 0 }
}
function moveSel(pnl, ev) {
  if (dragging.value.panelId !== pnl.id) return
  const p = relPos(ev.currentTarget, ev)
  const { x0, y0 } = dragging.value
  selections.value[pnl.id] = {
    x: Math.min(x0, p.x), y: Math.min(y0, p.y),
    w: Math.abs(p.x - x0), h: Math.abs(p.y - y0),
  }
}
function endSel(pnl) {
  const s = selections.value[pnl.id]
  if (s && (s.w < 0.05 || s.h < 0.05)) delete selections.value[pnl.id]
  dragging.value = { panelId: '', x0: 0, y0: 0 }
}
function selOf(pnl) {
  const s = selections.value[pnl.id]
  return s && s.w > 0.03 && s.h > 0.03 ? s : null
}
function rectStyle(pnl) {
  const s = selOf(pnl)
  if (!s) return {}
  return {
    left: s.x * 100 + '%', top: s.y * 100 + '%',
    width: s.w * 100 + '%', height: s.h * 100 + '%',
  }
}
function clearSel(pnl) { delete selections.value[pnl.id] }

async function doDuplicate(pnl) {
  busy.value = true
  try {
    await api.duplicatePanel(pnl.id)
    ElMessage.success('已复制这一格（含设定与选中候选图）')
    await refresh()
  } finally { busy.value = false }
}
async function doDeletePanel(pnl) {
  try {
    await ElMessageBox.confirm(`确定删除第 ${pnl.index} 格？其候选记录会一并删除（图片文件保留）。`, '确认', { type: 'warning' })
  } catch (e) { return }
  busy.value = true
  try {
    await api.deletePanel(pnl.id)
    ElMessage.success('已删除')
    await refresh()
  } finally { busy.value = false }
}
async function doAddPanel() {
  const pid = batchScope.value || project.value?.pages?.[0]?.id
  if (!pid) return ElMessage.warning('请先选择页面范围')
  busy.value = true
  try {
    await api.createPanel(pid, { action: '新分镜（请编辑动作描述）' })
    ElMessage.success('已在页面末尾新增空格')
    await refresh()
  } finally { busy.value = false }
}

let timer = null
function pollUntilIdle() {
  clearInterval(timer)
  timer = setInterval(async () => {
    await loadJobs()
    const active = jobs.value.some((j) => j.status === 'pending' || j.status === 'running')
    if (!active) {
      clearInterval(timer)
      await refresh()
    }
  }, 2000)
}

onMounted(async () => {
  fontOptions.value = await api.fonts()
  const provs = await api.providers()
  const comfy = provs.find((p) => p.name === 'comfyui')
  checkpoints.value = comfy?.checkpoints || []
  if (checkpoints.value.length) ckpt.value = checkpoints.value[0]
  // 风格库（分组 + 说明），后端为唯一数据源
  const lib = await api.styles()
  styleGroups.value = (lib.groups || []).map((name) => ({
    name, items: (lib.styles || []).filter((s) => s.group === name),
  }))
  styleDescMap.value = Object.fromEntries((lib.styles || []).map((s) => [s.key, s.desc]))
  await refresh()
  if (tab.value === 'export') await loadExports()   // 深链接进来也要有导出清单
})
onUnmounted(() => clearInterval(timer))
// 深链接：?tab=panels / chars / jobs / export
watch(tab, (v) => {
  router.replace({ query: { ...route.query, tab: v } })
  if (v === 'export') loadExports()
})
</script>

<style scoped>
.asset-section-title {
  font-size: 14px;
  font-weight: 600;
  margin: 8px 0 12px;
  padding-left: 8px;
  border-left: 3px solid #409eff;
}
.score-badge {
  position: absolute; top: 2px; left: 2px;
  padding: 0 5px; border-radius: 8px;
  font-size: 11px; font-weight: 600; color: #fff;
}
.score-badge.good { background: #67c23a; }
.score-badge.mid { background: #e6a23c; }
.score-badge.bad { background: #f56c6c; }
.best-flag {
  position: absolute; top: 2px; right: 2px;
  padding: 0 5px; border-radius: 8px;
  font-size: 10px; background: #409eff; color: #fff;
}
.img-wrap { position: relative; cursor: crosshair; user-select: none; line-height: 0; }
.sel-rect {
  position: absolute;
  border: 2px dashed #409eff;
  background: rgba(64, 158, 255, 0.18);
  pointer-events: none;
}
</style>
