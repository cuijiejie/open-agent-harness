<template>
  <div class="app">
    <!-- 拖拽上传提示 -->
    <div v-if="dragActive" class="drop-overlay">
      <div class="drop-card">
        <div class="drop-icon">📎</div>
        <div class="drop-title">松开鼠标即可上传</div>
        <div class="drop-sub">支持表格、图片、PDF/Word/PPT、压缩包、文本</div>
      </div>
    </div>
    <!-- 左侧边栏 -->
    <Transition name="sidebar-slide">
      <aside class="sidebar" v-if="!sidebarCollapsed">
        <div class="sidebar-header">
          <!-- 品牌区：Open Agent Harness（自绘 SVG logo + 字标） -->
          <div class="brand">
            <svg class="brand-mark" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-label="Open Agent Harness">
              <defs>
                <linearGradient id="oahBrand" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stop-color="#4F46E5"/>
                  <stop offset="1" stop-color="#6366F1"/>
                </linearGradient>
              </defs>
              <rect x="2" y="2" width="44" height="44" rx="13" fill="url(#oahBrand)"/>
              <g stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" opacity="0.65">
                <line x1="15.8" y1="17.4" x2="21.2" y2="21.3"/>
                <line x1="32.2" y1="17.4" x2="26.8" y2="21.3"/>
                <line x1="24" y1="29.4" x2="24" y2="32.2"/>
              </g>
              <circle cx="24" cy="24" r="5.3" fill="#FFFFFF"/>
              <circle cx="13.6" cy="15.2" r="3.7" fill="#FFFFFF" opacity="0.9"/>
              <circle cx="34.4" cy="15.2" r="3.7" fill="#FFFFFF" opacity="0.9"/>
              <circle cx="24" cy="35.6" r="3.7" fill="#FFFFFF" opacity="0.9"/>
            </svg>
            <div class="brand-text">
              <div class="brand-name">Open Agent Harness</div>
              <div class="brand-sub">OPEN SOURCE · 智能体平台</div>
            </div>
          </div>

          <button type="button" @click="createNewSession" class="sidebar-new-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            <span>新对话</span>
          </button>
          <button type="button" @click="openSkills" class="sidebar-new-btn sidebar-skill-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l2.4 5.2 5.6.8-4 4 1 5.6L12 15l-5 2.6 1-5.6-4-4 5.6-.8z"/></svg>
            <span>技能与工具</span>
          </button>
          <button type="button" @click="openKb" class="sidebar-new-btn sidebar-kb-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
            <span>知识库</span>
          </button>
          <button type="button" v-if="rbacCanManage" @click="toggleSysMenu" class="sidebar-new-btn sidebar-sys-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 003.6 15a1.65 1.65 0 00-1.51-1H2a2 2 0 010-4h.09A1.65 1.65 0 003.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 008 4.6 1.65 1.65 0 009 3.09V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9v.09a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
            <span>系统设置</span>
            <svg class="sys-chevron" :class="{ collapsed: !sysMenuOpen }" width="14" height="14"
                 viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </button>
          <!-- 系统设置子菜单：只有一个菜单，点选项 → 右侧显示内容 -->
          <div v-if="rbacCanManage && sysMenuOpen" class="sys-submenu">
            <div :class="['sys-menu-item', { active: sysOpen && sysTab === 'users' }]" @click="openSysTab('users')">用户管理</div>
            <div :class="['sys-menu-item', { active: sysOpen && sysTab === 'roles' }]" @click="openSysTab('roles')">角色管理</div>
            <div :class="['sys-menu-item', { active: sysOpen && sysTab === 'tools' }]" @click="openSysTab('tools')">工具管理</div>
            <div :class="['sys-menu-item', { active: sysOpen && sysTab === 'audit' }]" @click="openSysTab('audit')">操作审计</div>
          </div>
        </div>
        <!-- 会话折叠区：默认展开，历史会话放在里面（折叠状态记在浏览器本地） -->
        <div class="sys-menu-item side-files" @click="openTab('files')">
          <span>我的文件</span>
        </div>

        <div class="conv-section" @click="toggleSessions" :title="sessionsOpen ? '收起会话' : '展开会话'">
          <svg class="conv-chevron" :class="{ collapsed: !sessionsOpen }" width="14" height="14"
               viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
          <span>会话</span>
          <em v-if="sessions.length" class="conv-count">{{ sessions.length }}</em>
        </div>

        <div class="sidebar-body" v-show="sessionsOpen" ref="sidebarBodyRef" @scroll="onSidebarScroll">
          <!-- 我的偏好（用户自己管） -->
        <div class="conv-archive-toggle" @click="toggleArchivedView()">
            {{ showArchived ? '← 返回会话列表' : '📦 已归档会话' }}
          </div>
          <div class="conv-empty" v-if="sessions.length===0 && !loadingSessions">暂无历史对话</div>
          <div
            v-for="s in sessions"
            :key="s.id"
            :class="['conv-item', { active: s.session_id === sessionId }]"
            @click="switchSession(s.session_id)"
          >
            <div class="conv-item-title">{{ s.title }}</div>
            <div class="conv-item-time">
              <span v-if="loadingBySession[s.session_id]" class="conv-item-generating">生成中</span>
              <template v-else>{{ s.time }}</template>
            </div>
            <button v-if="!showArchived" class="conv-item-arch" @click.stop="archiveSession(s.id)" title="归档">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><line x1="10" y1="13" x2="14" y2="13"/>
              </svg>
            </button>
            <button class="conv-item-del" @click.stop="deleteSession(s.session_id)" title="删除">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <div v-if="loadingSessions" class="conv-loading">加载中...</div>
        </div>

        <!-- 左下角：当前登录用户（点整块进「个人设置」；退出按钮独立且醒目） -->
        <div class="user-bar">
          <!-- 点头像/名字：弹出「退出登录」 -->
          <transition name="modal-fade">
            <div v-if="userMenuOpen" class="user-pop" @click.stop>
              <button type="button" class="user-pop-item" @click="closeUserMenu(); doLogout()">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M15 17l5-5-5-5"/><path d="M20 12H9"/>
                  <path d="M12 19H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h6"/>
                </svg>
                <span>退出登录</span>
              </button>
            </div>
          </transition>

          <div class="user-card">
            <button type="button" class="user-id" :title="`${userLabel} · 账号菜单`"
                    @click.stop="toggleUserMenu">
              <div class="user-avatar">
                <img class="user-avatar-img" :src="avatarSrc" alt="默认头像"
                     @error="avatarFailed = true" v-if="!avatarFailed" />
                <span v-else>{{ userInitial || 'U' }}</span>
              </div>
              <div class="user-meta">
                <div class="user-name">{{ userLabel }}</div>
                <div class="user-sub" v-if="userRoleLabel && userRoleLabel !== userLabel">{{ userRoleLabel }}</div>
              </div>
              <svg class="user-caret" width="13" height="13" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </button>
            <button type="button" class="user-gear" title="个人设置（偏好 / 修改密码）"
                    @click.stop="openMeTab('prefs')">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="3"/>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6h.09A1.65 1.65 0 0 0 10 3.09V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
              </svg>
            </button>
          </div>
        </div>
      </aside>
    </Transition>

    <!-- 右侧主区域 -->
    <div class="main-area">
      <!-- 顶部标题栏 -->
      <header class="top-bar">
        <button @click="sidebarCollapsed = !sidebarCollapsed" class="top-bar-btn" title="侧边栏">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>
        </button>

        <!-- 标签页：会话锁定，其余可关闭 -->
        <div class="tab-strip">
          <div v-for="t in openTabs" :key="t.key" :class="['tab-item', { active: activeTab === t.key }]"
               @click="activeTab = t.key">
            <span>{{ t.title }}</span>
            <button v-if="t.closable" type="button" class="tab-x" @click.stop="closeTab(t.key)">✕</button>
          </div>
        </div>

      </header>

      <!-- 对话主内容 -->
      <!-- 会话区内部页签：对话 / 轨迹 -->
      <div v-if="activeTab === 'chat'" class="chat-subtabs">
        <button type="button" :class="['chat-subtab', { on: chatSubTab === 'chat' }]" @click="chatSubTab = 'chat'">对话</button>
        <button type="button" :class="['chat-subtab', { on: chatSubTab === 'trace' }]" @click="switchToTrace">轨迹</button>
      </div>

      <main class="main" v-show="activeTab === 'chat' && chatSubTab === 'chat'">
        <template v-if="messages.length === 0 && !loading">
          <div class="welcome">
            <div class="welcome-bot">
              <svg viewBox="0 0 100 100" width="72" height="72">
                <defs>
                  <linearGradient id="botGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stop-color="#667eea"/>
                    <stop offset="100%" stop-color="#764ba2"/>
                  </linearGradient>
                </defs>
                <circle cx="50" cy="50" r="48" fill="url(#botGrad)" opacity="0.12"/>
                <circle cx="50" cy="50" r="38" fill="url(#botGrad)" opacity="0.85"/>
                <path d="M33 36h34a3 3 0 013 3v18a3 3 0 01-3 3H50l-8 6v-6H33a3 3 0 01-3-3V39a3 3 0 013-3z" fill="#fff" opacity="0.95">
                  <animate attributeName="opacity" values="0.95;0.7;0.95" dur="2.5s" repeatCount="indefinite"/>
                </path>
                <circle cx="74" cy="30" r="2.5" fill="#fff" opacity="0.7">
                  <animate attributeName="opacity" values="0.7;0.2;0.7" dur="1.8s" repeatCount="indefinite"/>
                </circle>
                <circle cx="80" cy="24" r="1.8" fill="#fff" opacity="0.5">
                  <animate attributeName="opacity" values="0.5;0.1;0.5" dur="2.2s" repeatCount="indefinite"/>
                </circle>
                <circle cx="26" cy="26" r="1.8" fill="#fff" opacity="0.5">
                  <animate attributeName="opacity" values="0.5;0.1;0.5" dur="2.2s" repeatCount="indefinite"/>
                </circle>
              </svg>
            </div>
            <h2 class="welcome-title">你好，有什么可以帮你？</h2>
          </div>
        </template>
        <template v-else>
          <div class="msgs" ref="messagesRef">
            <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
              <div class="msg-avatar">
                <template v-if="m.role==='user'">{{ displayName.charAt(0) }}</template>
                <template v-else>AI</template>
              </div>
              <div class="msg-main">
                <div class="msg-name" v-if="m.role==='user'">{{ displayName }}</div>
                <div v-if="m.fileName" class="msg-file">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                  {{ m.fileName }}
                </div>
                <!-- 过程展示：思考 + 工具步骤（仅前端内存展示，**不入库**，刷新即消失） -->
                <div v-if="m.thinking || (m.steps && m.steps.length)" class="msg-process">
                  <div class="proc-head" @click="m.processOpen = (m.processOpen === false)">
                    <span class="proc-title">
                      <template v-if="m.thinkingLive">💭 思考中…</template>
                      <template v-else>💭 思考过程<template v-if="m.thinkingMs">（{{ Math.max(1, Math.round(m.thinkingMs / 1000)) }} 秒）</template></template>
                    </span>
                    <span class="proc-caret" :class="{ open: m.processOpen !== false }">▾</span>
                  </div>
                  <div v-show="m.processOpen !== false" class="proc-body">
                    <div v-if="m.steps && m.steps.length" class="proc-steps">
                      <div v-for="(st, si) in m.steps" :key="si" class="proc-step" :class="st.status">
                        <span class="proc-dot"></span>
                        <span class="proc-step-label">{{ st.label }}</span>
                        <span v-if="st.detail" class="proc-step-detail">{{ st.detail }}</span>
                        <span v-if="st.elapsed" class="proc-step-time">{{ st.elapsed }}s</span>
                      </div>
                    </div>
                    <div v-if="m.thinking" class="proc-think">{{ m.thinking }}</div>
                  </div>
                </div>
                <div class="msg-text markdown-body" v-html="renderMarkdown(m.content)"></div>
                <div v-if="m.interrupted" class="msg-interrupted">已打断 · 你插话后这一轮提前收尾（已产出的内容保留）</div>
                <div v-if="m.options && m.options.options && m.options.options.length" class="msg-options">
                  <div class="msg-options-btns">
                    <label
                      v-for="(opt, idx) in m.options.options"
                      :key="idx"
                      class="option-btn"
                      :class="{ chosen: (m.optionsSelected || []).includes(opt) }"
                    >
                      <input
                        type="checkbox"
                        class="option-checkbox"
                        :checked="(m.optionsSelected || []).includes(opt)"
                        :disabled="m.optionsChosen || loading"
                        @change="chooseOption(m, opt)"
                      />
                      <span class="option-label">{{ opt }}</span>
                    </label>
                  </div>
                  <div class="msg-options-actions">
                    <span v-if="m.optionsChosen" class="option-done-tip">已作答</span>
                    <button
                      class="option-confirm-btn"
                      :disabled="!(m.optionsSelected && m.optionsSelected.length) || loading || !!m.optionsChosen"
                      @click="confirmSelection(m)"
                    >确定</button>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="loading" class="msg assistant">
              <div class="msg-avatar">AI</div>
              <div class="msg-main">
                <div class="dots"><span></span><span></span><span></span></div>
              </div>
            </div>
          </div>
        </template>

        <div class="bottom">
          <div class="input-box">
            <div class="file-tag-row" v-if="selectedFiles.length">
              <span class="file-tag-name" v-for="(f, i) in selectedFiles" :key="i">
                {{ f.name }}
                <button @click="removeFileAt(i)" class="file-tag-close">&times;</button>
              </span>
              <span class="file-total" :class="{ over: selectedTotalMB > MAX_TOTAL_MB }">
                共 {{ selectedTotalMB.toFixed(1) }} / {{ MAX_TOTAL_MB }} MB
              </span>
            </div>
            <div class="input-row">
              <textarea
                ref="taRef"
                v-model="inputText"
                placeholder="输入消息..."
                @keydown.enter.exact.prevent="sendMessage"
                @input="autoResize"
                rows="1"
              ></textarea>
            </div>
            <div class="input-tools">
              <label class="tool-btn" title="支持表格 / 图片 / 办公文档 / 压缩包 / 文本，也可直接把文件拖进窗口">
                <input type="file" :accept="ACCEPT_ATTR" multiple @change="handleFileSelect" :disabled="loading" />
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                <span>上传附件</span>
              </label>
              <button v-if="loading" @click="stopGenerating" class="send stop" title="停止生成">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
              </button>
              <button @click="sendMessage" class="send"
                :disabled="!inputText.trim()&&!selectedFile"
                :class="{on:inputText.trim()||selectedFile, interrupt: loading}"
                :title="loading ? '发送并打断当前回答（改道）' : '发送'">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
              </button>
            </div>
          </div>
        </div>
      </main>

    <!-- 系统设置（右侧 tab 内容） -->
    <div v-if="sysOpen" class="tab-pane">
      <div class="sys-wrap">
        <section class="sys-body">

          <!-- ===== 工具管理：元数据（说明/分组/风险/启停/人人可用） ===== -->
          <template v-if="sysTab === 'tools'">
            <div class="seg-tabs">
              <button type="button" :class="['seg-tab', { active: toolsTab === 'tools' }]" @click="toolsTab = 'tools'">工具</button>
              <button type="button" :class="['seg-tab', { active: toolsTab === 'mcp' }]" @click="switchToolsTab('mcp')">MCP 服务</button>
            </div>

            <div v-show="toolsTab === 'tools'">
            <div class="rbac-toolbar toolbar-center">
              <div class="tb-left"></div>
              <div class="tb-group tb-mid">
                <input v-model="toolQ" class="rbac-input input-narrow" placeholder="搜索工具名/说明/分组"
                       @keyup.enter="goToolPage(1); loadTools()" />
                <button type="button" class="skill-act" @click="goToolPage(1); loadTools()">查询</button>
              </div>
              <div class="tb-group tb-right"></div>
            </div>

            <div v-if="toolsLoading" class="skill-empty">加载中...</div>
            <div v-else class="table-wrap">
              <div class="tools-thead">
                <span>工具</span>
                <span>分组</span>
                <span>风险</span>
                <span>说明</span>
                <span>公共</span>
                <span>状态</span>
                <span>操作</span>
              </div>
              <div v-for="t in toolList" :key="t.name" class="tools-row">
                <div>
                  <template v-if="toolEdit === t.name">
                    <input v-model="toolForm.display_name" class="inline-input" placeholder="中文名" />
                    <div class="cell-muted"><code>{{ t.name }}</code></div>
                  </template>
                  <template v-else>
                    <b>{{ t.display_name || t.name }}</b>
                    <div v-if="t.display_name && t.display_name !== t.name" class="cell-muted"><code>{{ t.name }}</code></div>
                  </template>
                </div>
                <div>
                  <input v-if="toolEdit === t.name" v-model="toolForm.grp" class="inline-input" placeholder="分组" />
                  <span v-else>{{ t.grp || '—' }}</span>
                </div>
                <div>
                  <select v-if="toolEdit === t.name" v-model="toolForm.risk" class="inline-input">
                    <option value="read">读</option>
                    <option value="write">写</option>
                    <option value="exec">执行</option>
                  </select>
                  <span v-else :class="['risk-tag', 'risk-' + t.risk]">{{ riskLabel(t.risk) }}</span>
                </div>
                <div>
                  <input v-if="toolEdit === t.name" v-model="toolForm.note" class="inline-input wide" placeholder="说明" />
                  <span v-else class="cell-disp">{{ t.note || '—' }}</span>
                </div>
                <div>
                  <!-- 公共：一键生效，不必进编辑态；公共工具人人可用、不参与角色分配 -->
                  <input type="checkbox" :checked="!!t.always_on" :disabled="toolSaving"
                         :title="t.always_on ? '公共：所有角色自动拥有（点一下取消）' : '点一下设为公共'"
                         @change="toggleToolPublic(t)" />
                </div>
                <div>
                  <input v-if="toolEdit === t.name" type="checkbox" v-model="toolForm.enabled" />
                  <span v-else :style="{ color: t.enabled === false ? '#d93025' : '#0f9d58' }">
                    {{ t.enabled === false ? '已停用' : '启用' }}
                  </span>
                </div>
                <div class="rbac-user-actions">
                  <template v-if="toolEdit === t.name">
                    <button type="button" class="link-btn" :disabled="toolSaving" @click="saveTool(t.name)">
                      {{ toolSaving ? '保存中…' : '保存' }}
                    </button>
                    <button type="button" class="link-btn danger" @click="toolEdit = ''">取消</button>
                  </template>
                  <button v-else type="button" class="link-btn" @click="startEditTool(t)">编辑</button>
                </div>
              </div>
              <div v-if="!toolList.length" class="skill-empty">没有匹配「{{ toolQ }}」的工具</div>
            </div>
            <div class="pager">
              <span class="pager-info">共 {{ toolTotal }} 个工具 · 第 {{ toolPage }} / {{ toolPages }} 页</span>
              <button type="button" class="skill-act" :disabled="toolPage <= 1" @click="goToolPage(toolPage - 1)">上一页</button>
              <button type="button" class="skill-act" :disabled="toolPage >= toolPages" @click="goToolPage(toolPage + 1)">下一页</button>
            </div>
            </div>

            <!-- ===== MCP 服务（streamable_http：管理员填地址即可接入别人的服务） ===== -->
            <div v-show="toolsTab === 'mcp'">
              <div class="rbac-toolbar toolbar-right">
                <button type="button" class="skill-act" @click="loadMcp">刷新</button>
                <button v-if="mcpCanManage" type="button" class="skill-act" @click="reloadMcp">重新连接全部</button>
                <button v-if="mcpCanManage && mcpEnabled" type="button" class="appr-ok" @click="startNewMcp">新增服务</button>
              </div>

              <div v-if="mcpEdit" class="mcp-edit-panel">
                <div class="mcp-edit-title">
                  {{ mcpForm.isNew ? '接入别人的 MCP 服务（streamable_http）' : '编辑 ' + mcpEdit }}
                </div>
                <div class="mcp-edit-row" v-if="mcpForm.isNew">
                  <span>服务名</span>
                  <input v-model="mcpForm.name" class="inline-input" placeholder="小写字母/数字/下划线，如 corp-wiki" />
                </div>
                <div class="mcp-edit-row">
                  <span>服务中文名</span>
                  <input v-model="mcpForm.display_name" class="inline-input" placeholder="如 公司 Wiki（留空显示服务名）" />
                </div>
                <div class="mcp-edit-row" v-if="mcpForm.isNew || mcpForm.remote">
                  <span>接入地址</span>
                  <input v-model="mcpForm.url" class="inline-input" placeholder="https://mcp.example.com/mcp" />
                </div>
                <div class="mcp-edit-row" v-if="mcpForm.isNew || mcpForm.remote">
                  <span>请求头</span>
                  <textarea v-model="mcpForm.headersText" class="inline-input mcp-headers-input"
                            placeholder="每行一个，例如&#10;Authorization: Bearer xxxx"></textarea>
                </div>
                <div class="mcp-edit-row" v-if="mcpForm.remote && !mcpForm.isNew">
                  <span>启用</span>
                  <label class="mcp-enable"><input type="checkbox" v-model="mcpForm.enabled" /> 参与工具装配</label>
                </div>
                <div class="mcp-edit-row" v-for="tname in mcpFormTools" :key="tname">
                  <span><code>{{ tname }}</code></span>
                  <input v-model="mcpForm.tool_names[tname]" class="inline-input" placeholder="工具中文名，如 回显" />
                </div>
                <div class="mcp-edit-actions">
                  <button type="button" class="appr-ok" :disabled="mcpSaving" @click="saveMcpServer">{{ mcpSaving ? '保存中…' : '保存' }}</button>
                  <button type="button" class="skill-act" @click="mcpEdit = ''">取消</button>
                </div>
              </div>
              <div v-if="mcpMsg" class="skill-msg">{{ mcpMsg }}</div>
              <div v-if="!mcpEnabled" class="skill-empty">
                当前环境未启用 MCP。把 <code>configs.cfg</code> 的 <code>[mcp] enabled</code> 改成 <code>true</code> 并重启服务即可。
              </div>
              <div v-else-if="mcpLoading" class="skill-empty">加载中…</div>
              <div v-else-if="!mcpServers.length" class="skill-empty">
                还没有接入 MCP 服务。<br />
                <span class="skill-hint">点「新增服务」填对方 MCP 的 streamable_http 地址即可；需要鉴权就在请求头里填 Token。</span>
              </div>
              <div v-else class="table-wrap">
                <div class="mcp-thead">
                  <span>服务名</span><span>状态</span><span>接入方式</span><span>工具数</span><span>工具</span><span>公共</span><span>操作</span>
                </div>
                <div v-for="srv in mcpServers" :key="srv.name" class="mcp-row">
                  <div class="mcp-name-cell"><b>{{ srv.display_name || srv.name }}</b><span class="cell-muted"><code>{{ srv.name }}</code></span></div>
                  <div>
                    <span :style="{ color: srv.connected ? '#0f9d58' : '#d93025' }">
                      {{ srv.connected ? '已连接' : '未连接' }}
                    </span>
                    <span v-if="srv.last_error" class="cell-muted" :title="srv.last_error"> ⚠</span>
                  </div>
                  <div class="cell-muted" :title="srv.url || ''">
                    <span class="mcp-transport-chip">streamable_http</span>
                    <code class="mcp-url">{{ srv.url }}</code>
                  </div>
                  <div class="cell-muted">{{ srv.tool_count || (srv.tools || []).length }} 个</div>
                  <div class="cell-disp" :title="(srv.tools || []).join(', ')">
                    <span v-for="(t, i) in (srv.tools || [])" :key="t">
                      <span class="mcp-tool-chip">{{ mcpListLabel(srv, t) }}</span><template v-if="i < (srv.tools || []).length - 1">，</template>
                    </span>
                    <span v-if="!(srv.tools || []).length">—</span>
                  </div>
                  <div>
                    <input type="checkbox" :checked="!!srv.public" :disabled="userBusy || !(srv.tools || []).length"
                           :title="srv.public ? '公共：所有角色自动拥有（点一下取消）' : '点一下把该服务全部工具设为公共'"
                           @change="toggleMcpPublic(srv)" />
                  </div>
                  <div class="rbac-user-actions">
                    <button type="button" class="link-btn" @click="startEditMcp(srv)">编辑</button><button type="button" class="link-btn" :disabled="userBusy" @click="testMcp(srv.name)">测试</button><button v-if="mcpCanManage" type="button" class="link-btn link-danger" @click="deleteMcp(srv)">删除</button>
                  </div>
                </div>
              </div>
              <div v-if="mcpServers.length" class="pager">
                <span class="pager-info">共 {{ mcpTotal }} 个服务 · 第 {{ mcpPage }} / {{ mcpPages }} 页</span>
                <button type="button" class="skill-act" :disabled="mcpPage <= 1" @click="goMcpPage(mcpPage - 1)">上一页</button>
                <button type="button" class="skill-act" :disabled="mcpPage >= mcpPages" @click="goMcpPage(mcpPage + 1)">下一页</button>
              </div>
            </div>
          </template>

          <!-- ===== 用户管理：账号 / 角色 / 启停 ===== -->
          <template v-if="sysTab === 'users'">
            <div class="rbac-toolbar toolbar-center">
              <div class="tb-left"></div>
              <div class="tb-group tb-mid">
                <input v-model="userQ" class="rbac-input input-narrow" placeholder="搜索域账号或姓名" @keyup.enter="userPage = 1; loadRbac()" />
                <input v-model="userRoleFilter" class="rbac-input input-narrow" placeholder="按角色（编码或名称）" @keyup.enter="userPage = 1; loadRbac()" />
                <button type="button" class="skill-act" @click="userPage = 1; loadRbac()">查询</button>
              </div>
              <div class="tb-group tb-right">
                <button type="button" class="btn-primary" @click="openAddUser">＋ 新增用户</button>
              </div>
            </div>

            <!-- 新增用户（行内展开，不用弹窗） -->
            <div v-if="addUserOpen" class="edit-row">
              <input v-model="newUser.username" class="rbac-input input-sm" placeholder="域账号（必填）" />
              <input v-model="newUser.display_name" class="rbac-input input-sm" placeholder="姓名" />
              <div class="rbac-user-roles">
                <span v-for="c in newUser.roles" :key="c" class="role-chip">
                  {{ roleName(c) }}<button type="button" class="chip-x" @click="toggleIn(newUser.roles, c)">✕</button>
                </span>
                <select class="rbac-select chip-add" :value="''" @change="addRoleTo(newUser.roles, $event)">
                  <option value="">＋ 角色</option>
                  <option v-for="r in rbacRoles" :key="r.role_code" :value="r.role_code"
                          :disabled="newUser.roles.includes(r.role_code)">{{ r.role_name }}</option>
                </select>
              </div>
              <button type="button" class="btn-primary" :disabled="userBusy" @click="submitNewUser">添加</button>
              <button type="button" class="skill-act" @click="addUserOpen = false">取消</button>
            </div>

            <div class="table-wrap">
            <div class="user-thead">
              <span>账号</span><span>姓名</span><span>角色</span>
              <span>状态</span><span>最近登录</span><span>会话</span><span>操作</span>
            </div>
            <div class="rbac-list">
              <div v-if="!rbacUsers.length && !rbacLoading" class="conv-empty">没有匹配的用户</div>
              <div v-for="u in rbacUsers" :key="u.username" :class="['rbac-user', { 'rbac-user-off': u.status === 'disabled' }]">
                <div class="cell-user">{{ u.username }}</div>
                <div class="cell-disp">
                  <input v-if="editUser === u.username" v-model="editUserData.display_name"
                         class="rbac-input input-xs" placeholder="姓名" />
                  <span v-else>{{ u.display_name || '—' }}</span>
                </div>
                <div class="rbac-user-roles">
                  <template v-if="editUser === u.username">
                    <span v-for="c in editUserData.roles" :key="c" class="role-chip">
                      {{ roleName(c) }}<button type="button" class="chip-x" @click="toggleIn(editUserData.roles, c)">✕</button>
                    </span>
                    <select class="rbac-select chip-add" :value="''" @change="addRoleTo(editUserData.roles, $event)">
                      <option value="">＋ 角色</option>
                      <option v-for="r in rbacRoles" :key="r.role_code" :value="r.role_code"
                              :disabled="editUserData.roles.includes(r.role_code)">{{ r.role_name }}</option>
                    </select>
                  </template>
                  <template v-else>
                    <span v-for="c in (u.roles || []).slice(0, 2)" :key="c" class="role-chip">{{ roleName(c) }}</span>
                    <span v-if="(u.roles || []).length > 2" class="role-chip more"
                          :title="(u.roles || []).map(roleName).join('、')">+{{ (u.roles || []).length - 2 }}</span>
                    <span v-if="!(u.roles || []).length" class="muted">无角色（按最小权限运行）</span>
                  </template>
                </div>
                <div>
                  <span :class="u.status === 'disabled' ? 'status-off' : 'status-ok'">
                    {{ u.status === 'disabled' ? '已停用' : '启用' }}
                  </span>
                </div>
                <div class="cell-muted">{{ u.last_login }}</div>
                <div class="cell-muted">{{ u.sessions }}</div>
                <div class="rbac-user-actions">
                  <template v-if="editUser === u.username">
                    <button type="button" class="btn-primary" :disabled="userBusy" @click="saveEditUser(u)">保存</button>
                    <button type="button" class="skill-act" @click="cancelEditUser">取消</button>
                  </template>
                  <template v-else>
                    <button type="button" class="link-btn" @click="startEditUser(u)">编辑</button>
                    <button type="button" class="link-btn" @click="openResetPwd(u)">重置密码</button>
                    <button type="button" class="link-btn" :disabled="userBusy" @click="toggleUserStatus(u)">
                      {{ u.status === 'disabled' ? '启用' : '停用' }}
                    </button>
                    <button type="button" class="link-btn danger" :disabled="userBusy" @click="removeUser(u)">删除</button>
                  </template>
                </div>
              </div>
            </div>
            </div>

            <!-- 分页（真分页：每次只取 10 条） -->
            <div class="pager">
              <span class="pager-info">共 {{ userTotal }} 人 · 第 {{ userPage }} / {{ userPages }} 页</span>
              <button type="button" class="skill-act" :disabled="userPage <= 1 || rbacLoading" @click="goUserPage(userPage - 1)">上一页</button>
              <button type="button" class="skill-act" :disabled="userPage >= userPages || rbacLoading" @click="goUserPage(userPage + 1)">下一页</button>
            </div>
          </template>

          <!-- ===== 角色管理：角色 → 内置工具 / MCP 工具 ===== -->
          <template v-else-if="sysTab === 'knowledge'">
            <!-- 两个子页：规则与口径（pa_knowledge 短事实）/ 文档资料（RAG） -->
            <div class="kb-tabs">
              <button type="button" :class="['kb-tab', { on: kbTab === 'facts' }]" @click="kbTab = 'facts'">
                业务知识
              </button>
              <button type="button" :class="['kb-tab', { on: kbTab === 'docs' }]" @click="openKbDocs">
                文档资料
              </button>
            </div>

            <!-- ===== 规则与口径（原有）===== -->
            <template v-if="kbTab === 'facts'">            <div class="prefs-count kb-know-count">
              个人 {{ kbKnowMine }}/{{ kbKnowLimit }} 条 · 共享知识由管理员维护
            </div>
            <div class="know-add">
              <input v-model="adminKnowInput" class="rbac-input know-input-main" maxlength="100"
                     placeholder="一句话事实，例如：产品 A 属于主推类目"
                     @keyup.enter="addAdminKnowledge" />
              <input v-model="adminKnowTags" class="rbac-input know-input-tags" maxlength="100"
                     placeholder="关键词：M003838,主推,车抵" />
              <button type="button" class="btn-primary" :disabled="!adminKnowInput.trim() || adminKnowSaving"
                      @click="addAdminKnowledge">{{ adminKnowSaving ? '保存中…' : '添加' }}</button>
            </div>

            <div class="table-wrap">
              <div class="know-thead">
                <span>编号</span><span>范围</span><span>内容</span>
                <span>关键词</span><span>创建人</span><span>创建时间</span><span>操作</span>
              </div>
              <div class="know-list">
                <div v-if="!adminKnowledge.length" class="conv-empty">还没有共享知识</div>
                <div v-for="k in adminKnowledge" :key="k.id" class="know-row">
                  <div class="cell-muted">{{ k.id }}</div>
                  <div><span class="know-scope" :class="k.scope">{{ k.scope === 'global' ? '共享' : '个人' }}</span></div>
                  <template v-if="knowEditing === k.id">
                    <div class="know-edit-cell">
                      <input v-model="knowEditContent" class="rbac-input" maxlength="500" placeholder="一句话事实" />
                    </div>
                    <div class="know-edit-cell">
                      <input v-model="knowEditTags" class="rbac-input" maxlength="200" placeholder="关键词，逗号分隔" />
                    </div>
                  </template>
                  <template v-else>
                    <div class="cell-know">{{ k.content }}</div>
                    <div class="cell-muted know-tags-cell">{{ k.tags || '—' }}</div>
                  </template>
                  <div class="cell-disp">{{ k.created_by || k.username || '—' }}</div>
                  <div class="cell-muted">{{ fmtTime(k.created_at) }}</div>
                  <div class="know-ops">
                    <template v-if="knowEditing === k.id">
                      <a class="op-link" @click="saveKnowledgeEdit(k.id)">{{ knowEditSaving ? '保存中…' : '保存' }}</a>
                      <a class="op-link op-muted" @click="cancelKnowledgeEdit">取消</a>
                    </template>
                    <template v-else>
                      <a class="op-link" @click="startKnowledgeEdit(k)">编辑</a>
                      <a class="op-link" @click="delAdminKnowledge(k.id)">删除</a>
                    </template>
                  </div>
                </div>
              </div>
            </div>
            </template>

            <!-- ===== 文档资料（RAG）===== -->
            <template v-else>
              <div class="know-add know-add-center">
                <input id="kbFileInput" ref="kbFileInput" type="file" class="kb-file"
                       accept=".pdf,.docx,.xlsx,.xlsm,.pptx,.txt,.md,.csv" @change="onKbFilePicked" />
                <label for="kbFileInput" class="kb-file-btn">选择文件</label>
                <select v-model="kbScope" class="rbac-input kb-scope" @change="kbScopeTouched = true">
                  <option value="user">个人</option>
                  <option v-if="isAdmin" value="global">全局</option>
                </select>
                <button type="button" class="btn-primary"
                        :disabled="kbUploading || !kbFile" @click="uploadKbDoc">
                  {{ kbUploading ? '上传中…' : '上传并索引' }}
                </button>
                <button type="button" :class="['kb-refresh-btn', { spinning: kbLoading }]" :disabled="kbLoading"
                        :title="kbAutoPolling ? '索引中，每 5 秒自动刷新' : '刷新文档状态'" @click="loadKbDocs()">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                       stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                    <polyline points="21 3 21 9 15 9" />
                  </svg>
                  <span>刷新</span>
                </button>
              </div>
              <div class="kb-hint kb-hint-line">
                {{ kbFileName || 'PDF / Word / Excel / PPT，≤20MB' }}
                <span v-if="kbAutoPolling" class="kb-polling">· 有文档在索引，每 5 秒自动刷新</span>
              </div>

              <div class="know-add">
                <input v-model="kbQuery" class="rbac-input know-input-main"
                       placeholder="检索测试：例如「天津 2023 地区生产总值」"
                       @keyup.enter="runKbSearch" />
                <button type="button" class="btn-primary"
                        :disabled="kbSearching || !kbQuery.trim()" @click="runKbSearch">
                  {{ kbSearching ? '检索中…' : '检索' }}
                </button>
              </div>

              <div v-if="kbResults.length" class="kb-hits">
                <div v-for="(r, i) in kbResults" :key="r.chunk_id" class="kb-hit">
                  <div class="kb-hit-head">
                    <span class="kb-hit-idx">[{{ i + 1 }}]</span>
                    <span class="kb-hit-doc">{{ r.doc }}</span>
                    <span v-if="r.page" class="kb-hit-meta">第 {{ r.page }} 页</span>
                    <span v-if="r.section" class="kb-hit-meta">· {{ r.section }}</span>
                    <span class="kb-hit-score">{{ r.score }}</span>
                  </div>
                  <div class="kb-hit-body">{{ r.content }}</div>
                </div>
              </div>
              <div v-else-if="kbSearched" class="conv-empty">没有检索到相关内容</div>

              <div class="table-wrap">
                <div class="kb-thead">
                  <span>ID</span><span>文件名</span><span>范围</span><span>大小</span>
                  <span>页数</span><span>片段</span><span>状态</span><span>操作</span>
                </div>
                <div class="kb-list">
                  <div v-if="!kbDocs.length" class="conv-empty">
                    {{ kbLoading ? '加载中…' : '还没有文档，先在上面上传' }}
                  </div>
                  <div v-for="d in kbDocs" :key="d.id" class="kb-row">
                    <div class="cell-muted">{{ d.id }}</div>
                    <div class="cell-know kb-fname" :title="d.filename">{{ d.filename }}</div>
                    <div>
                      <span class="know-scope" :class="d.scope">{{ d.scope === 'global' ? '全局' : '个人' }}</span>
                    </div>
                    <div class="cell-muted">{{ d.size_kb }}KB</div>
                    <div class="cell-muted">{{ d.pages || '—' }}</div>
                    <div class="cell-muted">{{ d.chunks }}</div>
                    <div>
                      <span :class="['kb-status', d.status]" :title="d.error || ''">
                        {{ kbStatusText(d.status) }}
                      </span>
                    </div>
                    <div class="kb-ops">
                      <a class="op-link" @click="reindexKbDoc(d)">重建</a>
                      <a class="op-link danger" @click="removeKbDoc(d)">删除</a>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </template>

          <template v-else-if="sysTab === 'audit'">

            <div class="audit-filters">
              <input v-model="auditUser" class="rbac-input audit-q" placeholder="用户（域账号）" @keyup.enter="loadAudit(0)" />
              <input v-model="auditTool" class="rbac-input audit-q" placeholder="工具名，如 sandbox_shell" @keyup.enter="loadAudit(0)" />
              <select v-model="auditResult" class="rbac-input audit-sel" @change="loadAudit(0)">
                <option value="">全部结果</option>
                <option value="ok">成功</option>
                <option value="rejected">被拒</option>
                <option value="error">失败</option>
              </select>
              <button type="button" class="btn-primary" @click="loadAudit(0)">查询</button>
              <span class="audit-count">共 {{ auditTotal }} 条</span>
            </div>

            <div class="table-wrap">
              <div class="audit-thead">
                <span>时间</span><span>用户</span><span>工具</span>
                <span>结果</span><span>参数</span><span>耗时</span>
              </div>
              <div class="audit-list">
                <div v-if="!auditRows.length" class="conv-empty">暂无审计记录</div>
                <div v-for="r in auditRows" :key="r.id" class="audit-row">
                  <div class="cell-muted audit-time">{{ r.ts }}</div>
                  <div class="cell-disp">{{ r.username || '—' }}</div>
                  <div class="cell-muted">{{ r.tool_name }}</div>
                  <div><span class="audit-badge" :class="r.result">{{ auditResultText(r.result) }}</span></div>
                  <div class="cell-know audit-params" :title="r.params_summary">{{ r.params_summary || '—' }}</div>
                  <div class="cell-muted">{{ r.elapsed_ms }} ms</div>
                </div>
              </div>
            </div>

            <div class="rbac-pager">
              <button type="button" class="skill-act" :disabled="auditPage <= 1" @click="loadAudit((auditPage - 2) * 50)">上一页</button>
              <button type="button" class="skill-act" :disabled="(auditPage * 50) >= auditTotal" @click="loadAudit(auditPage * 50)">下一页</button>
            </div>
          </template>
          <template v-else-if="sysTab === 'roles'">
            <div class="roles-split">

              <!-- ===== 左：角色列表 ===== -->
              <div class="roles-left">
                <div class="roles-left-head">
                  <input v-model="roleQ" class="rbac-input" placeholder="搜索角色名称或编码"
                         @keyup.enter="rolePage = 1; loadCatalog()" />
                  <button type="button" class="skill-act" @click="rolePage = 1; loadCatalog()">查询</button>
                  <button type="button" class="btn-primary btn-sm" @click="addRoleOpen = !addRoleOpen">＋ 新增</button>
                </div>

                <div v-if="addRoleOpen" class="role-add">
                  <input v-model="newRole.code" class="rbac-input" placeholder="编码（英文，如 finance_review）" />
                  <input v-model="newRole.name" class="rbac-input" placeholder="名称（如 财务复核）" />
                  <input v-model="newRole.note" class="rbac-input" placeholder="说明（可选）" />
                  <div class="role-add-actions">
                    <button type="button" class="appr-ok" :disabled="userBusy" @click="submitNewRole">创建</button>
                    <button type="button" class="skill-act" @click="addRoleOpen = false">取消</button>
                  </div>
                </div>

                <div class="roles-list">
                  <div class="role-thead">
                    <span>角色名称</span><span>角色代码</span><span>操作</span>
                  </div>
                  <div v-for="r in catalogRoles" :key="r.role_code"
                       :class="['role-trow', { active: editRole === r.role_code }]"
                       @click="selectRole(r.role_code)">
                    <div class="cell-user">{{ r.role_name }}
                      <span v-if="r.is_builtin" class="role-builtin" title="系统角色，不可删除">内置</span>
                    </div>
                    <div class="cell-muted"><code>{{ r.role_code }}</code></div>
                    <div class="rbac-user-actions" @click.stop>
                      <button type="button" class="link-btn danger" :disabled="userBusy || r.is_builtin"
                              :title="r.is_builtin ? '系统角色（管理员 / 公用角色）不可删除' : ''"
                              @click="removeRole(r.role_code)">删除</button>
                    </div>
                  </div>
                  <div v-if="!catalogRoles.length" class="skill-empty">没有匹配的角色</div>
                </div>

                <div class="roles-left-foot">
                  <span class="pager-info">共 {{ roleTotal }} 个 · 第 {{ rolePage }} / {{ rolePages }} 页</span>
                  <div class="tb-group">
                    <button type="button" class="skill-act" :disabled="rolePage <= 1" @click="goRolePage(rolePage - 1)">上一页</button>
                    <button type="button" class="skill-act" :disabled="rolePage >= rolePages" @click="goRolePage(rolePage + 1)">下一页</button>
                  </div>
                </div>
              </div>

              <!-- ===== 右：被选角色的工具 ===== -->
              <div class="roles-right">
                <template v-if="editRole">
                  <div class="roles-right-head">
                    <div>
                      <div class="roles-right-title">{{ roleName(editRole) }}</div>
                    </div>
                    <label class="rbac-check">
                      <input type="checkbox" :checked="hasTool('*')" @change="toggleAllRoleTools" />
                      <span>全部工具（含 MCP）</span>
                    </label>
                  </div>
                  <div class="roles-right-body">
                    <!-- 一级大标题：内置工具 -->
                    <div class="sec-head sec-builtin">
                      <span>内置工具</span>
                      <span class="sec-count">已授权 {{ roleBuiltinGranted }} / 共 {{ catalogBuiltin.length }}</span>
                    </div>
                    <template v-for="g in catalogBuiltinGroups" :key="g.name">
                      <div class="role-tool-group">{{ g.name }}</div>
                      <label v-for="t in g.items" :key="t.name" :class="['rbac-check', 'role-tool-item', { 'always-on': t.always_on }]">
                        <input type="checkbox" :checked="t.always_on || hasTool(t.name)" :disabled="t.always_on" @change="toggleRoleTool(t.name)" />
                        <span>
                          <b class="tool-alias">{{ t.note ? t.note.split('（')[0] : t.name }}</b>
                          <code class="tool-code">{{ t.name }}</code>
                          <em :class="['risk-tag', 'risk-' + t.risk]">{{ riskLabel(t.risk) }}</em>
                          <em v-if="t.always_on" class="always-on-tag">公共</em>
                          <em class="muted">{{ t.note }}</em>
                        </span>
                      </label>
                    </template>
                    <div class="sec-head sec-mcp">
                      <span>MCP 服务</span>
                      <span class="sec-count" v-if="catalogMcp.length">已接入 {{ catalogMcpGroups.length }} 个 · {{ catalogMcp.length }} 个工具</span>
                      <span class="sec-count" v-else>后台未配置</span>
                    </div>
                    <!-- 按服务名分组，一个服务一组 -->
                    <template v-for="g in catalogMcpGroups" :key="g.server">
                      <div class="mcp-group-head">
                        <span class="mcp-group-name">{{ mcpServerLabel(g.server) }}</span>
                        <span class="mcp-group-count">已授权 {{ g.granted }} / 共 {{ g.items.length }}</span>
                      </div>
                      <label v-for="t in g.items" :key="t.name" :class="['rbac-check', 'role-tool-item', 'mcp-group-item', { 'always-on': t.always_on }]">
                        <input type="checkbox" :checked="t.always_on || hasTool(t.name)" :disabled="t.always_on" @change="toggleRoleTool(t.name)" />
                        <span>
                          <b class="tool-alias">{{ t.display_name || mcpToolLabel(t.name, t.server) }}</b>
                          <code class="tool-code">{{ t.name }}</code>
                        </span>
                      </label>
                    </template>
                  </div>
                  <div class="roles-right-foot">
                    <button class="appr-ok" :disabled="roleSaving" @click="saveRoleTools">
                      {{ roleSaving ? '保存中…' : '保存' }}
                    </button>
                  </div>
                </template>
                <div v-else class="roles-empty">← 左侧选择一个角色，编辑它能用的工具</div>
              </div>

            </div>
          </template>
        </section>
      </div>
    </div>

    <!-- 轨迹（右侧 tab 内容）：每轮一条，展开看工具耗时 -->
    <!-- 个人设置：偏好 / 修改密码（业务知识在侧边栏「知识库」里） -->
    <div v-if="activeTab === 'me'" class="tab-pane">
      <div class="me-wrap">
        <div class="prefs-head">
          <div class="prefs-tabs">
            <button type="button" :class="['prefs-tab', { on: meTab === 'prefs' }]" @click="openMeTab('prefs')">偏好</button>
            <button type="button" :class="['prefs-tab', { on: meTab === 'pwd' }]" @click="openMeTab('pwd')">修改密码</button>
          </div>
        </div>

        <!-- 偏好 -->
        <template v-if="meTab === 'prefs'">
          <div class="prefs-list">
            <div v-if="!prefs.length" class="prefs-empty">还没有记录任何偏好</div>
            <div v-for="p in prefs" :key="p.id" class="prefs-item">
              <span class="prefs-text">{{ p.content }}</span>
              <button type="button" class="prefs-del" @click="delPref(p.id)" title="删除">删除</button>
            </div>
          </div>
          <div class="prefs-add">
            <input v-model="prefInput" class="prefs-input" maxlength="300"
                   placeholder="例如：回答用表格 / 只关注 3 年期数据"
                   @keyup.enter="addPref" />
            <button type="button" class="appr-ok" :disabled="!prefInput.trim()" @click="addPref">添加</button>
          </div>
          <div class="prefs-count">已用 {{ prefs.length }}/{{ prefsMax }} 条</div>
        </template>

        <!-- 修改密码 -->
        <template v-else>
          <div class="me-form">
            <label class="me-label">当前密码</label>
            <input v-model="mePwd.old" type="password" class="rbac-input"
                   autocomplete="current-password" placeholder="请输入当前密码" />
            <label class="me-label">新密码<span class="me-hint">至少 6 位</span></label>
            <input v-model="mePwd.pwd" type="password" class="rbac-input"
                   autocomplete="new-password" placeholder="请输入新密码" />
            <label class="me-label">再次输入新密码</label>
            <input v-model="mePwd.pwd2" type="password" class="rbac-input"
                   autocomplete="new-password" placeholder="再次输入新密码"
                   @keyup.enter="submitMePwd" />
            <p v-if="mePwd.err" class="pwd-err">{{ mePwd.err }}</p>
            <button type="button" class="btn-primary me-save" :disabled="mePwd.busy" @click="submitMePwd">
              {{ mePwd.busy ? '保存中…' : '保存' }}
            </button>
          </div>
        </template>
      </div>
    </div>

    <div v-if="activeTab === 'files'" class="tab-pane">
      <div class="crumb-bar" @dragover.prevent="onRowDragOver(curPath)" @dragleave="onRowDragLeave" @drop.prevent="onRowDrop(curPath)">
        <a class="crumb-item" :class="{ current: !crumbs.length }" @click="loadDir('/sandbox')">
          <span class="crumb-home">🏠</span>我的文件
        </a>
        <template v-for="(c, i) in crumbs" :key="c.path">
          <span class="crumb-sep">/</span>
          <a class="crumb-item" :class="{ current: i === crumbs.length - 1 }" @click="loadDir(c.path)">{{ c.name }}</a>
        </template>
        <span class="crumb-right">
          <span class="crumb-count">{{ dirCount }} 个文件夹 · {{ fileList.length }} 个文件 · {{ fmtSize(fileTotal) }}</span>
          <button type="button" class="btn-primary btn-sm" @click="openMkdir">＋ 新建文件夹</button>
          <button type="button" class="skill-act" :disabled="filesLoading" @click="loadDir(curPath)">{{ filesLoading ? '刷新中…' : '刷新' }}</button>
        </span>
        <span class="crumb-del">
          <button v-if="pickedPaths.length" type="button" class="skill-act" @click="clipCut(pickedPaths)">剪切</button>
          <button v-if="pickedPaths.length" type="button" class="skill-act" @click="clipCopy(pickedPaths)">复制</button>
          <button v-if="clip.paths.length" type="button" class="btn-primary btn-sm" @click="clipPaste(curPath)">粘贴 {{ clip.paths.length }} 个到此处</button>
          <button v-if="pickedPaths.length" type="button" class="btn-primary btn-sm btn-danger" @click="removeSelected()">删除已选 {{ pickedPaths.length }} 个</button>
        </span>
      </div>

      <div v-if="mkdirOpen" class="mkdir-bar">
        <input v-model="mkdirName" class="rbac-input" placeholder="文件夹名" maxlength="60" @keyup.enter="doMkdir" />
        <button type="button" class="btn-primary btn-sm" :disabled="!mkdirName.trim() || mkdirBusy" @click="doMkdir">创建</button>
        <button type="button" class="skill-act" @click="mkdirOpen = false">取消</button>
      </div>

      <div v-if="!fileList.length && !dirList.length && !filesLoading" class="conv-empty">这个文件夹是空的。</div>

      <div v-else class="table-wrap">
        <div class="file-thead">
          <span><input type="checkbox" :checked="allSelected" :indeterminate.prop="someSelected" @change="toggleAll($event)" /></span>
          <span>名称</span><span>大小 / 数量</span><span>修改时间</span><span>操作</span>
        </div>
        <div class="file-list">
          <div v-for="d in dirList" :key="'d-' + d.name" class="file-row"
               :class="{ 'row-dragging': dragPaths.includes(curPath + '/' + d.name), 'row-drop': dropTarget === curPath + '/' + d.name }"
               :draggable="!isSystemDir(d.name)"
               @dragstart="onRowDragStart($event, curPath + '/' + d.name)"
               @dragend="onRowDragEnd"
               @dragover.prevent="onRowDragOver(curPath + '/' + d.name)"
               @dragleave="onRowDragLeave"
               @drop.prevent="onRowDrop(curPath + '/' + d.name)"
               @contextmenu.prevent="openCtx($event, { kind: 'dir', name: d.name, path: curPath + '/' + d.name, count: d.count })">
            <div>
              <input v-if="!isSystemDir(d.name)" type="checkbox" :checked="dirSelected.includes(curPath + '/' + d.name)" @change="toggleDir(curPath + '/' + d.name)" />
            </div>
            <div class="file-name dir-name" @click="loadDir(curPath + '/' + d.name)">
              <span class="dir-icon">📁</span><b>{{ d.name }}</b>
            </div>
            <div class="cell-muted">{{ d.count }} 个文件</div>
            <div class="cell-muted">{{ d.mtime || '—' }}</div>
            <div class="file-ops">
              <a class="op-link" @click="loadDir(curPath + '/' + d.name)">进入</a>
              <a v-if="!isSystemDir(d.name)" class="op-link op-danger" @click="removeDir(d)">删除</a>
            </div>
          </div>
          <div v-for="f in fileList" :key="f.path" class="file-row"
               :class="{ 'row-dragging': dragPaths.includes(f.path), 'row-cut': clip.mode === 'cut' && clip.paths.includes(f.path) }"
               draggable="true"
               @dragstart="onRowDragStart($event, f.path)"
               @dragend="onRowDragEnd"
               @contextmenu.prevent="openCtx($event, { kind: 'file', name: f.name, path: f.path, inline: f.inline, size: f.size })">
            <div><input type="checkbox" :checked="selectedPaths.includes(f.path)" @change="toggleOne(f.path)" /></div>
            <div class="file-name" :title="f.path">
              <span class="dir-icon">📄</span><b>{{ f.name }}</b>
            </div>
            <div class="cell-muted">{{ fmtSize(f.size) }}</div>
            <div class="cell-muted">{{ f.mtime }}</div>
            <div class="file-ops">
              <a v-if="f.inline" class="op-link" :href="artifactUrl(f)" target="_blank" rel="noopener noreferrer">打开</a>
              <a class="op-link" :href="artifactUrl(f) + '?download=1'" target="_blank" rel="noopener noreferrer">下载</a>
              <a class="op-link op-danger" @click="removeFile(f)">删除</a>
            </div>
          </div>
        </div>
      </div>
    </div>

    <transition name="ctx-fade">
    <div v-if="ctx.open" class="ctx-menu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }" @click.stop>
      <div class="ctx-head">
        <span class="ctx-ico">{{ ctx.item.kind === 'dir' ? '📁' : '📄' }}</span>
        <span class="ctx-name">{{ ctx.item.name }}</span>
      </div>
      <div class="ctx-body">
        <a v-if="ctx.item.kind === 'dir'" class="ctx-item" @click="ctxGoto"><span class="ci">↳</span>进入</a>
        <template v-else>
          <a v-if="ctx.item.inline" class="ctx-item" :href="artifactUrl(ctx.item)" target="_blank" rel="noopener noreferrer"><span class="ci">👁</span>打开</a>
          <a class="ctx-item" :href="artifactUrl(ctx.item) + '?download=1'" target="_blank" rel="noopener noreferrer"><span class="ci">⬇</span>下载</a>
        </template>
        <a v-if="ctx.item.kind === 'file' || !isSystemDir(ctx.item.name)" class="ctx-item" @click="ctxCut"><span class="ci">✂</span>剪切</a>
        <a v-if="ctx.item.kind === 'file' || !isSystemDir(ctx.item.name)" class="ctx-item" @click="ctxCopy"><span class="ci">⧉</span>复制</a>
        <a v-if="ctx.item.kind === 'dir' && clip.paths.length" class="ctx-item" @click="ctxPasteHere"><span class="ci">📋</span>粘贴 {{ clip.paths.length }} 个到此处</a>
        <div v-if="ctx.item.kind === 'file' || !isSystemDir(ctx.item.name)" class="ctx-sep"></div>
        <a v-if="ctx.item.kind === 'file' || !isSystemDir(ctx.item.name)" class="ctx-item ctx-danger" @click="ctxDelete"><span class="ci">🗑</span>删除</a>
      </div>
    </div>
    </transition>

    <!-- 修改 / 重置密码 -->
    <transition name="modal-fade">
      <div v-if="pwdBox.open" class="modal-mask" @click.self="closePwdBox">
        <div class="modal-card" role="dialog" aria-modal="true">
          <div class="modal-head">
            <span class="modal-icon">🔑</span>
            <h4>重置密码</h4>
          </div>
          <div class="modal-body">
            <p class="modal-main">为账号 {{ pwdBox.user }} 设置新密码。</p>
            <input v-model="pwdBox.pwd" class="rbac-input pwd-input" type="password"
                   placeholder="新密码（至少 6 位）" autocomplete="new-password" />
            <p v-if="pwdBox.err" class="pwd-err">{{ pwdBox.err }}</p>
          </div>
          <div class="modal-foot">
            <button type="button" class="modal-btn" @click="closePwdBox">取消</button>
            <button type="button" class="modal-btn modal-btn-primary"
                    :disabled="pwdBox.busy" @click="submitPwdBox">确定</button>
          </div>
        </div>
      </div>
    </transition>

    <transition name="modal-fade">
    <div v-if="confirmBox.open" class="modal-mask" @click.self="confirmResolve(false)">
      <div class="modal-card" role="dialog" aria-modal="true">
        <div class="modal-head">
          <span class="modal-icon" :class="{ danger: confirmBox.danger }">{{ confirmBox.danger ? '🗑' : '❓' }}</span>
          <h4>{{ confirmBox.title }}</h4>
        </div>
        <div class="modal-body">
          <p class="modal-main">{{ confirmBox.message }}</p>
          <p v-if="confirmBox.detail" class="modal-detail">{{ confirmBox.detail }}</p>
          <p v-if="confirmBox.danger" class="modal-warn">此操作不可恢复。</p>
        </div>
        <div class="modal-foot">
          <button type="button" class="modal-btn" @click="confirmResolve(false)">取消</button>
          <button type="button" class="modal-btn" :class="confirmBox.danger ? 'modal-btn-danger' : 'modal-btn-primary'" @click="confirmResolve(true)">{{ confirmBox.okText }}</button>
        </div>
      </div>
    </div>
    </transition>

    <div v-if="activeTab === 'chat' && chatSubTab === 'trace'" class="tab-pane chat-trace-pane">
      <div class="trace-filter">
        <select v-model="traceKind" class="rbac-input trace-sel" @change="reloadTraces">
          <option value="">全部类型</option>
          <option value="user">用户输入</option>
          <option value="context">上下文注入</option>
          <option value="assistant">助手回复</option>
          <option value="tool">工具调用</option>
        </select>
        <select v-model="traceStatus" class="rbac-input trace-sel" @change="reloadTraces">
          <option value="">全部状态</option>
          <option value="ok">成功</option>
          <option value="fail">失败</option>
          <option value="denied">被拒</option>
        </select>
        <input v-model="traceTool" class="rbac-input trace-sel" placeholder="工具名包含…" @keyup.enter="reloadTraces" />
        <span class="trace-total">共 {{ traceTotal }} 条</span>
        <button class="skill-refresh" :disabled="traceLoading" @click="reloadTraces">{{ traceLoading ? '加载中…' : '刷新' }}</button>
      </div>

      <div v-if="!traceList.length && !traceLoading" class="skill-empty">这个会话还没有轨迹记录，先发一条消息试试。</div>

      <div v-else class="trace-list">
        <div v-for="(t, i) in traceList" :key="t.id" class="trace-item" :class="{ open: traceOpenId === t.id }">
          <div class="trace-line" @click="toggleTrace(t.id)">
            <span class="trace-time">{{ (t.created_at || '').slice(11, 19) }}</span>
            <span class="trace-kind" :class="'k-' + t.kind">{{ traceKindLabel(t.kind) }}</span>
            <span class="trace-name">{{ t.name || '—' }}</span>
            <span class="trace-args">{{ kindText(t) }}</span>
            <span class="trace-ms" v-if="t.elapsed_ms">{{ (t.elapsed_ms / 1000).toFixed(2) }}s</span>
            <span class="trace-ms" v-else></span>
            <span class="trace-status" :class="'s-' + t.status">{{ t.status }}</span>
            <span class="trace-caret">{{ traceOpenId === t.id ? '▾' : '▸' }}</span>
          </div>
          <div v-if="traceOpenId === t.id" class="trace-detail">
            <div class="trace-meta">第 {{ t.turn_no }} 轮 第 {{ t.step_no }} 步 · 风险 {{ t.risk || '-' }} · {{ t.created_at }}</div>
            <template v-if="t.kind === 'tool'">
              <div class="trace-block">
                <div class="trace-block-h">参数</div>
                <pre class="trace-pre">{{ prettyArgs(t.args_obj, t.args) }}</pre>
              </div>
              <div v-if="t.result" class="trace-block">
                <div class="trace-block-h">结果 <span v-if="t.status !== 'ok'" class="trace-bad">（{{ t.status }}）</span></div>
                <pre class="trace-pre trace-pre-result">{{ t.result }}</pre>
              </div>
            </template>
            <div v-else class="trace-block">
              <div class="trace-block-h">{{ traceKindLabel(t.kind) }}正文</div>
              <pre class="trace-pre">{{ t.result || '（空）' }}</pre>
            </div>
          </div>
        </div>
      </div>

      <div v-if="traceTotal > traceList.length" class="trace-more">
        <button class="skill-refresh" @click="loadTraces(tracePage + 1)">加载更多（已显示 {{ traceList.length }} / {{ traceTotal }}）</button>
      </div>
    </div>

    <!-- 技能与工具（右侧 tab 内容，不再是弹窗） -->
    <div v-if="skillsOpen" class="tab-pane">

          <!-- 页签：技能 / 内置工具 / MCP 服务 -->
          <div class="skill-tabs">
            <button :class="['skill-tab', { active: skillTab === 'skills' }]" @click="switchTab('skills')">技能</button>
            <button :class="['skill-tab', { active: skillTab === 'tools' }]" @click="switchTab('tools')">工具</button>
            <button v-if="mcpVisible" :class="['skill-tab', { active: skillTab === 'mcp' }]" @click="switchTab('mcp')">MCP 服务</button>
          </div>


          <!-- ===== 技能：子页签「我的技能 / 平台技能」 ===== -->
          <template v-if="skillTab === 'skills'">
            <div class="skill-subhead">
              <div class="skill-subtabs">
                <button :class="['skill-subtab', { active: skillSub === 'mine' }]" @click="switchSub('mine')">我的技能</button>
                <button :class="['skill-subtab', { active: skillSub === 'platform' }]" @click="switchSub('platform')">平台技能</button>
              </div>
              <div class="skill-subactions">
                <label v-if="skillSub === 'mine'" class="skill-upload">
                  <input type="file" accept=".zip" @change="uploadSkillFile" />
                  <span>＋ 上传技能包（.zip）</span>
                </label>
                <label v-else-if="platformIsAdmin" class="skill-upload">
                  <input type="file" accept=".zip" @change="uploadPlatformFile" />
                  <span>＋ 上传平台技能（.zip）</span>
                </label>
                <button class="skill-refresh" @click="skillSub === 'mine' ? loadSkills() : loadPlatform()"
                        :disabled="skillSub === 'mine' ? skillsLoading : platformLoading">刷新</button>
              </div>
            </div>

            <!-- 我的技能 -->
            <template v-if="skillSub === 'mine'">
            <div v-if="skillMsg" class="skill-msg">{{ skillMsg }}</div>
            <div v-if="skillsLoading" class="skill-empty">加载中...</div>
            <div v-else-if="!skills.length" class="skill-empty">
              还没有技能。上传一个 .zip（含 SKILL.md），或者直接在对话里让我帮你写一个。
            </div>
            <div v-else class="skill-list">
              <div v-for="sk in skills" :key="sk.name" :class="['skill-item', { off: !sk.enabled }]">
                <div class="skill-item-main">
                  <div class="skill-item-title">
                    {{ sk.name }}
                    <span v-if="sk.tools > 0" class="skill-badge tool">工具 {{ sk.tools }}</span>
                    <span v-if="sk.tools === -1" class="skill-badge warn">tools.json 有误</span>
                    <span v-if="!sk.enabled" class="skill-badge off">已停用</span>
                  </div>
                  <div class="skill-item-desc">{{ sk.description || '（无说明）' }}</div>
                  <div class="skill-item-meta">{{ sk.files }} 个文件 · {{ (sk.size / 1024).toFixed(1) }} KB · {{ sk.sha256 }}</div>
                </div>
                <div class="skill-item-actions">
                  <button class="skill-act" :disabled="publishing === sk.name" @click="publishSkill(sk)">{{ publishing === sk.name ? '发布中…' : '设为平台技能' }}</button>
                  <button class="skill-act" @click="toggleSkill(sk)">{{ sk.enabled ? '停用' : '启用' }}</button>
                  <a class="skill-act" :href="`${apiBase}/api/skills/${encodeURIComponent(sk.name)}/download`">下载</a>
                  <button class="skill-act danger" @click="removeSkill(sk)">删除</button>
                </div>
              </div>
            </div>
            </template>

            <!-- 平台技能：全体共享，可下载/添加到自己的沙箱 -->
            <template v-else>
            <div v-if="platformMsg" class="skill-msg">{{ platformMsg }}</div>
            <div v-if="platformLoading" class="skill-empty">加载中...</div>
            <div v-else-if="!platformSkills.length" class="skill-empty">
              平台还没有技能。在「技能」页把某个技能设为平台技能，或由管理员上传。
            </div>
            <div v-else class="skill-list">
              <div v-for="ps in platformSkills" :key="ps.name" class="skill-item">
                <div class="skill-item-main">
                  <div class="skill-item-title">
                    {{ ps.base_name || ps.name }}
                    <span v-if="(ps.version || 1) > 1" class="skill-badge tool">v{{ ps.version }}</span>
                    <span v-if="platformAdded[ps.name]" class="skill-badge off">已添加</span>
                    <span v-if="ps.warnings && ps.warnings.length" class="skill-badge warn" :title="ps.warnings.join('；')">⚠ 有提示</span>
                  </div>
                  <div class="skill-item-desc">{{ ps.description || '（无说明）' }}</div>
                  <div class="skill-item-meta">
                    {{ ps.files }} 个文件 · {{ (ps.size / 1024).toFixed(1) }} KB · {{ ps.contributor || '—' }} · {{ ps.created_at || '—' }}
                  </div>
                </div>
                <div class="skill-item-actions">
                  <button class="skill-act" @click="addPlatformSkill(ps)">添加到我的技能</button>
                  <a class="skill-act" :href="`${apiBase}/api/platform-skills/${encodeURIComponent(ps.name)}/download`">下载</a>
                  <button v-if="ps.can_delete" class="skill-act danger" @click="removePlatformSkill(ps)">删除</button>
                </div>
              </div>
            </div>
            </template>
          </template>

          <!-- ===== 内置工具（只读：风险等级 / 授权范围 / 是否可见 / 是否审批） ===== -->
          <template v-else-if="skillTab === 'tools'">
            <div class="skill-toolbar">
              <button class="skill-refresh" @click="loadTools">刷新</button>
            </div>
            <div v-if="toolsMsg" class="skill-msg">{{ toolsMsg }}</div>
            <div v-if="toolsLoading" class="skill-empty">加载中...</div>
            <template v-else>
              <template v-for="g in toolGroups" :key="g.name">
                <div class="tool-group-head">
                  <span class="tool-group-name">{{ g.name }}</span>
                  <span class="tool-group-count">{{ g.items.length }} 个</span>
                </div>
                <div v-for="t in g.items" :key="t.name" class="tool-line">
                  <b class="tool-alias">{{ t.display_name && t.display_name !== t.name ? t.display_name : (t.note ? t.note.split('（')[0] : t.name) }}</b>
                  <code class="tool-code">{{ t.name }}</code>
                  <em :class="['risk-tag', 'risk-' + t.risk]">{{ riskLabel(t.risk) }}</em>
                  <span v-if="t.enabled === false" class="skill-badge off">已停用</span>
                </div>
              </template>
              <div v-if="!toolList.length" class="skill-empty">没有匹配的工具</div>
            </template>
            <div v-if="toolPages > 1" class="pager">
              <span class="pager-info">第 {{ toolPage }} / {{ toolPages }} 页</span>
              <button type="button" class="skill-act" :disabled="toolPage <= 1" @click="goToolPage(toolPage - 1)">上一页</button>
              <button type="button" class="skill-act" :disabled="toolPage >= toolPages" @click="goToolPage(toolPage + 1)">下一页</button>
            </div>
          </template>

          <!-- ===== MCP 服务（管理员只读 + 测连通） ===== -->
          <template v-else-if="skillTab === 'mcp'">
            <div class="skill-toolbar">
              <button v-if="mcpCanManage" class="skill-refresh" @click="reloadMcp">重新连接全部</button>
              <button class="skill-refresh" :disabled="mcpRefreshing" @click="refreshMcp">{{ mcpRefreshing ? '刷新中…' : (mcpRefreshed ? '已刷新' : '刷新') }}</button>
            </div>
            <div v-if="mcpMsg" class="skill-msg">{{ mcpMsg }}</div>
            <div v-if="!mcpEnabled" class="skill-empty">
              当前环境未启用 MCP。在 <code>configs.cfg</code> 的 <code>[mcp]</code> 段把 <code>enabled</code> 设为 true 并重启服务。
            </div>
            <div v-else-if="mcpLoading" class="skill-empty">加载中…（首次要探测各服务连接状态，约 1 秒）</div>
            <div v-else-if="!mcpServers.length" class="skill-empty">当前账号还没有被授权的 MCP 服务。</div>
            <template v-else>
              <template v-for="srv in mcpServers" :key="srv.name">
                <div class="mcp-group-head">
                  <span class="mcp-group-name">{{ srv.display_name || srv.name }}</span>
                  <span class="mcp-group-count">
                    {{ srv.connected ? '已连接' : '未连接' }} · 共 {{ srv.total_tools || srv.tool_count }} 个工具<template v-if="srv.total_tools && srv.total_tools !== srv.tool_count"> · 已授权 {{ srv.tool_count }}</template>
                  </span>
                </div>
                <div v-for="tname in (srv.tools || [])" :key="tname" class="tool-line">
                  <b class="tool-alias">{{ mcpListLabel(srv, tname) }}</b>
                  <code class="tool-code">{{ tname }}</code>
                  <em v-if="mcpListNote(srv, tname)" class="tool-note">{{ mcpListNote(srv, tname) }}</em>
                </div>
              </template>
            </template>
            <div class="pager" v-if="mcpTotal > mcpPageSize">
              <span class="pager-info">第 {{ mcpPage }} / {{ mcpPages }} 页</span>
              <button type="button" class="skill-act" :disabled="mcpPage <= 1" @click="goMcpPage(mcpPage - 1)">上一页</button>
              <button type="button" class="skill-act" :disabled="mcpPage >= mcpPages" @click="goMcpPage(mcpPage + 1)">下一页</button>
            </div>
          </template>
    </div>
    </div>
    <!-- 轻提示（右上/顶部居中，2.6 秒自动消失） -->
    <Transition name="fade">
      <div v-if="toast.show" :class="['toast', toast.type]">{{ toast.msg }}</div>
    </Transition>

    <!-- 应用内确认框（替代原生 confirm） -->
    <Transition name="fade">
      <div v-if="confirmState.open" class="skill-mask" @click.self="closeConfirm(false)">
        <div class="confirm-card">
          <div class="confirm-msg">{{ confirmState.msg }}</div>
          <div class="confirm-actions">
            <button type="button" class="skill-act" @click="closeConfirm(false)">取消</button>
            <button type="button" class="btn-primary" @click="closeConfirm(true)">确定</button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
// 纯函数抽到 utils.js：可以用 node 直接单测（导航白名单是安全红线）
import { isSafeNavUrl, windowSizeFor, pageForWindow, evictMsgCache } from './utils.js'

const API_BASE = import.meta.env.VITE_API_URL || '/open-agent-harness'

// Markdown 渲染：大模型输出 markdown（表格/列表/代码块等），前端通用渲染
marked.setOptions({ gfm: true, breaks: true })
const renderMarkdown = (text) => {
  if (!text) return ''
  const html = marked.parse(text)
  // 所有链接（产物下载 / 外链）都在新标签打开，不覆盖当前对话窗口
  const withTarget = html.split('<a ').join('<a target="_blank" rel="noopener noreferrer" ')
  return DOMPurify.sanitize(withTarget, { ADD_ATTR: ['target'] })
}

const getUrlParam = (k) => {
  const p = new URLSearchParams(window.location.search)
  return p.get(k) || ''
}

const readConfig = () => {
  const script = document.querySelector('script[data-agent-api]') || document.querySelector('script[src*="index"]') || document.currentScript
  const cfg = window.ChatWidgetConfig || {}
  return {
    apiBase: (script && script.dataset.agentApi) || cfg.apiBase || API_BASE,
    username: (script && script.dataset.username) || cfg.username || getUrlParam('username') || '',
    displayName: (script && script.dataset.displayName) || cfg.displayName || getUrlParam('displayName') || '',
    sessionId: (script && script.dataset.sessionId) || cfg.sessionId || getUrlParam('session_id') || ''
  }
}

const config = readConfig()
// 按会话隔离的消息/加载状态，支持多会话并行生成、切换不断连
const messagesBySession = ref({})   // { sessionId: message[] }
const loadingBySession = ref({})    // { sessionId: boolean }
// 消息缓存上限：只保留最近访问的若干个会话，否则聊得越久页面内存越大
const MSG_CACHE_MAX = 12
const msgCacheOrder = []            // 最近访问顺序，尾部最新
const inputText = ref('')
const messagesRef = ref(null)
const taRef = ref(null)
const selectedFiles = ref([])
// 兼容旧逻辑（发送按钮 disabled、乐观消息等仍读单值）
const selectedFile = computed(() => selectedFiles.value[0] || null)
const sidebarCollapsed = ref(false)
// 会话区折叠：默认展开（用户折叠过就记住，下次还用他的选择）
const sessionsOpen = ref(localStorage.getItem('pa_sessions_open') !== '0')
const toggleSessions = () => {
  sessionsOpen.value = !sessionsOpen.value
  try { localStorage.setItem('pa_sessions_open', sessionsOpen.value ? '1' : '0') } catch (e) {}
}
const sessions = ref([])
const sessionsPage = ref(0)
const sessionsTotal = ref(0)
const loadingSessions = ref(false)
const sidebarBodyRef = ref(null)
const abortBySession = {}            // { sessionId: AbortController }（非响应式）

// ==== 我的技能（技能存在本人沙箱里，这里只管展示/启停/删除/上传） ====
// ===== 右侧标签页：会话是锁死的固定 tab，其余 tab 可开可关 =====
const openTabs = ref([
  { key: 'chat', title: '会话', closable: false },
])
const activeTab = ref('chat')
const TAB_TITLES = { chat: '会话', me: '个人设置', skills: '技能与工具', 'sys-users': '用户管理', 'sys-roles': '角色管理', 'sys-tools': '工具管理', 'sys-knowledge': '知识库', 'sys-audit': '操作审计', files: '我的文件', trace: '会话' }

const openTab = (key) => {
  if (!openTabs.value.some(t => t.key === key)) {
    openTabs.value.push({ key, title: TAB_TITLES[key] || key, closable: key !== 'chat' })
  }
  activeTab.value = key
}
const closeTab = (key) => {
  if (key === 'chat' || key === 'trace') return   // 会话与轨迹是固定标签，不关
  const i = openTabs.value.findIndex(t => t.key === key)
  if (i >= 0) openTabs.value.splice(i, 1)
  if (activeTab.value === key) activeTab.value = 'chat'
}
const skillsOpen = computed(() => activeTab.value === 'skills')
const skills = ref([])
const skillsLoading = ref(false)
const skillMsg = ref('')

const loadSkills = async () => {
  skillsLoading.value = true
  try {
    const res = await fetch(`${apiBase}/api/skills`)
    const data = await res.json()
    skills.value = data.skills || []
    if (data.error) skillMsg.value = data.error
  } catch (e) {
    skillMsg.value = `读取技能失败：${e.message}`
  } finally {
    skillsLoading.value = false
  }
}

const openSkills = async () => {
  skillMsg.value = ''
  openTab('skills')
  // 每次打开都重新拉（后端 Redis 缓存，代价很小）
  await loadSkills()
  setTimeout(() => { loadRbac() }, 400)
}

// ===== 角色管理（仅管理员；接口层也会校验） =====
const rbacRoles = ref([])
// 新增用户时默认勾选的角色：以后端 [app] default_role 为准（通常是「公用角色」），不写死 viewer
const defaultRoleCode = ref('default')
const rbacUsers = ref([])
const rbacCanManage = ref(false)
const meRoles = ref([])
// 角色编码 → 中文名（非管理员拿不到角色目录时回落成编码）
const meRoleLabels = computed(() => {
  const names = meRoles.value.map((c) => roleName(c) || c)
  return names.join('、')
})
// ===== 左下角账号菜单（点头像/名字弹出：退出登录）=====
const userMenuOpen = ref(false)
const toggleUserMenu = () => { userMenuOpen.value = !userMenuOpen.value }
const closeUserMenu = () => { userMenuOpen.value = false }

// ===== 个人设置页（偏好 / 修改密码）=====
const meTab = ref('prefs')
const mePwd = ref({ old: '', pwd: '', pwd2: '', err: '', busy: false })
const openMeTab = async (tab = 'prefs') => {
  meTab.value = tab
  openTab('me')
  if (tab === 'prefs') await loadPrefs()
}
const submitMePwd = async () => {
  const b = mePwd.value
  b.err = ''
  const pwd = String(b.pwd || '').trim()
  if (!b.old) { b.err = '请输入当前密码'; return }
  if (pwd.length < 6) { b.err = '新密码至少 6 位'; return }
  if (pwd !== b.pwd2) { b.err = '两次输入的新密码不一致'; return }
  b.busy = true
  try {
    const res = await fetch(`${apiBase}/api/user/password`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: b.old, new_password: pwd }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { b.err = d.error || `失败（HTTP ${res.status}）`; return }
    mePwd.value = { old: '', pwd: '', pwd2: '', err: '', busy: false }
    showToast('密码已修改，请重新登录')
    setTimeout(() => { window.location.href = `${apiBase}/login` }, 900)
  } catch (e) {
    b.err = '网络错误，请重试'
  } finally {
    b.busy = false
  }
}
// 显示名：有中文名就只显示中文名；没有中文名（纯拼音/英文/等于账号）就显示账号
const userLabel = computed(() => {
  const dn = String(displayName.value || '').trim()
  const un = String(username.value || '').trim()
  if (!dn || dn === un) return un
  return /[\u4e00-\u9fa5]/.test(dn) ? dn : un
})
const userRoleLabel = computed(() => String(meRoleLabels.value || '').trim())

// 默认头像：姓名/账号首字 + 按账号哈希固定的渐变底色（同一个人每次登录颜色一致）
const AVATAR_GRADIENTS = [
  ['#6366F1', '#4338CA'],
  ['#0EA5E9', '#0369A1'],
  ['#10B981', '#047857'],
  ['#F59E0B', '#B45309'],
  ['#EC4899', '#BE185D'],
  ['#8B5CF6', '#6D28D9'],
]
const userInitial = computed(() => {
  const n = String(displayName.value || username.value || '').trim()
  return n ? n.slice(0, 1).toUpperCase() : ''
})
const avatarStyle = computed(() => {
  const key = String(username.value || 'anon')
  let h = 0
  for (let i = 0; i < key.length; i += 1) h = (h * 31 + key.charCodeAt(i)) % 9973
  const [a, b] = AVATAR_GRADIENTS[h % AVATAR_GRADIENTS.length]
  return {
    background: `linear-gradient(135deg, ${a} 0%, ${b} 100%)`,
    color: '#fff', borderColor: 'transparent',
    boxShadow: `0 3px 9px ${a}55`,
  }
})

const doLogout = async () => {
  if (!await confirmYes('确定退出登录？')) return
  window.location.href = `${apiBase}/logout`
}

const isAdmin = ref(false)
const loadMe = async () => {
  try {
    const res = await fetch(`${apiBase}/api/rbac/me`)
    if (!res.ok) return
    const d = await res.json()
    meRoles.value = d.roles || []
    // 管理员判定只依赖这个接口（/api/users 非管理员会 403，不能拿它当探测）
    isAdmin.value = !!d.is_admin
    applyKbScopeDefault()
    if (isAdmin.value) rbacCanManage.value = true
  } catch (e) { /* 权限接口不可用不影响主流程 */ }
}
const rbacLoading = ref(false)
const rbacSaving = ref('')
const rbacNewUser = ref('')
const userQ = ref('')
const userRoleFilter = ref('')
const userBusy = ref(false)

const userPage = ref(1)
const userTotal = ref(0)
const userPageSize = 10
const userPages = computed(() => Math.max(1, Math.ceil(userTotal.value / userPageSize)))
const goUserPage = (p) => {
  const np = Math.min(Math.max(1, p), userPages.value)
  if (np !== userPage.value) { userPage.value = np; loadRbac() }
}

const loadRbac = async () => {
  // 非管理员没有权限拉用户列表（403），直接跳过；管理员判定见 loadMe()
  if (!isAdmin.value) { rbacLoading.value = false; return }
  rbacLoading.value = true
  try {
    // 用户管理列表（账号/姓名/角色/状态/最近登录/会话数）+ 角色定义，一次拿全
    const qs = new URLSearchParams()
    if (userQ.value.trim()) qs.set('q', userQ.value.trim())
    if (userRoleFilter.value) qs.set('role', userRoleFilter.value)
    qs.set('page', String(userPage.value))
    qs.set('page_size', String(userPageSize))
    const res = await fetch(`${apiBase}/api/users?${qs.toString()}`)
    if (res.ok) {
      const d = await res.json()
      rbacRoles.value = d.roles || []
      if (d.default_role) defaultRoleCode.value = d.default_role
      rbacUsers.value = (d.users || []).map(u => ({ ...u, roles: [...(u.roles || [])] }))
      userTotal.value = d.total || 0
      rbacCanManage.value = !!d.can_manage
      return
    }
    // 非管理员：只用角色接口判断页签是否显示
    const r1 = await fetch(`${apiBase}/api/rbac/roles`)
    if (r1.ok) {
      const d = await r1.json()
      rbacRoles.value = d.roles || []
      rbacCanManage.value = !!d.can_manage
    }
  } catch (e) {
    console.error('加载用户失败:', e)
  } finally {
    rbacLoading.value = false
  }
}

const toggleUserStatus = async (u) => {
  const next = u.status === 'disabled' ? 'active' : 'disabled'
  const verb = next === 'disabled' ? '停用' : '启用'
  if (next === 'disabled' && !await confirmYes(`确定停用 ${u.username}？停用后该账号无法登录（已登录的也会被拦）。`)) return
  userBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/users/${encodeURIComponent(u.username)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: next }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) showToast(d.error || `操作失败（HTTP ${res.status}）`)
    else showToast(`${u.username} 已${verb}`)
    await loadRbac()
  } catch (e) {
    showToast('操作失败：' + e.message)
  } finally {
    userBusy.value = false
  }
}

const toggleRbacRole = (u, code) => {
  if (!u.roles) u.roles = []
  const i = u.roles.indexOf(code)
  if (i >= 0) u.roles.splice(i, 1)
  else u.roles.push(code)
}

const saveRbacUser = async (u) => {
  if (!(u.roles || []).length) {
    showToast('至少要选一个角色')
    return
  }
  rbacSaving.value = u.username
  try {
    const res = await fetch(`${apiBase}/api/rbac/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u.username, roles: u.roles }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) {
      showToast(d.error || d.message || `保存失败（HTTP ${res.status}）`)
    } else {
      showToast('角色已保存')
    }
    await loadRbac()
  } catch (e) {
    showToast('保存失败：' + e.message)
  } finally {
    rbacSaving.value = ''
  }
}

// ===== 轻提示（toast）+ 应用内确认框，替代原生 alert/confirm =====
const toast = ref({ show: false, msg: '', type: 'ok' })
let _toastTimer = null
const showToast = (msg, type = 'ok') => {
  const text = String(msg || '')
  // 没显式指定类型时，按文案猜一下：失败/不能/错误/异常 → 红色
  const t = type === 'ok' && /失败|不能|错误|异常|已存在|拒绝/.test(text) ? 'err' : type
  toast.value = { show: true, msg: text, type: t }
  if (_toastTimer) clearTimeout(_toastTimer)
  _toastTimer = setTimeout(() => { toast.value = { ...toast.value, show: false } }, 2600)
}

// ===== 修改 / 重置密码 =====
const pwdBox = ref({ open: false, user: '', pwd: '', err: '', busy: false })
const openResetPwd = (u) => {
  pwdBox.value = { open: true, user: u.username, pwd: '', err: '', busy: false }
}
const closePwdBox = () => { pwdBox.value = { ...pwdBox.value, open: false } }
const submitPwdBox = async () => {
  const box = pwdBox.value
  box.err = ''
  const pwd = String(box.pwd || '').trim()
  if (pwd.length < 6) { box.err = '新密码至少 6 位'; return }
  box.busy = true
  try {
    const res = await fetch(`${apiBase}/api/users/${encodeURIComponent(box.user)}/password`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { box.err = d.error || `失败（HTTP ${res.status}）`; return }
    box.open = false
    showToast(`已重置 ${box.user} 的密码`)
  } catch (e) {
    box.err = '网络错误，请重试'
  } finally {
    box.busy = false
  }
}

const confirmState = ref({ open: false, msg: '', resolve: null })
const confirmYes = (msg) => new Promise((resolve) => {
  confirmState.value = { open: true, msg: String(msg || ''), resolve }
})
const closeConfirm = (ok) => {
  const fn = confirmState.value.resolve
  confirmState.value = { open: false, msg: '', resolve: null }
  if (fn) fn(ok)
}

// ===== 系统设置：角色 → 内置工具 / MCP 工具 =====
const sysOpen = computed(() => ['sys-users','sys-roles','sys-tools','sys-knowledge','sys-audit'].includes(activeTab.value))
const sysTab = computed(() => ({ 'sys-roles': 'roles', 'sys-tools': 'tools', 'sys-knowledge': 'knowledge', 'sys-audit': 'audit' }[activeTab.value] || 'users'))
const sysMenuOpen = ref(false)
const fmtTime = (v) => {
  if (!v) return '—'
  try {
    const d = new Date(v)
    if (isNaN(d.getTime())) return String(v).slice(0, 16)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  } catch (e) { return String(v).slice(0, 16) }
}
const adminKnowledge = ref([])
const kbKnowMine = ref(0)
const kbKnowLimit = ref(50)
const adminKnowInput = ref('')

// ===== 知识库 · 文档资料（RAG）=====
const kbTab = ref('facts')
const kbDocs = ref([])
const kbLoading = ref(false)
const kbFile = ref(null)
const kbFileName = ref('')
const kbUploading = ref(false)
const kbScope = ref('user')
// 默认范围随身份走：管理员=全局，普通用户=个人；用户手动改过就不再覆盖
const kbScopeTouched = ref(false)
const applyKbScopeDefault = () => {
  if (!kbScopeTouched.value) kbScope.value = isAdmin.value ? 'global' : 'user'
}
const kbQuery = ref('')
const kbResults = ref([])
const kbSearching = ref(false)
const kbSearched = ref(false)

const kbFileInput = ref(null)

const kbStatusText = (st) => ({
  pending: '排队中', parsing: '解析中', ocr: 'OCR 识别中', embedding: '索引中', indexed: '可用', failed: '失败',
}[st] || st || '—')

// 有文档在"排队中/解析中/OCR/索引中"时每 5 秒自动拉一次，全部完成后自动停
const KB_POLL_MS = 5000
const kbAutoPolling = ref(false)
let _kbPollTimer = null
const _kbPending = () => (kbDocs.value || []).some(
  (d) => ['pending', 'parsing', 'ocr', 'embedding'].includes(String(d.status || '')),
)
const stopKbPolling = () => {
  if (_kbPollTimer) { clearTimeout(_kbPollTimer); _kbPollTimer = null }
  kbAutoPolling.value = false
}
const scheduleKbPolling = () => {
  if (_kbPollTimer) { clearTimeout(_kbPollTimer); _kbPollTimer = null }
  if (!_kbPending()) { kbAutoPolling.value = false; return }
  kbAutoPolling.value = true
  _kbPollTimer = setTimeout(async () => {
    _kbPollTimer = null
    await loadKbDocs()
  }, KB_POLL_MS)
}
const loadKbDocs = async () => {
  if (kbLoading.value) return                 // 正在刷就跳过，避免叠请求
  kbLoading.value = true
  try {
    const res = await fetch(`${apiBase}/api/kb/docs?limit=200`)
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || '读取文档列表失败'); return }
    kbDocs.value = d.items || []
  } catch (e) {
    showToast('读取文档列表失败：' + e.message)
  } finally {
    kbLoading.value = false
    scheduleKbPolling()
  }
}

const openKbDocs = async () => {
  kbTab.value = 'docs'
  applyKbScopeDefault()
  await loadKbDocs()
}

const onKbFilePicked = (e) => {
  const f = (e.target.files || [])[0]
  kbFile.value = f || null
  kbFileName.value = f ? `${f.name}（${Math.max(1, Math.round(f.size / 1024))}KB）` : ''
}

const uploadKbDoc = async () => {
  if (!kbFile.value) return
  kbUploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', kbFile.value)
    fd.append('scope', kbScope.value)
    const res = await fetch(`${apiBase}/api/kb/docs`, { method: 'POST', body: fd })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || '上传失败'); return }
    showToast('已上传，后台排队索引（稍后刷新状态）')
    kbFile.value = null
    kbFileName.value = ''
    if (kbFileInput.value) kbFileInput.value.value = ''
    await loadKbDocs()
  } catch (e) {
    showToast('上传失败：' + e.message)
  } finally {
    kbUploading.value = false
  }
}

const runKbSearch = async () => {
  const q = kbQuery.value.trim()
  if (!q) return
  kbSearching.value = true
  kbSearched.value = false
  try {
    const res = await fetch(`${apiBase}/api/kb/search?q=${encodeURIComponent(q)}&top_k=5`)
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || '检索失败'); return }
    kbResults.value = d.items || []
    kbSearched.value = true
  } catch (e) {
    showToast('检索失败：' + e.message)
  } finally {
    kbSearching.value = false
  }
}

const reindexKbDoc = async (d) => {
  const ok = await confirmYes(`重建「${d.filename}」的索引？会在后台重新解析和嵌入。`)
  if (!ok) return
  const res = await fetch(`${apiBase}/api/kb/docs/${d.id}/reindex`, { method: 'POST' })
  const j = await res.json().catch(() => ({}))
  if (!res.ok || j.ok === false) { showToast(j.error || '重建失败'); return }
  showToast('已加入重建队列')
  await loadKbDocs()
}

const removeKbDoc = async (d) => {
  const ok = await confirmYes(`删除「${d.filename}」？同时删除它的全部向量片段，不可恢复。`)
  if (!ok) return
  const res = await fetch(`${apiBase}/api/kb/docs/${d.id}`, { method: 'DELETE' })
  const j = await res.json().catch(() => ({}))
  if (!res.ok || j.ok === false) { showToast(j.error || '删除失败'); return }
  showToast('已删除')
  await loadKbDocs()
}
const adminKnowTags = ref('')
const adminKnowSaving = ref(false)

const auditRows = ref([])
const auditTotal = ref(0)
const auditUser = ref('')
const auditTool = ref('')
const auditResult = ref('')
const auditPage = ref(1)
const auditResultText = (r) => ({ ok: '成功', rejected: '被拒', error: '失败' }[r] || r || '—')

const fileList = ref([])
const dirList = ref([])
const fileTotal = ref(0)
const filesLoading = ref(false)
const curPath = ref('/sandbox')
const selectedPaths = ref([])
const dirSelected = ref([])
const mkdirOpen = ref(false)
const mkdirName = ref('')
const mkdirBusy = ref(false)

const dirCount = computed(() => dirList.value.length)
const isSystemDir = (n) => n === 'upload' || n === 'output'
const crumbs = computed(() => {
  const rel = curPath.value.replace(/^\/sandbox\/?/, '')
  if (!rel) return []
  const parts = rel.split('/').filter(Boolean)
  return parts.map((n, i) => ({ name: n, path: '/sandbox/' + parts.slice(0, i + 1).join('/') }))
})
const removableDirs = computed(() => dirList.value.filter(d => !isSystemDir(d.name)).map(d => curPath.value + '/' + d.name))
const pickedPaths = computed(() => selectedPaths.value.concat(dirSelected.value))
const allSelected = computed(() => {
  const total = fileList.value.length + removableDirs.value.length
  return total > 0 && pickedPaths.value.length === total
})
const someSelected = computed(() => {
  const total = fileList.value.length + removableDirs.value.length
  return pickedPaths.value.length > 0 && pickedPaths.value.length < total
})

const fmtSize = (n) => {
  const v = Number(n) || 0
  if (v >= 1024 * 1024 * 1024) return (v / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + ' MB'
  if (v >= 1024) return (v / 1024).toFixed(1) + ' KB'
  return v + ' B'
}

const artifactUrl = (f) => {
  const rel = String(f.path || '').replace(/^\/sandbox\//, '')
  return `${apiBase}/api/artifacts/${sessionId.value || 'x'}/${rel.split('/').map(encodeURIComponent).join('/')}`
}

const loadDir = async (path) => {
  filesLoading.value = true
  try {
    const res = await fetch(`${apiBase}/api/sandbox/files?path=${encodeURIComponent(path || '/sandbox')}`)
    const d = await res.json()
    if (!res.ok) { showToast(d.error || '读取失败', 'err'); return }
    curPath.value = d.path || '/sandbox'
    dirList.value = d.dirs || []
    fileList.value = d.files || []
    fileTotal.value = d.total || 0
    selectedPaths.value = []
    dirSelected.value = []
    clip.value = { mode: '', paths: [] }
  } catch (e) {
    showToast('读取失败：' + e.message, 'err')
  } finally {
    filesLoading.value = false
  }
}

const confirmBox = ref({ open: false, title: '', message: '', detail: '', danger: true, okText: '确定' })
let confirmResolveFn = null

function confirmResolve(v) {
  confirmBox.value.open = false
  const fn = confirmResolveFn
  confirmResolveFn = null
  if (fn) fn(v)
}

function askConfirm(opts) {
  return new Promise((resolve) => {
    confirmResolveFn = resolve
    confirmBox.value = {
      open: true,
      title: opts.title || '确定',
      message: opts.message || '',
      detail: opts.detail || '',
      danger: opts.danger !== false,
      okText: opts.okText || '确定',
    }
  })
}

document.addEventListener('keydown', onEscKey)

const ctx = ref({ open: false, x: 0, y: 0, item: {} })

function closeCtx() { ctx.value.open = false }
const openCtx = (e, item) => {
  const pad = 8
  const w = 170, h = item.kind === 'dir' ? 120 : 150
  ctx.value = {
    open: true,
    x: Math.min(e.clientX, window.innerWidth - w - pad),
    y: Math.min(e.clientY, window.innerHeight - h - pad),
    item,
  }
}
const ctxGoto = () => { const p = ctx.value.item.path; closeCtx(); loadDir(p) }
const ctxDelete = () => {
  const it = ctx.value.item
  closeCtx()
  if (it.kind === 'dir') removeDir({ name: it.name, count: it.count })
  else removeFile({ name: it.name, path: it.path })
}

const dragPaths = ref([])
const dropTarget = ref('')
const clip = ref({ mode: '', paths: [] })

const onRowDragStart = (e, path) => {
  const paths = selectedPaths.value.includes(path) ? selectedPaths.value.slice() : [path]
  dragPaths.value = paths
  clip.value = { mode: '', paths: [] }      // 拖动优先，清掉剪切态
  try {
    e.dataTransfer.effectAllowed = 'copyMove'
    e.dataTransfer.setData('text/plain', paths.join('\n'))
  } catch (_) {}
}
const onRowDragEnd = () => { dragPaths.value = []; dropTarget.value = '' }
const onRowDragOver = (targetPath) => { if (dragPaths.value.length) dropTarget.value = targetPath }
const onRowDragLeave = () => { dropTarget.value = '' }
const onRowDrop = async (targetPath) => {
  const paths = dragPaths.value.slice()
  dragPaths.value = []
  dropTarget.value = ''
  if (!paths.length) return
  if (paths.includes(targetPath)) return
  await transfer(paths, targetPath, 'move')
}

const clipCut = (paths) => { clip.value = { mode: 'cut', paths: paths.slice() }; showToast('已剪切 ' + paths.length + ' 个，选择目标文件夹后粘贴', 'ok') }
const clipCopy = (paths) => { clip.value = { mode: 'copy', paths: paths.slice() }; showToast('已复制 ' + paths.length + ' 个，选择目标文件夹后粘贴', 'ok') }
const clipPaste = async (dest) => {
  if (!clip.value.paths.length) return
  const mode = clip.value.mode
  await transfer(clip.value.paths.slice(), dest, mode)
  clip.value = { mode: '', paths: [] }
}

const transfer = async (paths, dest, mode) => {
  const url = mode === 'cut' ? 'move' : 'copy'
  try {
    const res = await fetch(`${apiBase}/api/sandbox/${url}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, dest }),
    })
    const d = await res.json()
    if (!res.ok) { showToast(d.error || '操作失败', 'err'); return }
    const okN = (d.moved || d.copied || []).length
    const bad = (d.failed || []).length
    const verb = mode === 'cut' ? '移动' : '复制'
    showToast(bad ? (verb + ' ' + okN + ' 个，' + bad + ' 个失败') : (verb + ' ' + okN + ' 个'), bad ? 'err' : 'ok')
    selectedPaths.value = []
    await loadDir(curPath.value)
  } catch (e) {
    showToast('操作失败：' + e.message, 'err')
  }
}

const ctxCut = () => { const p = ctx.value.item.path; closeCtx(); clipCut([p]) }
const ctxCopy = () => { const p = ctx.value.item.path; closeCtx(); clipCopy([p]) }
const ctxPasteHere = () => { const p = ctx.value.item.path; closeCtx(); clipPaste(p) }

function onEscKey(e) {
  if (e.key === 'Escape' && confirmBox.value.open) confirmResolve(false)
}

onMounted(() => {
  window.addEventListener('click', closeCtx)
  window.addEventListener('scroll', closeCtx, true)
  window.addEventListener('resize', closeCtx)
})
onBeforeUnmount(() => {
  window.removeEventListener('click', closeCtx)
  window.removeEventListener('scroll', closeCtx, true)
  window.removeEventListener('resize', closeCtx)
  // 这两个是 setup 顶层注册的，不清掉的话组件重新挂载会重复叠加
  document.removeEventListener('keydown', onEscKey)
  window.removeEventListener('dragenter', onDragEnter)
  window.removeEventListener('dragover', onDragOver)
  window.removeEventListener('dragleave', onDragLeave)
  window.removeEventListener('drop', onDrop)
})

const openMkdir = () => { mkdirOpen.value = true; mkdirName.value = '' }
const doMkdir = async () => {
  const name = mkdirName.value.trim()
  if (!name) return
  mkdirBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/sandbox/mkdir`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: curPath.value, name }),
    })
    const d = await res.json()
    if (!res.ok) { showToast(d.error || '创建失败', 'err'); return }
    mkdirOpen.value = false
    showToast('已创建 ' + name, 'ok')
    await loadDir(curPath.value)
  } finally {
    mkdirBusy.value = false
  }
}

const removeDir = async (d) => {
  const ok = await askConfirm({
    title: '删除文件夹',
    message: '「' + d.name + '」',
    detail: d.count > 0 ? ('里面有 ' + d.count + ' 个文件，会一并删除。') : '这是个空文件夹。',
    okText: '删除',
  })
  if (!ok) return
  const url = `${apiBase}/api/sandbox/files?path=${encodeURIComponent(curPath.value + '/' + d.name)}` + (d.count > 0 ? '&force=1' : '')
  try {
    const res = await fetch(url, { method: 'DELETE' })
    const r = await res.json()
    if (!res.ok) { showToast(r.error || '删除失败', 'err'); return }
    showToast('已删除 ' + d.name, 'ok')
    await loadDir(curPath.value)
  } catch (e) {
    showToast('删除失败：' + e.message, 'err')
  }
}

const toggleOne = (path) => {
  const i = selectedPaths.value.indexOf(path)
  if (i >= 0) selectedPaths.value.splice(i, 1)
  else selectedPaths.value.push(path)
}
const toggleDir = (path) => {
  const i = dirSelected.value.indexOf(path)
  if (i >= 0) dirSelected.value.splice(i, 1)
  else dirSelected.value.push(path)
}
const toggleAll = (e) => {
  if (e.target.checked) {
    selectedPaths.value = fileList.value.map(f => f.path)
    dirSelected.value = removableDirs.value.slice()
  } else {
    selectedPaths.value = []
    dirSelected.value = []
  }
}

const batchDelete = async (paths) => {
  try {
    const res = await fetch(`${apiBase}/api/sandbox/files/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths }),
    })
    const d = await res.json()
    if (!res.ok) { showToast(d.error || '删除失败', 'err'); return }
    const ok = (d.deleted || []).length
    const bad = (d.failed || []).length
    showToast(bad ? ('已删除 ' + ok + ' 个，' + bad + ' 个失败') : ('已删除 ' + ok + ' 个文件'), bad ? 'err' : 'ok')
    selectedPaths.value = []
    dirSelected.value = []
    await loadDir(curPath.value)
  } catch (e) {
    showToast('删除失败：' + e.message, 'err')
  }
}

const removeSelected = async () => {
  const files = selectedPaths.value.slice()
  const dirs = dirSelected.value.slice()
  const n = files.length + dirs.length
  if (!n) return
  const ok = await askConfirm({
    title: '批量删除',
    message: '共选中 ' + n + ' 项',
    detail: (files.length ? files.length + ' 个文件' : '') + (files.length && dirs.length ? '、' : '') + (dirs.length ? dirs.length + ' 个文件夹（内含文件一并删除）' : ''),
    okText: '全部删除',
  })
  if (!ok) return
  await batchDelete(files.concat(dirs))
}

const removeFile = async (f) => {
  const ok = await askConfirm({ title: '删除文件', message: '「' + f.name + '」', okText: '删除' })
  if (!ok) return
  await batchDelete([f.path])
}

const loadAudit = async (offset) => {
  const o = Math.max(0, offset || 0)
  const q = new URLSearchParams({ limit: '50', offset: String(o) })
  if (auditUser.value.trim()) q.set('user', auditUser.value.trim())
  if (auditTool.value.trim()) q.set('tool', auditTool.value.trim())
  if (auditResult.value) q.set('result', auditResult.value)
  try {
    const res = await fetch(`${apiBase}/api/audit?${q}`)
    const d = await res.json()
    if (!res.ok) { showToast(d.error || '读取审计失败', 'err'); return }
    auditRows.value = d.items || []
    auditTotal.value = d.total || 0
    auditPage.value = Math.floor(o / 50) + 1
  } catch (e) { showToast('读取审计失败：' + e.message, 'err') }
}

const loadAdminKnowledge = async () => {
  try {
    const res = await fetch(`${apiBase}/api/knowledge`)
    if (!res.ok) { showToast('读取知识库失败', 'err'); return }
    const d = await res.json()
    adminKnowledge.value = d.items || []
    kbKnowMine.value = d.mine_count || 0
    kbKnowLimit.value = d.limit || 50
  } catch (e) { showToast('读取知识库失败：' + e.message, 'err') }
}
const addAdminKnowledge = async () => {
  const content = adminKnowInput.value.trim()
  if (!content) return
  adminKnowSaving.value = true
  try {
    const res = await fetch(`${apiBase}/api/knowledge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content, tags: adminKnowTags.value.trim(),
        scope: isAdmin.value ? 'global' : 'user',
      }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || '添加失败', 'err'); return }
    showToast(isAdmin.value ? '已添加共享知识' : '已添加个人知识')
    adminKnowInput.value = ''; adminKnowTags.value = ''
    await loadAdminKnowledge()
  } catch (e) { showToast('添加失败：' + e.message, 'err') }
  finally { adminKnowSaving.value = false }
}
const knowEditing = ref(null)
const knowEditContent = ref('')
const knowEditTags = ref('')
const knowEditSaving = ref(false)
const startKnowledgeEdit = (k) => {
  knowEditing.value = k.id
  knowEditContent.value = k.content || ''
  knowEditTags.value = k.tags || ''
}
const cancelKnowledgeEdit = () => {
  knowEditing.value = null
  knowEditContent.value = ''
  knowEditTags.value = ''
}
const saveKnowledgeEdit = async (id) => {
  const content = knowEditContent.value.trim()
  if (!content) { showToast('内容不能为空', 'err'); return }
  knowEditSaving.value = true
  try {
    const res = await fetch(`${apiBase}/api/knowledge/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, tags: knowEditTags.value.trim() }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || '修改失败', 'err'); return }
    showToast('已保存')
    cancelKnowledgeEdit()
    await loadAdminKnowledge()
  } catch (e) {
    showToast('修改失败：' + e.message, 'err')
  } finally {
    knowEditSaving.value = false
  }
}
const delAdminKnowledge = async (id) => {
  if (!confirm('确定删除这条知识？')) return
  try {
    const res = await fetch(`${apiBase}/api/knowledge/${id}`, { method: 'DELETE' })
    if (!res.ok) { showToast('删除失败', 'err'); return }
    showToast('已删除')
    await loadAdminKnowledge()
  } catch (e) { showToast('删除失败：' + e.message, 'err') }
}
const catalogRoles = ref([])
const catalogBuiltin = ref([])
const catalogMcp = ref([])
const editRole = ref('')
const roleTools = ref([])
const roleSaving = ref(false)

// 点「系统设置」→ **只展开/收起子菜单，不跳转**；右侧内容只在点子项时才切换
const toggleSysMenu = () => {
  sysMenuOpen.value = !sysMenuOpen.value
}

const openSysTab = async (tab) => {
  const key = { roles: 'sys-roles', tools: 'sys-tools', knowledge: 'sys-knowledge', audit: 'sys-audit' }[tab] || 'sys-users'
  sysMenuOpen.value = true
  openTab(key)
  if (tab === 'knowledge') loadAdminKnowledge()
  if (tab === 'audit') loadAudit(0)
  else if (key === 'sys-users') await loadRbac()
  else if (key === 'sys-roles') await loadCatalog()
  else if (key === 'sys-tools') { toolsLoaded.value = true; await loadTools() }
}

// 知识库是一级入口：普通用户也能进（规则与口径看自己的 + 共享；文档资料同理）
const openKb = async () => {
  openTab('sys-knowledge')
  await loadAdminKnowledge()
}

const closeSys = () => { activeTab.value = 'chat' }

const selectRole = (code) => {
  editRole.value = code
  const r = catalogRoles.value.find(x => x.role_code === code)
  // 历史数据可能有 mcp_* 通配：展开成当前实际存在的 MCP 工具，界面上只有具体勾选框
  roleTools.value = ((r && r.tools) || []).flatMap(
    (t) => (t === 'mcp_*' ? catalogMcp.value.map((m) => m.name) : [t])
  )
  roleEdit.value = { name: (r && r.role_name) || '', note: (r && r.note) || '' }
}

const rolePage = ref(1)
const roleTotal = ref(0)
const rolePageSize = 10
const roleQ = ref('')
const rolePages = computed(() => Math.max(1, Math.ceil(roleTotal.value / rolePageSize)))
const allRoles = ref([])
const goRolePage = (p) => {
  const np = Math.min(Math.max(1, p), rolePages.value)
  if (np !== rolePage.value) { rolePage.value = np; loadCatalog() }
}

let _catalogReqId = 0
const loadCatalog = async (opts = {}) => {
  const _my = ++_catalogReqId            // 只认最后一次请求，防止旧响应把新状态盖掉
  try {
    const rq = new URLSearchParams({ page: String(rolePage.value), page_size: String(rolePageSize) })
    if (roleQ.value.trim()) rq.set('q', roleQ.value.trim())
    const res = await fetch(`${apiBase}/api/rbac/catalog?${rq.toString()}`, { cache: 'no-store' })
    if (!res.ok) return
    const d = await res.json()
    if (_my !== _catalogReqId) return    // 已有更新的请求，丢弃本次结果
    catalogRoles.value = d.roles || []
    // 左右分栏：进来默认选中第一个角色，右栏不至于空着
    if (!editRole.value && catalogRoles.value.length) selectRole(catalogRoles.value[0].role_code)
    roleTotal.value = d.roles_total || 0
    allRoles.value = d.all_roles || catalogRoles.value
    catalogBuiltin.value = d.builtin || []
    loadMcpMeta()
    catalogMcp.value = d.mcp || []
    rbacCanManage.value = !!d.can_manage
    const keep = catalogRoles.value.some(r => r.role_code === editRole.value)
      ? editRole.value
      : (catalogRoles.value[0] || {}).role_code
    if (keep && !opts.keepRoleTools) selectRole(keep)   // 刚保存完：不要用目录重置勾选
  } catch (e) {
    console.error('加载角色工具失败:', e)
  }
}

// 角色编码 → 中文名。用户管理页只有 rbacRoles（/api/users 带回的），角色管理页才有 allRoles，
// 两个来源都要查，否则界面上会直接暴露 admin / default / product 这种编码
const roleName = (code) => ((allRoles.value.find(r => r.role_code === code)
  || catalogRoles.value.find(r => r.role_code === code)
  || rbacRoles.value.find(r => r.role_code === code)
  || {}).role_name) || code
const toolCountText = (r) => {
  const ts = r.tools || []
  if (ts.includes('*')) return '全部'
  return `${ts.length} 个`
}
const hasTool = (name) => {
  const ts = roleTools.value
  if (ts.includes('*')) return true
  return ts.includes(name)
}
const riskLabel = (r) => ({ read: '读', write: '写', exec: '执行' }[r] || r)

const toggleRoleTool = (name) => {
  const _t = catalogBuiltin.value.concat(catalogMcp.value).find((x) => x.name === name)
  if (_t && _t.always_on) return   // 人人可用的不给改
  const ts = roleTools.value
  // 已勾了「全部」再点具体工具 → 展开成全量列表再移除
  if (ts.includes('*')) {
    const all = [...catalogBuiltin.value.map(t => t.name), ...catalogMcp.value.map(t => t.name)]
    roleTools.value = all.filter(x => x !== name)
    return
  }
  const i = ts.indexOf(name)
  if (i >= 0) ts.splice(i, 1)
  else ts.push(name)
}

const toggleAllRoleTools = () => {
  roleTools.value = roleTools.value.includes('*') ? [] : ['*']
}

const saveRoleTools = async () => {
  if (!editRole.value) {
    showToast('先在左侧选一个角色')
    return
  }
  roleSaving.value = true
  // 提交的就是界面上勾的：先做快照，避免保存过程中被其它请求改动
  const _role = editRole.value
  const _payload = roleTools.value.slice()
  try {
    const res = await fetch(`${apiBase}/api/rbac/roles/${encodeURIComponent(_role)}/tools`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tools: _payload }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) {
      showToast(d.error || `保存失败（HTTP ${res.status}）`, 'err')
      return
    }
    // 以接口返回的 tools 为准（服务端按它落库），只更新本地状态。
    // **不再调用 loadCatalog()**：它会把服务端目录写回来，正是"保存后又变回去"的根源。
    const _saved = Array.isArray(d.tools) ? d.tools.slice() : _payload
    const _idx = catalogRoles.value.findIndex((r) => r.role_code === _role)
    if (_idx >= 0) catalogRoles.value[_idx].tools = _saved.slice()
    if (editRole.value === _role) roleTools.value = _saved.slice()
    showToast('工具已保存', 'ok')
  } catch (e) {
    showToast('保存失败：' + e.message)
  } finally {
    roleSaving.value = false
  }
}

// ===== 用户：新增 / 编辑 / 删除（全部行内操作） =====
const addUserOpen = ref(false)
const newUser = ref({ username: '', display_name: '', roles: [] })
const editUser = ref('')
const editUserData = ref({ display_name: '', roles: [] })

const toggleIn = (arr, code) => {
  if (!arr) return
  const i = arr.indexOf(code)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(code)
}

const addRoleTo = (arr, ev) => {
  const code = ev && ev.target ? ev.target.value : ''
  if (code && arr && !arr.includes(code)) arr.push(code)
  if (ev && ev.target) ev.target.value = ''   // 复位下拉，方便连续添加
}

const openAddUser = () => {
  addUserOpen.value = true
  // 默认勾选配置里的默认角色（通常「公用角色」）；找不到就留空，由管理员自己选
  const has = (rbacRoles.value || []).some(r => r.role_code === defaultRoleCode.value)
  newUser.value = { username: '', display_name: '', roles: has ? [defaultRoleCode.value] : [] }
}

const submitNewUser = async () => {
  const u = (newUser.value.username || '').trim()
  if (!u) { showToast('域账号不能为空'); return }
  userBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/users`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, display_name: newUser.value.display_name, roles: newUser.value.roles }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || `添加失败（HTTP ${res.status}）`); return }
    addUserOpen.value = false
    showToast('用户已添加')
    await loadRbac()
  } catch (e) { showToast('添加失败：' + e.message) } finally { userBusy.value = false }
}

const startEditUser = (u) => {
  editUser.value = u.username
  editUserData.value = { display_name: u.display_name || '', roles: [...(u.roles || [])] }
}
const cancelEditUser = () => { editUser.value = '' }

const saveEditUser = async (u) => {
  userBusy.value = true
  try {
    const r1 = await fetch(`${apiBase}/api/users/${encodeURIComponent(u.username)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: editUserData.value.display_name }),
    })
    const d1 = await r1.json().catch(() => ({}))
    if (!r1.ok || d1.ok === false) { showToast(d1.error || '保存姓名失败'); return }
    const r2 = await fetch(`${apiBase}/api/rbac/users`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u.username, roles: editUserData.value.roles }),
    })
    const d2 = await r2.json().catch(() => ({}))
    if (!r2.ok || d2.ok === false) { showToast(d2.error || '保存角色失败'); return }
    editUser.value = ''
    showToast('已保存')
    await loadRbac()
  } catch (e) { showToast('保存失败：' + e.message) } finally { userBusy.value = false }
}

const removeUser = async (u) => {
  if (!await confirmYes(`确定删除用户 ${u.username}？

删除后该账号无法登录；聊天记录与操作日志会保留（审计可查）。`)) return
  userBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/users/${encodeURIComponent(u.username)}`, { method: 'DELETE' })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) showToast(d.error || `删除失败（HTTP ${res.status}）`)
    else showToast('用户已删除')
    await loadRbac()
  } catch (e) { showToast('删除失败：' + e.message) } finally { userBusy.value = false }
}

// ===== 角色：新增 / 改名 / 删除 =====
const addRoleOpen = ref(false)
const newRole = ref({ code: '', name: '', note: '' })
const roleEdit = ref({ name: '', note: '' })
const isBuiltinRole = (code) => {
  const r = catalogRoles.value.find(x => x.role_code === code)
  return !!(r && r.is_builtin)
}

const submitNewRole = async () => {
  const code = (newRole.value.code || '').trim()
  if (!code) { showToast('角色编码不能为空'); return }
  userBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/rbac/roles`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, name: newRole.value.name || code, note: newRole.value.note }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || `创建失败（HTTP ${res.status}）`); return }
    addRoleOpen.value = false
    newRole.value = { code: '', name: '', note: '' }
    showToast('角色已创建')
    await loadCatalog()
    selectRole(code)
  } catch (e) { showToast('创建失败：' + e.message) } finally { userBusy.value = false }
}

const saveRoleMeta = async () => {
  userBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/rbac/roles/${encodeURIComponent(editRole.value)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: roleEdit.value.name, note: roleEdit.value.note }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || '保存失败'); return }
    await loadCatalog()
  } catch (e) { showToast('保存失败：' + e.message) } finally { userBusy.value = false }
}

const removeRole = async (code) => {
  const r = (catalogRoles.value || []).find(x => x.role_code === code)
  if (r && r.is_builtin) { showToast('系统角色（管理员 / 公用角色）不可删除'); return }
  if (!await confirmYes(`确定删除角色 ${code}？`)) return
  userBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/rbac/roles/${encodeURIComponent(code)}`, { method: 'DELETE' })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || '删除失败'); return }
    editRole.value = ''
    showToast('角色已删除')
    await loadCatalog()
  } catch (e) { showToast('删除失败：' + e.message) } finally { userBusy.value = false }
}

const addRbacUser = () => {
  const name = (rbacNewUser.value || '').trim()
  if (!name) return
  if (rbacUsers.value.some(u => u.username === name)) {
    showToast('该用户已在列表里')
    return
  }
  rbacUsers.value.unshift({ username: name, roles: [defaultRoleCode.value] })
  rbacNewUser.value = ''
}

// ==== MCP 服务（管理员只读：列表 + 测连通 + 重载） ====
const skillTab = ref('skills')
const skillSub = ref('mine')   // 技能页内的子页签：mine 我的技能 / platform 平台技能
// 默认显示：MCP 页签是「平台接了哪些能力」的说明，不该等接口回来才出现。
// 只有后端明确 403（当前用户一个 MCP 工具都没授权）时才隐藏。
const mcpVisible = ref(true)
const mcpCanManage = ref(false)
const mcpEnabled = ref(false)
const mcpServers = ref([])
const mcpLoading = ref(false)
const mcpPage = ref(1)
const mcpEdit = ref('')
const mcpSaving = ref(false)
const mcpForm = ref({ isNew: false, name: '', display_name: '', url: '', headersText: '', enabled: true, remote: false, tool_names: {} })
const mcpFormTools = computed(() => Object.keys(mcpForm.value.tool_names || {}))
const startNewMcp = () => {
  mcpForm.value = { isNew: true, name: '', display_name: '', url: '', headersText: '', enabled: true, remote: true, tool_names: {} }
  mcpEdit.value = '（新增）'
}
const startEditMcp = (srv) => {
  const names = {}
  for (const t of (srv.tools || [])) {
    const v = (srv.tool_display || {})[t] || (srv.tool_names || {})[t] || ''
    names[t] = v === t ? '' : v          // 和原名一样等于没起名，显示占位提示更清楚
  }
  const hdrs = srv.headers || {}
  mcpForm.value = {
    isNew: false,
    name: srv.name,
    display_name: srv.display_name || '',
    url: srv.url || '',
    headersText: Object.entries(hdrs).map(([k, v]) => `${k}: ${v}`).join('\n'),
    enabled: srv.enabled !== false,
    remote: true,
    tool_names: names,
  }
  mcpEdit.value = srv.name
}
// 请求头：逐行 "K: V" → 字典（后端也支持这种文本，这里前端先规范化）
const parseHeaderLines = (text) => {
  const out = {}
  for (const line of String(text || '').split('\n')) {
    const t = line.trim()
    if (!t || !t.includes(':')) continue
    const i = t.indexOf(':')
    const k = t.slice(0, i).trim()
    if (k) out[k] = t.slice(i + 1).trim()
  }
  return out
}
const saveMcpServer = async () => {
  const f = mcpForm.value
  const hdrs = { 'Content-Type': 'application/json' }
  mcpSaving.value = true
  try {
    if (f.isNew) {
      const res = await fetch(`${apiBase}/api/mcp/servers`, {
        method: 'POST',
        headers: hdrs,
        body: JSON.stringify({
          name: String(f.name || '').trim().toLowerCase(),
          transport: 'streamable_http',
          url: String(f.url || '').trim(),
          headers: parseHeaderLines(f.headersText),
          display_name: f.display_name || '',
        }),
      })
      const d = await res.json().catch(() => ({}))
      if (!res.ok || d.ok === false) { showToast(d.error || `新增失败（HTTP ${res.status}）`); return }
      showToast('MCP 服务已接入')
    } else {
      if (f.remote) {
        const res = await fetch(`${apiBase}/api/mcp/servers/${encodeURIComponent(f.name)}`, {
          method: 'PUT',
          headers: hdrs,
          body: JSON.stringify({
            url: String(f.url || '').trim(),
            headers: parseHeaderLines(f.headersText),
            enabled: !!f.enabled,
            display_name: f.display_name || '',
          }),
        })
        const d = await res.json().catch(() => ({}))
        if (!res.ok || d.ok === false) { showToast(d.error || `保存失败（HTTP ${res.status}）`); return }
      }
      const res2 = await fetch(`${apiBase}/api/mcp/servers/${encodeURIComponent(f.name)}`, {
        method: 'PATCH',
        headers: hdrs,
        body: JSON.stringify({ display_name: f.display_name || '', tool_names: f.tool_names || {} }),
      })
      const d2 = await res2.json().catch(() => ({}))
      if (!res2.ok || d2.ok === false) { showToast(d2.error || `保存失败（HTTP ${res2.status}）`); return }
      showToast('MCP 服务配置已保存')
    }
    mcpEdit.value = ''
    await loadMcp()
    await loadMcpMeta()
  } catch (e) {
    showToast('保存失败：' + e.message)
  } finally {
    mcpSaving.value = false
  }
}
const deleteMcp = async (srv) => {
  const label = srv.display_name || srv.name
  if (!window.confirm(`确定删除 MCP 服务「${label}」？它的工具会立即从所有角色下线。`)) return
  try {
    const res = await fetch(`${apiBase}/api/mcp/servers/${encodeURIComponent(srv.name)}`, { method: 'DELETE' })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.error || `删除失败（HTTP ${res.status}）`); return }
    showToast('MCP 服务已删除')
    await loadMcp()
  } catch (e) {
    showToast('删除失败：' + e.message)
  }
}
const catalogMcpMeta = ref({})
const loadMcpMeta = async () => {
  try {
    const res = await fetch(`${apiBase}/api/mcp/servers?page=1&page_size=100`)
    if (!res.ok) return
    const d = await res.json()
    const m = {}
    for (const srv of (d.servers || [])) m[srv.name] = srv
    catalogMcpMeta.value = m
  } catch (e) { /* 不影响主流程 */ }
}
const mcpServerLabel = (name) => (catalogMcpMeta.value[name] && catalogMcpMeta.value[name].display_name) || name
const mcpToolLabel = (toolName, server) => {
  const meta = catalogMcpMeta.value[server] || {}
  const names = meta.tool_names || {}
  return names[toolName] || shortToolName(toolName)
}
const mcpPageSize = 10
const mcpTotal = ref(0)
const mcpPages = computed(() => Math.max(1, Math.ceil(mcpTotal.value / mcpPageSize)))
const goMcpPage = (p) => {
  const target = Math.min(Math.max(1, p), mcpPages.value)
  if (target === mcpPage.value) return
  mcpPage.value = target
  loadMcp()
}
const mcpMsg = ref('')

const mcpRefreshing = ref(false)
const mcpRefreshed = ref(false)
let _mcpRefreshedTimer = null
const refreshMcp = async () => {
  if (mcpRefreshing.value) return
  mcpRefreshing.value = true
  mcpMsg.value = ''
  try {
    await loadMcp()
    mcpRefreshed.value = true               // 按钮短暂显示"已刷新"，不弹全局提示
    if (_mcpRefreshedTimer) clearTimeout(_mcpRefreshedTimer)
    _mcpRefreshedTimer = setTimeout(() => { mcpRefreshed.value = false }, 1200)
  } finally {
    mcpRefreshing.value = false
  }
}

const loadMcp = async (probe = false) => {
  if (!probe) mcpLoading.value = true
  try {
    const qs = `page=${mcpPage.value}&page_size=${mcpPageSize}` + (probe ? '&probe=1' : '')
    const res = await fetch(`${apiBase}/api/mcp/servers?${qs}`)
    if (res.status === 403) {
      mcpVisible.value = false
      if (skillTab.value === 'mcp') skillTab.value = 'skills'
      return
    }
    const data = await res.json()
    mcpVisible.value = true
    mcpCanManage.value = !!data.can_manage
    mcpEnabled.value = !!data.enabled
    mcpServers.value = data.servers || []
    mcpTotal.value = Number(data.total ?? mcpServers.value.length)
    mcpPage.value = Number(data.page ?? mcpPage.value)
    if (data.error) mcpMsg.value = data.error
    // 快速模式先渲染，随后后台探测真实连接状态（用户无需等待）
    if (!probe) setTimeout(() => { loadMcp(true) }, 60)
  } catch (e) {
    mcpMsg.value = `读取 MCP 服务失败：${e.message}`
  } finally {
    mcpLoading.value = false
  }
}

const mcpLoaded = ref(false)
const toolsLoaded = ref(false)
const skillsLoaded = ref(false)

const switchSub = (s) => {
  skillSub.value = s
  if (s === 'platform') {
    // 「已添加」标签要跟我的技能比对，没加载过就先拉一次
    if (!skills.value.length) loadSkills()
    loadPlatform()
  } else {
    loadSkills()
  }
}

const switchTab = (t) => {
  skillTab.value = t
  // 不做页面级缓存：角色权限随时可能被管理员改，每次切过来都重新拉
  // （后端有 Redis 兜着，查询很便宜）
  if (t === 'skills') {
    if (skillSub.value === 'platform') loadPlatform()
    else loadSkills()
  }
  if (t === 'tools') loadTools()
  if (t === 'mcp') { mcpMsg.value = ''; loadMcp() }
}

const traceList = ref([])
const traceTotal = ref(0)
const tracePage = ref(1)
const traceLoading = ref(false)
const traceOpenId = ref(null)
const traceKind = ref('')
const traceStatus = ref('')
const traceTool = ref('')

const traceKindLabel = (k) => ({ tool: '工具', user: '用户', context: '上下文', assistant: '助手', model: '模型', error: '异常' }[k] || k || '事件')

const kindText = (t) => {
  if (t.kind === 'tool') return argSummary(t.args_obj, t.args)
  const txt = String(t.result || '').replace(/\s+/g, ' ').trim()
  return txt.slice(0, 90) || '—'
}

const argSummary = (obj, raw) => {
  if (obj && typeof obj === 'object') {
    const keys = Object.keys(obj)
    if (!keys.length) return '—'
    const first = obj[keys[0]]
    const txt = typeof first === 'string' ? first : JSON.stringify(first)
    return keys[0] + '=' + String(txt || '').slice(0, 70).replace(/\s+/g, ' ')
  }
  return String(raw || '').slice(0, 70).replace(/\s+/g, ' ') || '—'
}

const prettyArgs = (obj, raw) => {
  if (obj && typeof obj === 'object') {
    try { return JSON.stringify(obj, null, 2) } catch (_) { return String(raw || '') }
  }
  return String(raw || '（无参数）')
}

const toggleTrace = (id) => { traceOpenId.value = traceOpenId.value === id ? null : id }

const loadTraces = async (page = 1) => {
  traceLoading.value = true
  try {
    const qs = new URLSearchParams({ page: String(page), size: '100' })
    if (traceKind.value) qs.set('kind', traceKind.value)
    if (traceStatus.value) qs.set('status', traceStatus.value)
    if (traceTool.value.trim()) qs.set('tool', traceTool.value.trim())
    qs.set('session', String(sessionId.value || ''))

    const res = await fetch(`${apiBase}/api/trace?${qs.toString()}`)
    const d = await res.json()
    if (!res.ok) { showToast(d.error || '读取轨迹失败', 'err'); return }
    traceTotal.value = d.total || 0
    tracePage.value = page
    traceList.value = page === 1 ? (d.rows || []) : traceList.value.concat(d.rows || [])
  } catch (e) {
    showToast('读取轨迹失败：' + e.message, 'err')
  } finally {
    traceLoading.value = false
  }
}

const reloadTraces = () => { traceOpenId.value = null; return loadTraces(1) }

const chatSubTab = ref('chat')
const switchToTrace = async () => {
  chatSubTab.value = 'trace'
  await reloadTraces()
}
const openTrace = switchToTrace

const toolsCanManage = ref(false)
import avatarUrl from './assets/avatar-default.svg'
const avatarFailed = ref(false)
const avatarSrc = avatarUrl   // Vite 会把 src/assets 里的资源打进 dist/assets 并给带哈希的 URL
const toolsTab = ref('tools')
const switchToolsTab = async (t) => {
  toolsTab.value = t
  if (t === 'mcp') await loadMcp()
}
// 工具按分组展示（先「人人可用」相关组，再业务组，「其他」最后）
const toolGroups = computed(() => {
  const map = new Map()
  for (const t of toolList.value) {
    const k = t.grp || '其他'
    if (!map.has(k)) map.set(k, [])
    map.get(k).push(t)
  }
  const order = (n) => (n === '其他' ? 99 : (n === '业务' ? 50 : 10))
  const all = [...map.entries()]
    .map(([name, items]) => ({ name, items }))
    .sort((a, b) => order(a.name) - order(b.name) || a.name.localeCompare(b.name))
  // 按「组」分页：一组不会被拆到两页 —— 每页 toolPageSize 组
  const start = (toolPage.value - 1) * toolGroupPageSize
  return all.slice(start, start + toolGroupPageSize)
})

// 用作分页的组总数（供 pager 用）
const toolGroupTotal = computed(() => {
  const set = new Set(toolList.value.map((t) => t.grp || '其他'))
  return set.size
})

const toolQ = ref('')
const toolPage = ref(1)
const toolPageSize = 10
const toolTotal = ref(0)
const toolGroupPageSize = 10   // 组数不多，一页显示完；将来组多了再调小
const toolPages = computed(() => Math.max(1, Math.ceil(toolGroupTotal.value / toolGroupPageSize)))
const goToolPage = (p) => {
  const target = Math.min(Math.max(1, p), toolPages.value)
  if (target === toolPage.value) return
  toolPage.value = target
  loadTools()
}
const toolEdit = ref('')
const toolSaving = ref(false)
const toolForm = ref({ display_name: '', note: '', grp: '', risk: 'read', always_on: false, enabled: true })
const startEditTool = (t) => {
  toolEdit.value = t.name
  toolForm.value = {
    display_name: t.display_name || t.name,
    note: t.note || '', grp: t.grp || '', risk: t.risk || 'read',
    always_on: !!t.always_on, enabled: t.enabled !== false,
  }
}
// 工具管理：一键切换「公共」（= 人人可用，不参与角色分配）
const toggleToolPublic = async (t) => {
  const want = !t.always_on
  toolSaving.value = true
  try {
    const res = await fetch(`${apiBase}/api/tools/${encodeURIComponent(t.name)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ always_on: want }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) {
      showToast(d.error || '修改失败', 'err')
      return
    }
    t.always_on = want
    showToast(want ? `「${t.display_name || t.name}」已设为公共：所有角色自动拥有` : '已取消公共', 'ok')
  } catch (e) {
    showToast('修改失败：' + e.message, 'err')
  } finally {
    toolSaving.value = false
  }
}

// MCP 服务：整个服务一起设为「公共」/取消（名下所有工具）
const toggleMcpPublic = async (srv) => {
  const want = !srv.public
  userBusy.value = true
  try {
    const res = await fetch(`${apiBase}/api/mcp/servers/${encodeURIComponent(srv.name)}/public`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ public: want }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) {
      showToast(d.error || '修改失败', 'err')
      return
    }
    srv.public = want
    showToast(want ? `「${srv.display_name || srv.name}」全部工具已设为公共` : '已取消公共', 'ok')
    await loadMcp()
  } catch (e) {
    showToast('修改失败：' + e.message, 'err')
  } finally {
    userBusy.value = false
  }
}

const saveTool = async (name) => {
  toolSaving.value = true
  try {
    const res = await fetch(`${apiBase}/api/tools/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(toolForm.value),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) {
      showToast(d.error || `保存失败（HTTP ${res.status}）`)
    } else {
      showToast('工具配置已保存')
      toolEdit.value = ''
      await loadTools()
    }
  } catch (e) {
    showToast('保存失败：' + e.message)
  } finally {
    toolSaving.value = false
  }
}

// 角色页：内置工具按分组展示
const roleBuiltinGranted = computed(() => {
  const ts = roleTools.value
  if (ts.includes('*')) return catalogBuiltin.value.length
  const always = catalogBuiltin.value.filter((t) => t.always_on).length
  return always + catalogBuiltin.value.filter((t) => !t.always_on && ts.includes(t.name)).length
})

const roleMcpGranted = computed(() => {
  const ts = roleTools.value
  if (ts.includes('*')) return catalogMcp.value.length
  return catalogMcp.value.filter((t) => ts.includes(t.name)).length
})

// mcp_demo_server_time → "server time"（服务前缀已由分组标题表达）
// MCP 工具显示名：优先 pa_tools 里的中文名 → 服务自定义名 → 截短代码
const mcpListLabel = (srv, tname) =>
  (srv.tool_display || {})[tname] || (srv.tool_names || {})[tname] || shortToolName(tname)
const mcpListNote = (srv, tname) => (srv.tool_notes || {})[tname] || ''

const shortToolName = (name) => {
  const raw = String(name || '')
  const m = raw.match(/^mcp_[^_]+_(.+)$/)
  return (m ? m[1] : raw).replace(/_/g, ' ')
}

const catalogMcpGroups = computed(() => {
  const map = new Map()
  for (const t of catalogMcp.value) {
    const key = t.server || '未标注服务'
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(t)
  }
  return [...map.entries()].map(([server, items]) => ({
    server,
    items,
    granted: items.filter((t) => t.always_on || hasTool(t.name)).length,
  }))
})

const catalogBuiltinGroups = computed(() => {
  const m = new Map()
  for (const t of catalogBuiltin.value) {
    const g = t.grp || '其他'
    if (!m.has(g)) m.set(g, [])
    m.get(g).push(t)
  }
  return [...m.entries()].map(([name, items]) => ({ name, items }))
})

// ==== 内置工具（只读清单） ====
const toolList = ref([])
// 技能与工具页：按分组展示（和角色管理同一套排版）
const toolsGroups = computed(() => {
  const m = new Map()
  for (const t of (toolList.value || [])) {
    const g = t.grp || '其他'
    if (!m.has(g)) m.set(g, [])
    m.get(g).push(t)
  }
  return [...m.entries()].map(([name, items]) => ({ name, items }))
})
const toolsLoading = ref(false)
const toolsMsg = ref('')

const loadTools = async () => {
  toolsLoading.value = true
  toolsMsg.value = ''
  try {
    // 取全量：分组要跨页才完整，所以后端不分页，前端按「组」分页
    const qs = new URLSearchParams({ page: '1', page_size: '999' })
    if (toolQ.value.trim()) qs.set('q', toolQ.value.trim())
    const res = await fetch(`${apiBase}/api/tools?${qs.toString()}`)
    const data = await res.json()
    toolsCanManage.value = !!data.can_manage
    const rows = data.tools || []
    // 普通用户：只展示有权限的；管理员：全展示（含已停用，方便重新启用）
    toolList.value = toolsCanManage.value ? rows : rows.filter((t) => t.visible !== false)
    toolTotal.value = Number(data.total ?? toolList.value.length)
    toolPage.value = Number(data.page ?? toolPage.value)
    if (data.error) toolsMsg.value = data.error
  } catch (e) {
    toolsMsg.value = `读取内置工具失败：${e.message}`
  } finally {
    toolsLoading.value = false
  }
}

const testMcp = async (name) => {
  mcpMsg.value = `正在连接 ${name} ...`
  try {
    const res = await fetch(`${apiBase}/api/mcp/servers/${encodeURIComponent(name)}/test`, { method: 'POST' })
    const data = await res.json()
    mcpMsg.value = data.ok
      ? `✅ ${name} 连接成功，提供 ${(data.tools || []).length} 个工具：${(data.tools || []).map(t => t.name).join('、')}`
      : `❌ ${name} 连接失败：${data.error || '未知错误'}`
    await loadMcp()
  } catch (e) {
    mcpMsg.value = `❌ ${e.message}`
  }
}

const reloadMcp = async () => {
  mcpMsg.value = '正在重载...'
  try {
    const res = await fetch(`${apiBase}/api/mcp/reload`, { method: 'POST' })
    const data = await res.json()
    mcpMsg.value = data.ok
      ? `✅ 已断开全部 MCP 连接，下次对话重新接入（清理 agent 缓存 ${data.agents_cleared ?? 0} 个）`
      : `❌ ${data.error || '重载失败'}`
    await loadMcp()
  } catch (e) {
    mcpMsg.value = `❌ ${e.message}`
  }
}

const uploadSkillFile = async (ev) => {
  const f = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!f) return
  const fd = new FormData()
  fd.append('file', f)
  skillMsg.value = '上传中...'
  try {
    const res = await fetch(`${apiBase}/api/skills/upload`, { method: 'POST', body: fd })
    const data = await res.json()
    skillMsg.value = res.ok
      ? `✅ 已安装 ${data.name}（${data.files} 个文件${data.warnings && data.warnings.length ? '，' + data.warnings.join('；') : ''}）`
      : `❌ ${data.error || '上传失败'}`
    await loadSkills()
  } catch (e) {
    skillMsg.value = `❌ 上传失败：${e.message}`
  }
}

const toggleSkill = async (sk) => {
  const act = sk.enabled ? 'disable' : 'enable'
  const before = !!sk.enabled
  // 真正的乐观更新：**先**改界面（沙箱操作要 1~4 秒），失败再回滚
  sk.enabled = (act === 'enable')
  try {
    const res = await fetch(`${apiBase}/api/skills/${encodeURIComponent(sk.name)}/${act}`, { method: 'POST' })
    const data = await res.json()
    if (!res.ok) {
      sk.enabled = before
      skillMsg.value = `❌ ${data.error || '操作失败'}`
      return
    }
    skillMsg.value = `✅ ${sk.name} 已${act === 'disable' ? '停用' : '启用'}（下一条消息生效）`
    loadSkills()
  } catch (e) {
    sk.enabled = before
    skillMsg.value = `❌ ${e.message}`
  }
}

const removeSkill = async (sk) => {
  if (!await confirmYes(`删除技能「${sk.name}」？会移到沙箱回收站（/sandbox/skills-trash），可手工找回。`)) return
  const before = skills.value.slice()
  // 先从列表移除（后端要在沙箱里 mv 目录，约 4 秒），失败再放回去
  skills.value = skills.value.filter((x) => x.name !== sk.name)
  try {
    const res = await fetch(`${apiBase}/api/skills/${encodeURIComponent(sk.name)}`, { method: 'DELETE' })
    const data = await res.json()
    if (!res.ok) {
      skills.value = before
      skillMsg.value = `❌ ${data.error || '删除失败'}`
      return
    }
    skillMsg.value = `✅ 已删除 ${sk.name}`
    loadSkills()
  } catch (e) {
    skills.value = before
    skillMsg.value = `❌ ${e.message}`
  }
}

// ===== 平台技能：全体共享的技能库 =====
const platformSkills = ref([])
const platformLoading = ref(false)
const platformMsg = ref('')
const platformIsAdmin = ref(false)
const publishing = ref('')   // 正在发布到平台的技能名（按钮显示「发布中…」并禁用）

const loadPlatform = async () => {
  platformLoading.value = true
  try {
    const res = await fetch(`${apiBase}/api/platform-skills`, { cache: 'no-store' })
    const data = await res.json()
    if (!res.ok) {
      platformMsg.value = `❌ ${data.error || '读取平台技能失败'}`
      return
    }
    platformSkills.value = data.skills || []
    platformIsAdmin.value = !!data.is_admin
  } catch (e) {
    platformMsg.value = `❌ 读取平台技能失败：${e.message}`
  } finally {
    platformLoading.value = false
  }
}

// 平台条目 → 我沙箱里是否已有（按基础名比对，因为 v2 落到沙箱是同一个名字）
const platformAdded = computed(() => {
  const mine = new Set(skills.value.map((s) => s.name))
  const m = {}
  for (const ps of platformSkills.value) m[ps.name] = mine.has(ps.base_name || ps.name)
  return m
})

const publishSkill = async (sk) => {
  if (!await confirmYes(`把「${sk.name}」设为平台技能？\n平台所有用户都能看到并添加它；删除平台版本需要管理员或你本人。`)) return
  publishing.value = sk.name
  skillMsg.value = `正在发布 ${sk.name} 到平台（约 10~15 秒）...`
  try {
    const res = await fetch(`${apiBase}/api/platform-skills`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: sk.name }),
    })
    const data = await res.json()
    if (!res.ok) {
      skillMsg.value = `❌ ${data.error || '发布失败'}`
      return
    }
    skillMsg.value = `✅ 已发布到平台：${data.name}${data.version > 1 ? `（第 ${data.version} 版）` : ''}`
  } catch (e) {
    skillMsg.value = `❌ ${e.message}`
  } finally {
    publishing.value = ''
  }
}

const addPlatformSkill = async (ps, force = false) => {
  const local = ps.base_name || ps.name
  const exists = !!platformAdded.value[ps.name]
  if (exists && !force) {
    if (!await confirmYes(`你的沙箱里已有「${local}」，用平台版本覆盖？旧版会归档保存。`)) return
    force = true
  }
  platformMsg.value = `正在添加 ${ps.name} ...`
  try {
    const res = await fetch(`${apiBase}/api/platform-skills/${encodeURIComponent(ps.name)}/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overwrite: force }),
    })
    const data = await res.json()
    if (res.status === 409) {
      if (!force && await confirmYes(`你的沙箱里已有「${data.local_name || local}」，覆盖？旧版会归档保存。`)) {
        return addPlatformSkill(ps, true)
      }
      return
    }
    if (!res.ok) {
      platformMsg.value = `❌ ${data.error || '添加失败'}`
      return
    }
    platformMsg.value = `✅ 已添加到你的沙箱：${data.local_name || local}`
    await loadSkills()
  } catch (e) {
    platformMsg.value = `❌ ${e.message}`
  }
}

const removePlatformSkill = async (ps) => {
  if (!await confirmYes(`从平台删除「${ps.name}」？\n已添加到沙箱的用户不受影响，但他们以后不能再添加。`)) return
  const before = platformSkills.value.slice()
  platformSkills.value = platformSkills.value.filter((x) => x.name !== ps.name)
  try {
    const res = await fetch(`${apiBase}/api/platform-skills/${encodeURIComponent(ps.name)}`, { method: 'DELETE' })
    const data = await res.json()
    if (!res.ok) {
      platformSkills.value = before
      platformMsg.value = `❌ ${data.error || '删除失败'}`
      return
    }
    platformMsg.value = `✅ 已从平台删除 ${ps.name}`
  } catch (e) {
    platformSkills.value = before
    platformMsg.value = `❌ ${e.message}`
  }
}

const uploadPlatformFile = async (ev) => {
  const f = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!f) return
  const fd = new FormData()
  fd.append('file', f)
  platformMsg.value = '上传中...'
  try {
    const res = await fetch(`${apiBase}/api/platform-skills/upload`, { method: 'POST', body: fd })
    const data = await res.json()
    platformMsg.value = res.ok
      ? `✅ 已上传 ${data.name}${data.version > 1 ? `（第 ${data.version} 版）` : ''}`
      : `❌ ${data.error || '上传失败'}`
    await loadPlatform()
  } catch (e) {
    platformMsg.value = `❌ 上传失败：${e.message}`
  }
}

const fetchCurrentUser = async () => {
  try {
    const res = await fetch(`${apiBase}/api/user/currentUser`)
    if (!res.ok) return
    const data = await res.json()
    if (data.username) {
      username.value = data.username
      // 优先用中文名，USC 未返回时兜底为域账号首字母
      displayName.value = data.displayName || data.username
    }
  } catch (e) { console.error('获取用户信息失败:', e) }
}

const generateSessionId = () => {
  try { return crypto.randomUUID().replace(/-/g, '') } catch {}
  return 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}
const username = ref(config.username)
const displayName = ref(config.displayName || config.username)
onMounted(() => { document.addEventListener('click', closeUserMenu) })
onBeforeUnmount(() => { document.removeEventListener('click', closeUserMenu) })

const sessionId = ref(config.sessionId || generateSessionId())

// 切到「轨迹」标签或切换会话时自动拉取（必须放在 sessionId 定义之后）
watch(activeTab, (v) => { if (v === 'files') loadDir(curPath.value); if (v === 'trace') loadTraces(); if (v === 'sys-audit') loadAudit(0) })
watch(sessionId, () => { if (activeTab.value === 'trace') loadTraces() })

// 会话消息缓存淘汰（LRU，最多 MSG_CACHE_MAX 个会话；当前会话永不淘汰）
const touchMsgCache = (sid) => {
  if (!sid) return
  const i = msgCacheOrder.indexOf(sid)
  if (i >= 0) msgCacheOrder.splice(i, 1)
  msgCacheOrder.push(sid)
  for (const drop of evictMsgCache(msgCacheOrder, MSG_CACHE_MAX, sessionId.value)) {
    delete messagesBySession.value[drop]
  }
}
watch(sessionId, (v) => touchMsgCache(v))

const apiBase = config.apiBase

// 当前会话的响应式视图（依赖 sessionId，必须在其声明之后）
const messages = computed(() => messagesBySession.value[sessionId.value] || [])
const loading = computed(() => !!loadingBySession.value[sessionId.value])

const formatTime = (isoStr) => {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diffDays = Math.floor((today - target) / 86400000)
  const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  if (diffDays === 0) return time
  if (diffDays === 1) return `昨天 ${time}`
  if (d.getFullYear() === now.getFullYear()) {
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getDate().toString().padStart(2, '0')} ${time}`
  }
  return `${d.getFullYear()}/${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getDate().toString().padStart(2, '0')} ${time}`
}

const showArchived = ref(false)
const prefs = ref([])
const prefsMax = ref(10)
const prefInput = ref('')
const loadPrefs = async () => {
  try {
    const res = await fetch(`${apiBase}/api/prefs`)
    if (!res.ok) { showToast('读取偏好失败', 'err'); return }
    const d = await res.json()
    prefs.value = d.items || []
    prefsMax.value = d.max_items || 10
  } catch (e) { showToast('读取偏好失败：' + e.message, 'err') }
}
const openPrefs = async () => { await openMeTab('prefs') }
const addPref = async () => {
  const text = prefInput.value.trim()
  if (!text) return
  try {
    const res = await fetch(`${apiBase}/api/prefs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: text }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok || d.ok === false) { showToast(d.message || '添加失败', 'err'); return }
    showToast(d.message || '已添加')
    prefInput.value = ''
    await loadPrefs()
  } catch (e) { showToast('添加失败：' + e.message, 'err') }
}
const delPref = async (id) => {
  try {
    const res = await fetch(`${apiBase}/api/prefs/${id}`, { method: 'DELETE' })
    if (!res.ok) { showToast('删除失败', 'err'); return }
    showToast('已删除')
    await loadPrefs()
  } catch (e) { showToast('删除失败：' + e.message, 'err') }
}
const archiveSession = async (sid) => {
  try {
    const res = await fetch(`${apiBase}/api/sessions/${sid}/archive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ archived: !showArchived.value }),
    })
    const d = await res.json().catch(() => ({}))
    if (!res.ok) { showToast(d.error || `操作失败（HTTP ${res.status}）`, 'err'); return }
    showToast(showArchived.value ? '已恢复到列表' : '已归档')
    sessionsPage.value = 0
    sessions.value = []
    await loadSessions()
  } catch (e) { showToast('操作失败：' + e.message, 'err') }
}
const toggleArchivedView = async () => {
  showArchived.value = !showArchived.value
  sessions.value = []
  await loadSessions()
}
const loadSessions = async (append = false, keepPageCount = false) => {
  if (loadingSessions.value) return
  // keepPageCount：每轮对话结束后刷新侧边栏时用，别把翻到第 3 页的人弹回第 1 页
  const pageSize = keepPageCount ? windowSizeFor(sessions.value.length) : 20
  const page = append ? sessionsPage.value + 1 : 1
  loadingSessions.value = true
  try {
    const res = await fetch(`${apiBase}/api/sessions?page=${page}&page_size=${pageSize}&archived=${showArchived.value ? 1 : 0}`)
    const result = await res.json()
    if (!res.ok) return
    const list = (result.data || []).map(s => ({
      id: s.id,
      session_id: s.session_id,
      title: s.title,
      time: formatTime(s.updated_at)
    }))
    if (append) {
      sessions.value = [...sessions.value, ...list]
    } else {
      sessions.value = list
    }
    // keepPageCount 时窗口是"一次取前面 N 条"，页数要按窗口大小换算，否则下次滚动加载会重复
    sessionsPage.value = keepPageCount
      ? pageForWindow(pageSize)
      : (result.page || page)
    sessionsTotal.value = result.total || 0
  } catch (e) { console.error('加载会话列表失败:', e); if (!append) sessions.value = [] }
  finally { loadingSessions.value = false }
}

// 发送消息的瞬间，本地把新会话插到列表顶部（不等后端响应）
const optimisticUpsertSession = (sid, title) => {
  const now = new Date()
  const existing = sessions.value.find(s => s.session_id === sid)
  const time = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  if (existing) {
    // 已存在：更新标题和时间，移到顶部
    existing.title = title
    existing.time = time
    sessions.value = [existing, ...sessions.value.filter(s => s.session_id !== sid)]
  } else {
    sessions.value = [{ id: null, session_id: sid, title: title || '新对话', time }, ...sessions.value]
  }
}

const onSidebarScroll = () => {
  const el = sidebarBodyRef.value
  if (!el || loadingSessions.value) return
  const hasMore = sessions.value.length < sessionsTotal.value
  if (!hasMore) return
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) {
    loadSessions(true)
  }
}

// 拉取某个会话的消息并填充到本地（幂等：已加载过就不再请求）
// 抽成独立函数的原因：刷新后 onMounted 用 URL 里的 session_id 调 switchSession，
// 而那时 sessionId.value 已经等于该 id → 旧代码的 `if (id === sessionId.value) return`
// 守卫会直接返回，导致「刷新后能看到会话名、却看不到聊天记录」。
const loadMessages = async (id) => {
  if (messagesBySession.value[id]) return
  loadingBySession.value[id] = true
  try {
    const res = await fetch(`${apiBase}/api/sessions/${id}/messages`)
    const data = await res.json()
    if (!res.ok) return
    if (Array.isArray(data)) {
      // 从 MySQL 恢复消息；assistant 消息若带 options 字段则恢复复选框
      const list = data.map(m => {
        const item = {
          role: m.role === 'agent' ? 'assistant' : 'user',
          content: m.content
        }
        if (m.role === 'agent' && m.options) {
          try {
            const opts = JSON.parse(m.options)
            if (opts && Array.isArray(opts.options) && opts.options.length) {
              item.options = { question: opts.question || '', options: opts.options }
              item.optionsSelected = []
              item.optionsChosen = null
            }
          } catch (e) { console.error('解析 options 失败:', e) }
        }
        return item
      })
      // 刷新后历史里的选项按钮本来是"可再点"的：但如果这条消息后面还有别的消息，
      // 说明当时已经答过了，再点一次等于把同一个选择重复提交（白跑一轮、可能触发写操作）。
      // 这里按"后面还有消息 → 视为已作答"上锁；真正的状态仍以后端校验为准。
      for (let i = 0; i < list.length - 1; i++) {
        if (list[i].options && !list[i].optionsChosen) list[i].optionsChosen = ['（已作答）']
      }
      messagesBySession.value[id] = list
      touchMsgCache(id)
    } else {
      messagesBySession.value[id] = []
      touchMsgCache(id)
    }
  } catch (e) { console.error('加载会话消息失败:', e) } finally {
    loadingBySession.value[id] = false
  }
}

const switchSession = async (id) => {
  // 切换视图，不断开当前会话的后台生成
  activeTab.value = 'chat'   // 点了会话就回到会话 tab
  // 若当前停在「轨迹」子页签，切会话要回到「对话」——否则看不到新会话的内容
  if (chatSubTab.value === 'trace') chatSubTab.value = 'chat'
  if (id !== sessionId.value) {
    sessionId.value = id
    inputText.value = ''
    selectedFiles.value = []
  }
  // 打开哪个会话就把它写进 URL：刷新后能回到同一会话，而不是掉回空白新会话
  persistSessionId(id)
  // 无论 id 是否与当前相同，都确保该会话的消息已加载（loadMessages 内部幂等）
  await loadMessages(id)
  await nextTick()
  scrollToBottom()
}

const deleteSession = async (sid) => {
  // 删除不可恢复：先让用户确认（以前是点了就删，容易误删）
  const _s = sessions.value.find((x) => x.session_id === sid)
  const _title = (_s && _s.title) ? String(_s.title).slice(0, 30) : '该会话'
  if (!await confirmYes(`删除会话「${_title}」？对话内容会一并删除，不可恢复。`)) return
  sessions.value = sessions.value.filter(s => s.session_id !== sid)
  // 取消被删除会话的后台生成（若有）
  if (abortBySession[sid]) {
    abortBySession[sid].abort()
    delete abortBySession[sid]
  }
  delete loadingBySession.value[sid]
  delete messagesBySession.value[sid]
  try {
    const res = await fetch(`${apiBase}/api/sessions/${sid}`, { method: 'DELETE' })
    if (!res.ok) return
  } catch (e) { console.error('删除会话失败:', e) }
  if (sid === sessionId.value) {
    chatSubTab.value = 'chat'
    sessionId.value = generateSessionId()
    messagesBySession.value[sessionId.value] = []
    inputText.value = ''
    selectedFiles.value = []
    // 必须同步到 URL：否则刷新页面时仍拿"已删除的会话号"去恢复，
    // 表现为侧栏有会话、聊天区空白（历史"消失"）
    persistSessionId(sessionId.value)
  }
}

const createNewSession = () => {
  // 新建会话只切换视图，不断开任何正在后台生成的会话
  activeTab.value = 'chat'   // 新对话 = 回到会话 tab
  sessionId.value = generateSessionId()
  messagesBySession.value[sessionId.value] = []
  inputText.value = ''
  // selectedFile 是只读 computed（selectedFiles[0]），给它赋值无效 → 必须清数组，
  // 否则上一个会话选的附件会跟着发到新会话里
  selectedFiles.value = []
  persistSessionId(sessionId.value)   // 同上：新会话号要立刻写回 URL
}

const autoResize = () => {
  const el = taRef.value
  if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px' }
}

const scrollToBottom = async () => {
  await nextTick()
  if (messagesRef.value) messagesRef.value.scrollTop = messagesRef.value.scrollHeight
}

// 与后端 ChatController._ALLOWED_EXT 保持一致
const SUPPORTED_EXTS = [
  'xlsx', 'xls', 'csv',
  'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp',
  'pdf', 'doc', 'docx', 'ppt', 'pptx', 'rtf', 'odt', 'ods', 'odp',
  'zip', 'rar', '7z', 'tar', 'gz',
  'txt', 'md', 'json', 'xml', 'html', 'htm', 'yaml', 'yml', 'log'
]
const ACCEPT_ATTR = SUPPORTED_EXTS.map(e => '.' + e).join(',')
// 上传口径（与后端 TOTAL_UPLOAD_BYTES 一致）：**文件个数不限，单次总大小 ≤ 100MB**
const MAX_TOTAL_MB = 100
const selectedBytes = computed(() => selectedFiles.value.reduce((s, f) => s + (f.size || 0), 0))
const selectedTotalMB = computed(() => selectedBytes.value / 1048576)

const pickFile = (f) => {
  if (!f) return
  const ext = (f.name.split('.').pop() || '').toLowerCase()
  if (!SUPPORTED_EXTS.includes(ext)) {
    showToast(`不支持的文件类型 .${ext}（支持表格/图片/办公文档/压缩包/文本）`)
    return
  }
  if (selectedFiles.value.some(x => x.name === f.name && x.size === f.size)) return
  // 个数不限制，只卡总大小：超了直接拒绝这一个文件，已选的保持不动
  const nextTotal = selectedBytes.value + (f.size || 0)
  if (nextTotal > MAX_TOTAL_MB * 1048576) {
    showToast(`单次上传总大小不能超过 ${MAX_TOTAL_MB}MB（已选 ${selectedTotalMB.value.toFixed(1)}MB，`
      + `再加这个文件会到 ${(nextTotal / 1048576).toFixed(1)}MB）`)
    return
  }
  selectedFiles.value = [...selectedFiles.value, f]
}

const handleFileSelect = (e) => {
  // 文件框是 multiple，之前只取 files[0]：一次选 3 个只传第 1 个且无任何提示
  Array.from((e.target.files || [])).forEach(pickFile)
  e.target.value = ''
}

// 整窗口拖拽上传：拖到页面任意位置松手都会选中该文件
const dragActive = ref(false)
let _dragDepth = 0
const onDragEnter = (e) => {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return
  e.preventDefault(); _dragDepth++; dragActive.value = true
}
const onDragOver = (e) => {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return
  e.preventDefault(); e.dataTransfer.dropEffect = 'copy'
}
const onDragLeave = () => {
  if (_dragDepth > 0) _dragDepth--
  if (_dragDepth === 0) dragActive.value = false
}
const onDrop = (e) => {
  if (!e.dataTransfer) return
  e.preventDefault(); _dragDepth = 0; dragActive.value = false
  Array.from(e.dataTransfer.files || []).forEach(pickFile)
}
if (typeof window !== 'undefined') {
  window.addEventListener('dragenter', onDragEnter)
  window.addEventListener('dragover', onDragOver)
  window.addEventListener('dragleave', onDragLeave)
  window.addEventListener('drop', onDrop)
}

const clearFile = () => { selectedFiles.value = [] }
const removeFileAt = (i) => { selectedFiles.value = selectedFiles.value.filter((_, k) => k !== i) }

// 把当前 sessionId 写入 URL query，刷新后能恢复同一会话
const persistSessionId = (sid) => {
  try {
    const url = new URL(window.location.href)
    if (url.searchParams.get('session_id') !== sid) {
      url.searchParams.set('session_id', sid)
      window.history.replaceState(null, '', url.toString())
    }
  } catch (e) { /* 忽略：URL 持久化失败不影响主流程 */ }
}

onMounted(async () => {
  // 从后端获取 SSO 登录后的用户信息（中文名等）
  await fetchCurrentUser()
  // 预热沙箱：登录接口已经预热过一次，但登录会话活得久，第二天直接打开页面
  // 不会重走登录；这里补一刀，让容器在用户看界面时就起来（接口立即返回，不阻塞）
  fetch(`${apiBase}/api/warm`, { method: 'GET' }).catch(() => {})
  await loadSessions()
  // 先问"我是不是管理员"（/api/rbac/me 所有人可读），**只有管理员**才去拉
  // /api/users（那个接口非管理员必 403：以前每次打开页面都白打一次、还刷一条红字）
  await loadMe()
  if (isAdmin.value) loadRbac()
  // 若 URL 带 session_id（或 embed 配置），刷新后恢复该会话的消息和选项
  if (config.sessionId) {
    await switchSession(config.sessionId)
    // 这个 id 可能已被删除（或不属于当前用户）：列表里查不到就换成新会话号，
    // 否则会停在"侧栏没有这一条、聊天区空白"的状态
    if (!sessions.value.some((x) => x.session_id === config.sessionId)) {
      sessionId.value = generateSessionId()
      messagesBySession.value[sessionId.value] = []
      persistSessionId(sessionId.value)
    }
  }
})

// 展开侧边栏时刷新会话列表
watch(sidebarCollapsed, (val) => {
  if (!val) loadSessions()
})

// SSE 流式响应解析（共享于纯文本和图片上传）
// sid: 发起请求时的会话 ID（闭包捕获），切换会话后流仍写回原会话
const handleSSEStream = async (res, signal, throttledScroll, sid) => {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let assistantIdx = -1
  const msgs = () => messagesBySession.value[sid] || []

  // 无事件看门狗：网关半开/模型挂起时 reader.read() 可能永远不返回，
  // loading 就永远不释放（输入框、发送、选项全锁死）。超时直接 abort 并提示。
  // 阈值给 300s：沙箱创建/长命令可能有几分钟没有任何 SSE 事件。
  const STALL_MS = 300000
  let lastTs = Date.now()
  const watchdog = setInterval(() => {
    if (Date.now() - lastTs < STALL_MS) return
    clearInterval(watchdog)
    const m = assistantIdx >= 0 ? msgs()[assistantIdx] : null
    const tip = '⚠️ 长时间没有收到结果，已自动停止（可以重试，或换一种问法）'
    if (m) m.content = (m.content || '') + '\n\n' + tip
    else messagesBySession.value[sid] = [...msgs(), { role: 'assistant', content: tip }]
    try { signal && signal.abort() } catch (e) { /* 忽略 */ }
  }, 5000)

  try {
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    lastTs = Date.now()
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const parsed = JSON.parse(line.slice(6))
          if (parsed.error) {
            // 不要覆盖已经流式输出的正文：中断/异常时那段回答仍有效
            const errText = `⚠️ ${parsed.error}`
            if (assistantIdx >= 0 && msgs()[assistantIdx]) {
              const cur = msgs()[assistantIdx].content || ''
              msgs()[assistantIdx].content = cur ? `${cur}\n\n${errText}` : errText
            } else {
              messagesBySession.value[sid] = [...msgs(), { role: 'assistant', content: errText }]
            }
            loadingBySession.value[sid] = false
            return
          }
          if (parsed.typing === false) {
            // 正文结束（服务端后面还有产物回传/落库/轨迹，done 会晚十几秒）：
            // 先撤掉"转圈"，让用户可以继续操作；流继续在后台读完。
            loadingBySession.value[sid] = false
            if (assistantIdx >= 0 && msgs()[assistantIdx]) {
              const m0 = msgs()[assistantIdx]
              if (m0.thinkingLive) {
                m0.thinkingLive = false
                m0.thinkingMs = Date.now() - (m0.thinkingT0 || Date.now())
                m0.processOpen = false
              }
              if (m0.steps) m0.steps.forEach(x => { if (x.status === 'running') x.status = 'done' })
            }
            if (sid === sessionId.value) throttledScroll()
            continue
          }
          if (parsed.done) {
            // 只有当前会话才更新 sessionId（后台流的 done 不应覆盖当前视图）
            if (sid === sessionId.value) {
              sessionId.value = parsed.session_id || sessionId.value
            }
            // ★ 立刻解锁界面：done = 本轮语义已结束。
            // 服务端在 done 之后还要做上下文治理（滚动摘要可能调一次模型，几十秒），
            // 那段时间 HTTP 流不会关闭；以前要等流关闭才清 loading，
            // 用户就会看到「回答完了下面还在转圈」。流继续在后台读完即可。
            loadingBySession.value[sid] = false
            // 本轮收尾：思考块定稿（折叠 + 耗时），未回状态的工具步骤标记完成
            if (assistantIdx >= 0) {
              const m = msgs()[assistantIdx]
              if (m && m.thinkingLive) {
                m.thinkingLive = false
                m.thinkingMs = Date.now() - (m.thinkingT0 || Date.now())
                m.processOpen = false
              }
              if (m && m.steps) m.steps.forEach(x => { if (x.status === 'running') x.status = 'done' })
            }
            // 无论哪个会话完成，都刷新侧边栏列表（新会话出现/时间更新/「生成中」消失）
            // keepPageCount：保留当前翻到的页数，别把人弹回第 1 页
            loadSessions(false, true)
          } else if (parsed.url) {
            // 安全红线：只允许 http(s) 与站内路径。后端也可能被提示注入产出
            // javascript:/data: 链接，而这里会直接 window.open / a.click（同源执行脚本）。
            if (!isSafeNavUrl(parsed.url)) {
              console.warn('已忽略不安全的导航 URL:', parsed.url)
            } else {
            if (assistantIdx >= 0) {
              msgs()[assistantIdx].content = ''
            }
            if (window.parent !== window) {
              window.parent.postMessage({
                hostname: parsed.url,
                name: parsed.name || 'newTab',
                methods: 'newTab'
              }, '*')
            } else {
              const w = window.open(parsed.url, '_blank')
              if (!w) {
                const a = document.createElement('a')
                a.href = parsed.url
                a.target = '_blank'
                a.rel = 'noopener'
                a.click()
              }
            }
            }
          } else if (parsed.reset) {
            // 模型重新生成（空回复自动重试）→ 清掉本轮已展示的思考/步骤，避免两轮堆叠
            if (assistantIdx >= 0) {
              const m = msgs()[assistantIdx]
              m.thinking = ''
              m.steps = []
              m.thinkingLive = true
              m.thinkingT0 = Date.now()
            }
          } else if (parsed.reasoning) {
            // 思考片段：**只进内存，不入库**（刷新后从 MySQL 读的历史里没有它）
            if (assistantIdx < 0) {
              assistantIdx = msgs().length
              messagesBySession.value[sid] = [...msgs(), {
                role: 'assistant', content: '', thinking: '', steps: [],
                thinkingLive: true, thinkingT0: Date.now()
              }]
            }
            const m = msgs()[assistantIdx]
            m.thinking = (m.thinking || '') + parsed.reasoning
            if (sid === sessionId.value) throttledScroll()
          } else if (parsed.step) {
            // 工具步骤卡片（正在调用工具 / 正在沙箱执行命令…）：同样只进内存
            if (assistantIdx < 0) {
              assistantIdx = msgs().length
              messagesBySession.value[sid] = [...msgs(), {
                role: 'assistant', content: '', thinking: '', steps: [],
                thinkingLive: true, thinkingT0: Date.now()
              }]
            }
            const m = msgs()[assistantIdx]
            if (!m.steps) m.steps = []
            const stIdx = m.steps.findIndex(x => x.id === parsed.step.id)
            if (stIdx >= 0) m.steps[stIdx] = { ...m.steps[stIdx], ...parsed.step }
            else m.steps.push({ ...parsed.step })
            if (sid === sessionId.value) throttledScroll()
          } else if (parsed.options) {
            // 选项选择指令：把 question 追加到正文后面（先答一段再提问是常态，不能覆盖）
            const q = (parsed.options && parsed.options.question) || ''
            if (assistantIdx < 0) {
              assistantIdx = msgs().length
              messagesBySession.value[sid] = [...msgs(), { role: 'assistant', content: q, options: parsed.options }]
            } else {
              const cur = msgs()[assistantIdx].content || ''
              msgs()[assistantIdx].content = cur ? `${cur}\n\n${q}` : q
              msgs()[assistantIdx].options = parsed.options
            }
            if (sid === sessionId.value) throttledScroll()
          } else if (parsed.content) {
            if (assistantIdx < 0) {
              assistantIdx = msgs().length
              messagesBySession.value[sid] = [...msgs(), { role: 'assistant', content: '' }]
            }
            const m = msgs()[assistantIdx]
            m.content += parsed.content
            // 正文开始 → 思考块定稿：停止流式、记住耗时、自动折叠成一行（点击可再展开）
            if (m.thinkingLive) {
              m.thinkingLive = false
              m.thinkingMs = Date.now() - (m.thinkingT0 || Date.now())
              if (m.thinking || (m.steps && m.steps.length)) m.processOpen = false
            }
            if (m.steps) m.steps.forEach(x => { if (x.status === 'running') x.status = 'done' })
            if (sid === sessionId.value) throttledScroll()
          }
        } catch {}
      }
    }
  }
  } finally {
    clearInterval(watchdog)
  }
}

const submitResume = async (resumeValue, displayText, msg) => {
  const sid = sessionId.value
  // 将用户的选择作为一条用户消息展示
  messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'user', content: displayText }]
  await scrollToBottom()

  const ac = new AbortController()
  abortBySession[sid] = ac
  const signal = ac.signal
  let failed = false

  try {
    const res = await fetch(`${apiBase}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, message: displayText, resume_value: resumeValue }),
      signal
    })
    if (!res.ok) {
      failed = true
      const data = await res.json().catch(() => ({}))
      messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'assistant', content: data.error || '请求失败，请重试。' }]
      return
    }

    let scrollPending = false
    const throttledScroll = () => {
      if (!scrollPending) {
        scrollPending = true
        requestAnimationFrame(async () => {
          await scrollToBottom()
          scrollPending = false
        })
      }
    }
    await handleSSEStream(res, signal, throttledScroll, sid)
  } catch (err) {
    if (err.name === 'AbortError') return
    failed = true
    messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'assistant', content: '抱歉，发生了错误，请重试。' }]
  } finally {
    if (abortBySession[sid] === ac) delete abortBySession[sid]
    loadingBySession.value[sid] = false
    // 请求失败时把"已作答"标记回滚，否则选项按钮永久变灰、用户只能手打
    if (failed && msg) msg.optionsChosen = null
    if (sid === sessionId.value) {
      await scrollToBottom()
      setTimeout(() => { if (sid === sessionId.value) scrollToBottom() }, 200)
    }
  }
}

// 打断当前这一轮：中断流 + 把"思考中"的卡片收尾（已输出内容保留）
const interruptTurn = async (reason) => {
  const sid = sessionId.value
  const ac = abortBySession[sid]
  if (ac) {
    try { ac.abort() } catch (e) { /* 忽略 */ }
  }
  const list = messagesBySession.value[sid] || []
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i]
    if (m.role !== 'assistant') continue
    const running = (m.steps || []).some((x) => x.status === 'running')
    if (m.thinkingLive || running) {
      m.thinkingLive = false
      m.processOpen = false
      m.thinkingMs = Date.now() - (m.thinkingT0 || Date.now())
      ;(m.steps || []).forEach((x) => { if (x.status === 'running') x.status = 'done' })
      m.interrupted = true
      break
    }
  }
  loadingBySession.value[sid] = false
  if (reason === '用户插话') showToast('已打断上一轮，正在按你的新要求继续')
}

const stopGenerating = () => {
  interruptTurn('用户停止')
}

const chooseOption = (msg, opt) => {
  if (loading.value || msg.optionsChosen) return
  // 切换勾选状态，不立即提交
  if (!msg.optionsSelected) msg.optionsSelected = []
  const idx = msg.optionsSelected.indexOf(opt)
  if (idx >= 0) {
    msg.optionsSelected.splice(idx, 1)
  } else {
    msg.optionsSelected.push(opt)
  }
}

const confirmSelection = (msg) => {
  if (loading.value || msg.optionsChosen) return
  const selected = msg.optionsSelected || []
  if (!selected.length) return
  msg.optionsChosen = selected
  loadingBySession.value[sessionId.value] = true
  submitResume(selected, selected.join('、'), msg)
}

const sendMessage = async () => {
  // 生成中直接发送 = 打断当前这一轮并改道（不用先点停止）
  if (loading.value) await interruptTurn('用户插话')
  if (!inputText.value.trim() && !selectedFile.value) return
  const userText = inputText.value.trim()
  const sid = sessionId.value
  loadingBySession.value[sid] = true

  const ac = new AbortController()
  abortBySession[sid] = ac
  const signal = ac.signal

  if (selectedFile.value) {
    messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'user', content: userText || `上传了文件：${selectedFile.value.name}`, fileName: selectedFile.value.name }]
  } else {
    messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'user', content: userText }]
  }
  inputText.value = ''
  if (taRef.value) taRef.value.style.height = 'auto'
  // 发送瞬间：本地把新会话插到侧边栏顶部（不等后端/AI）
  optimisticUpsertSession(sid, userText || (selectedFile.value ? selectedFile.value.name : '新对话'))
  persistSessionId(sid)
  await scrollToBottom()

  try {
    if (selectedFile.value) {
      const fd = new FormData()
      selectedFiles.value.forEach(f => fd.append('files', f))
      fd.append('session_id', sid)
      if (userText) fd.append('message', userText)
      const res = await fetch(`${apiBase}/api/chat/upload`, { method: 'POST', body: fd, signal })
      clearFile()

      // 检测响应类型：图片走 SSE 流式，Excel 走 JSON
      const contentType = res.headers.get('Content-Type') || ''
      if (contentType.includes('text/event-stream')) {
        // SSE 流式（图片上传）；保留页数同上
        loadSessions(false, true)
        let scrollPending = false
        const throttledScroll = () => {
          if (!scrollPending) {
            scrollPending = true
            requestAnimationFrame(async () => {
              await scrollToBottom()
              scrollPending = false
            })
          }
        }
        await handleSSEStream(res, signal, throttledScroll, sid)
        return
      }

      // JSON 响应（Excel 上传）
      const data = await res.json()
      if (!res.ok) {
        messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'assistant', content: data.error || '请求失败，请重试。' }]
        return
      }
      if (sid === sessionId.value) {
        sessionId.value = data.session_id
      }
      loadSessions(false, true)
      messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'assistant', content: data.content }]
    } else {
      const res = await fetch(`${apiBase}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, message: userText }),
        signal
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'assistant', content: data.error || '请求失败，请重试。' }]
        return
      }

      // 会话已创建、用户消息已入库，刷新侧边栏拿真实 title/时间（覆盖乐观数据）
      loadSessions(false, true)

      let scrollPending = false
      const throttledScroll = () => {
        if (!scrollPending) {
          scrollPending = true
          requestAnimationFrame(async () => {
            await scrollToBottom()
            scrollPending = false
          })
        }
      }
      await handleSSEStream(res, signal, throttledScroll, sid)
    }
  } catch (err) {
    if (err.name === 'AbortError') return
    messagesBySession.value[sid] = [...(messagesBySession.value[sid] || []), { role: 'assistant', content: '抱歉，发生了错误，请重试。' }]
  } finally {
    if (abortBySession[sid] === ac) delete abortBySession[sid]
    loadingBySession.value[sid] = false
    // 只在看的是这个会话时才滚到底：后台会话完成时不能把用户正在读的另一个会话拽走
    if (sid === sessionId.value) {
      await scrollToBottom()
      setTimeout(() => { if (sid === sessionId.value) scrollToBottom() }, 200)
    }
  }
}
</script>

<style>

.drop-overlay { position: fixed; inset: 0; z-index: 9999; background: rgba(30,41,59,.35);
  display: flex; align-items: center; justify-content: center; pointer-events: none; }
.drop-card { background: #fff; border: 2px dashed #4f46e5; border-radius: 16px; padding: 28px 40px;
  text-align: center; box-shadow: 0 12px 40px rgba(15,23,42,.25); }
.drop-icon { font-size: 30px; line-height: 1; margin-bottom: 8px; }
.drop-title { font-size: 15px; font-weight: 600; color: #1e293b; }
.drop-sub { font-size: 12px; color: #64748b; margin-top: 4px; }

/* ==== 全局重置 ==== */
html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; }
#cw-app { height: 100%; }

.app, .app * { margin: 0; padding: 0; box-sizing: border-box; }
.app {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
  color: #1f2937;
  height: 100vh;
  display: flex;
  background: #f8fafc;
}

/* ==== 左侧边栏 ==== */
.sidebar {
  width: 260px;
  background: #f1f5f9;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  border-right: 1px solid #e2e8f0;
}
/* ===== 左下角用户信息（头像 + 姓名/角色 + 圆形操作按钮） ===== */
.user-bar {
  position: relative;
  margin-top: auto;
  border-top: 1px solid rgba(148, 163, 184, .22);
  padding: 8px;
}
.user-card { display: flex; align-items: center; gap: 4px; }
/* 左：头像 + 名字 + ›，点开账号菜单 */
.user-id {
  flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px;
  padding: 6px 8px; border: none; background: none; border-radius: 8px;
  cursor: pointer; text-align: left; transition: background .15s ease;
}
.user-id:hover { background: #eef2f9; }
.user-id:hover .user-name { color: #4F46E5; }
.user-caret { flex: 0 0 auto; color: #cbd5e1; transition: color .15s ease; }
.user-id:hover .user-caret { color: #94a3b8; }
/* 右：齿轮 → 个人设置 */
.user-gear {
  flex: 0 0 auto; width: 30px; height: 30px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  border: none; background: none; cursor: pointer; color: #94a3b8;
  transition: color .15s ease, background .15s ease;
}
.user-gear:hover { color: #4F46E5; background: #eef2f9; }
/* 账号菜单：贴在用户行上方 */
.user-pop {
  position: absolute; left: 8px; right: 8px; bottom: calc(100% - 4px);
  padding: 6px; z-index: 40; background: #fff;
  border: 1px solid #e5e9f2; border-radius: 10px;
  box-shadow: 0 12px 30px rgba(15, 23, 42, .16);
}
.user-pop-item {
  display: flex; align-items: center; gap: 9px; width: 100%;
  padding: 9px 11px; border: none; background: none; border-radius: 8px;
  font-size: 13px; color: #334155; cursor: pointer; text-align: left;
  transition: background .15s ease, color .15s ease;
}
.user-pop-item:hover { background: #fef2f2; color: #dc2626; }
.user-pop-item svg { flex: 0 0 auto; }

/* ===== 个人设置页（偏好 / 业务知识 / 修改密码）===== */
.me-wrap { max-width: 720px; padding: 18px 22px 26px; }
.me-form { display: flex; flex-direction: column; max-width: 360px; }
.me-label { font-size: 13px; color: #334155; margin: 14px 0 6px; }
.me-label:first-child { margin-top: 4px; }
.me-hint { font-size: 12px; color: #94a3b8; margin-left: 6px; }
.me-save { margin-top: 20px; align-self: flex-start; padding: 9px 22px; }
.user-avatar {
  width: 34px; height: 34px; flex: 0 0 auto; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; font-weight: 700; color: #fff;
  background: #eef2ff; border: 1px solid #e2e8f5; overflow: hidden;
  user-select: none;
}
.user-avatar-img { width: 100%; height: 100%; object-fit: cover; display: block; }
.user-meta { flex: 1; min-width: 0; }
.user-name {
  font-size: 14.5px; font-weight: 600; color: #1e293b; line-height: 1.25;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.user-sub {
  display: flex; align-items: center; gap: 4px;
  font-size: 12px; color: #94a3b8; margin-top: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.user-dot { color: #cbd5e1; }
/* 用户卡片右侧的图标按钮：浅色幽灵风格，和侧栏其余图标一致 */
.user-action {
  width: 28px; height: 28px; flex: 0 0 auto; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  border: none; cursor: pointer; color: #94a3b8; background: transparent;
  transition: color .15s ease, background .15s ease;
}
.user-action:hover { color: #4F46E5; background: #EEF2FF; }
.user-action:active { background: #E0E7FF; }
.user-action svg { width: 15px; height: 15px; }

/* ===== 侧边栏品牌区（Open Agent Harness） ===== */
.brand {
  display: flex; align-items: center; gap: 10px;
  padding: 4px 6px 16px; margin-bottom: 12px;
  border-bottom: 1px solid rgba(148, 163, 184, .22);
}
.brand-mark {
  width: 40px; height: 40px; flex: 0 0 auto; display: block;
  transition: transform .18s ease;
}
.brand:hover .brand-mark { transform: scale(1.04); }
.brand-text { min-width: 0; flex: 1; }
.brand-name {
  display: block;
  font-size: 16px; font-weight: 800; letter-spacing: .01em;
  color: #3F3F46; line-height: 1.2; white-space: nowrap;
}
/* 参考 deepseek + [HARNESS] 徽标式：字标 + 描边胶囊后缀 */
.brand-suffix {
  font-size: 9.5px; font-weight: 700; letter-spacing: .14em;
  color: #4338CA; background: #EEF2FF;
  border: 1px solid #C7D2FE; border-radius: 5px;
  padding: 2px 7px; margin-left: 9px; line-height: 1.4;
  text-transform: uppercase;
}
.brand-sub {
  font-size: 10px; letter-spacing: .1em; color: #94a3b8; margin-top: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.sidebar-header {
  padding: 16px;
}
.sidebar-new-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 16px;
  border: none;
  border-radius: 12px;
  /* 主操作：靛蓝渐变（之前的灰底太寡淡） */
  background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: .2px;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.28);
  transition: transform .16s ease, box-shadow .16s ease, filter .16s ease;
  font-family: inherit;
}
.sidebar-new-btn:hover {
  filter: brightness(1.06);
  box-shadow: 0 6px 16px rgba(79, 70, 229, 0.36);
  transform: translateY(-1px);
}
.sidebar-new-btn:active { transform: translateY(0); filter: brightness(.98); }
.sidebar-body {
  flex: 1;
  overflow-y: auto;
  padding: 0 12px 10px;
}
.sidebar-body::-webkit-scrollbar { width: 4px; }
.sidebar-body::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 2px; }
.conv-empty { font-size: 12px; color: #94a3b8; padding: 16px 8px; text-align: center; }
/* 会话折叠区标题（在「我的技能」下面） */
.conv-section {
  display: flex; align-items: center; gap: 6px;
  margin: 10px 16px 4px; padding: 6px 8px;
  font-size: 12px; font-weight: 600; color: #475569;
  border-radius: 8px; cursor: pointer; user-select: none;
  transition: background .16s ease, color .16s ease;
}
.conv-section:hover { background: #e8ecf5; color: #1e293b; }
.conv-chevron { flex-shrink: 0; transition: transform .18s ease; color: #6366f1; }
.conv-chevron.collapsed { transform: rotate(-90deg); }
.conv-count {
  margin-left: auto; font-style: normal; font-size: 11px; font-weight: 600;
  color: #6d28d9; background: #ede9fe; border: 1px solid #ddd6fe;
  border-radius: 999px; padding: 1px 7px;
}
.conv-loading { font-size: 12px; color: #94a3b8; padding: 12px 8px; text-align: center; }
.conv-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s;
  gap: 6px;
  position: relative;
}
.conv-item:hover { background: #e8edf7; }
/* 选中：淡靛蓝底 + 左侧渐变强调条 */
.conv-item.active {
  background: linear-gradient(90deg, #e0e7ff 0%, #eef2ff 100%);
  box-shadow: inset 0 0 0 1px #c7d2fe;
}
.conv-item.active::before {
  content: '';
  position: absolute; left: 3px; top: 50%; transform: translateY(-50%);
  width: 3px; height: 20px; border-radius: 2px;
  background: linear-gradient(180deg, #6366f1, #8b5cf6);
}
.conv-item-title {
  flex: 1;
  font-size: 13px;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-item.active .conv-item-title { color: #3730a3; font-weight: 600; }
.conv-item-time { font-size: 11px; color: #94a3b8; white-space: nowrap; }
.conv-item-generating { color: #3b82f6; }
.conv-item-del {
  opacity: 0;
  background: none;
  border: none;
  cursor: pointer;
  color: #94a3b8;
  padding: 4px;
  display: flex;
  transition: opacity 0.2s;
}
.conv-item:hover .conv-item-del { opacity: 1; }
.conv-item-arch {
  border: none; background: transparent; color: #94a3b8; cursor: pointer;
  padding: 2px 4px; border-radius: 4px; opacity: 0; transition: opacity .15s, color .15s;
  display: flex; align-items: center;
}
.conv-item-arch:hover { color: #6366F1; background: #EEF2FF; }
.conv-item:hover .conv-item-arch { opacity: 1; }
.prefs-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, .38); z-index: 60;
  display: flex; align-items: center; justify-content: center;
}
.prefs-panel {
  width: 520px; max-width: calc(100vw - 40px); max-height: 76vh; overflow: auto;
  background: #fff; border-radius: 14px; box-shadow: 0 18px 48px rgba(15,23,42,.22);
  padding: 18px 20px 16px;
}
.prefs-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.prefs-head h3 { margin: 0; font-size: 15px; font-weight: 700; color: #1f2937; }
.prefs-close { border: none; background: transparent; color: #94a3b8; cursor: pointer; font-size: 14px; }
.prefs-tip { font-size: 12px; color: #94a3b8; margin-bottom: 12px; line-height: 1.6; }
.know-admin-tip { font-size: 12px; color: #64748b; background: #f8fafc; border: 1px solid #eef1f5; border-radius: 8px; padding: 9px 12px; margin-bottom: 12px; }
.know-admin-add { display: flex; gap: 8px; margin-bottom: 12px; }
.know-admin-add .prefs-input { flex: 1; }
.know-add { display: flex; gap: 10px; align-items: center; padding: 0 18px 14px; }
/* 上传行居中：选择文件 / 范围 / 上传 / 刷新 一排摆中间，提示放下一行 */
.know-add-center { justify-content: center; }
.kb-hint-line { text-align: center; padding: 0 18px 12px; }
/* 生成中：发送按钮改成"会打断"的描边样式，和红色停止按钮区分 */
.send.interrupt { background: #EEF2FF !important; color: #4338CA !important; box-shadow: inset 0 0 0 1px #C7D2FE; }
.send.interrupt:hover { background: #E0E7FF !important; }
.msg-interrupted { margin-top: 6px; font-size: 12px; color: #94a3b8; }
/* 刷新按钮：浅靛底 + 边框 + 圆环箭头，和「上传并索引」区分开（次要动作） */
.kb-refresh-btn {
  display: inline-flex; align-items: center; gap: 7px;
  height: 34px; padding: 0 16px; border-radius: 10px;
  background: #EEF2FF; border: 1px solid #C7D2FE; color: #4338CA;
  font-size: 13px; font-weight: 500; cursor: pointer; transition: all .14s ease;
}
.kb-refresh-btn:hover:not(:disabled) { background: #E0E7FF; border-color: #A5B4FC; }
.kb-refresh-btn:active:not(:disabled) { transform: translateY(1px); }
.kb-refresh-btn:disabled { opacity: .55; cursor: default; }
.kb-refresh-btn svg { width: 15px; height: 15px; }
.kb-refresh-btn.spinning svg { animation: kb-spin 1s linear infinite; }
@keyframes kb-spin { to { transform: rotate(360deg); } }
.kb-polling { color: #6366F1; margin-left: 4px; }
/* ===== 知识库子页签 ===== */
.kb-tabs { display: flex; gap: 6px; padding: 0 18px 12px; }
.kb-tab {
  padding: 6px 14px; font-size: 13px; color: #64748b; background: #f1f5f9;
  border: none; border-radius: 8px; cursor: pointer; transition: all .15s ease;
}
.kb-tab:hover { color: #4F46E5; background: #eef2ff; }
.kb-tab.on { color: #fff; background: #4F46E5; }
.kb-file { display: none; }               /* 原生控件藏起来，用下面的按钮代替 */
.kb-file-btn {
  flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 14px; border: 1px solid #d8dde6; border-radius: 8px;
  background: #fff; color: #334155; font-size: 13px; cursor: pointer;
  transition: border-color .15s, color .15s, background .15s;
}
.kb-file-btn:hover { border-color: #8b7cf6; color: #4f46e5; background: #f8f7ff; }
/* 提高优先级：.rbac-input 的 flex:1 定义在后面，会把这个下拉拉满整行 */
.know-add select.kb-scope { flex: 0 0 auto; width: auto; min-width: 84px; }
.kb-hint { font-size: 12px; color: #94a3b8; }

/* 检索结果 */
.kb-hits { padding: 0 18px 14px; display: flex; flex-direction: column; gap: 8px; }
.kb-hit { border: 1px solid #eef1f5; border-radius: 10px; padding: 10px 12px; background: #fff; }
.kb-hit-head { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #64748b; margin-bottom: 6px; }
.kb-hit-idx { color: #4F46E5; font-weight: 600; }
.kb-hit-doc { color: #1e293b; font-weight: 600; }
.kb-hit-meta { color: #94a3b8; }
.kb-hit-score { margin-left: auto; color: #94a3b8; font-variant-numeric: tabular-nums; }
.kb-hit-body { font-size: 12.5px; color: #334155; line-height: 1.6; white-space: pre-wrap; }

/* 文档列表 */
.kb-thead, .kb-row { display: grid; grid-template-columns: 56px minmax(220px, 1fr) 72px 80px 64px 64px 90px 110px; align-items: stretch; }
.kb-thead { position: sticky; top: 0; z-index: 2; background: #f7f8fa; border-bottom: 1px solid #e6e9ef; font-size: 13px; font-weight: 600; color: #333; }
.kb-thead > span, .kb-row > div { display: flex; align-items: center; justify-content: center; padding: 9px 8px; }
.kb-row { border-bottom: 1px solid #f0f2f5; background: #fff; font-size: 12.5px; }
.kb-row:hover { background: #fafbfc; }
.kb-row:last-child { border-bottom: none; }
.kb-fname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; justify-content: flex-start !important; }
.kb-ops { gap: 10px; }
.kb-status { font-size: 11px; padding: 2px 7px; border-radius: 5px; }
.kb-status.indexed { background: #e8f7ee; color: #16794c; }
.kb-status.failed { background: #fdeaea; color: #b3261e; }
.kb-status.pending, .kb-status.parsing, .kb-status.ocr, .kb-status.embedding { background: #eef2ff; color: #4338ca; }
.know-input-main { flex: 1; }
.know-input-tags { width: 260px; }
.pane-sub { font-size: 12.5px; color: #94a3b8; margin-left: 10px; font-weight: 400; }
.know-edit-cell { padding: 4px 8px !important; }
.know-edit-cell .rbac-input { height: 30px; font-size: 12.5px; }
.know-ops { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.op-muted { color: #94a3b8; }
.know-thead, .know-row {
  display: grid; grid-template-columns: 56px 72px minmax(260px, 1fr) 200px 110px 140px 112px;
  align-items: stretch;
}
.know-thead {
  position: sticky; top: 0; z-index: 2; background: #f7f8fa; border-bottom: 1px solid #e6e9ef;
  font-size: 13px; font-weight: 600; color: #333;
}
.know-thead > span, .know-row > div {
  padding: 12px 10px; display: flex; align-items: center; justify-content: center;
  border-right: 1px solid #f0f2f5; min-width: 0;
}
.know-thead > span:last-child, .know-row > div:last-child { border-right: none; }
.know-row { border-bottom: 1px solid #f0f2f5; background: #fff; font-size: 12.5px; }
.know-row:hover { background: #fafbfc; }
.know-row:last-child { border-bottom: none; }
.cell-know { text-align: left !important; justify-content: flex-start !important; color: #334155; }
.know-tags-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.know-list { max-height: 46vh; overflow-y: auto; }
.op-link { color: #1a73e8; cursor: pointer; font-size: 12.5px; }
.op-link:hover { text-decoration: underline; }
.prefs-tabs { display: flex; gap: 4px; }
.prefs-tab { border: 0; background: transparent; padding: 5px 10px; font-size: 13px; color: #64748b; border-radius: 6px; cursor: pointer; }
.prefs-tab.on { background: #eef2ff; color: #4338ca; font-weight: 600; }
.know-scope { display: inline-block; font-size: 10px; padding: 1px 5px; border-radius: 4px; margin-right: 5px; vertical-align: middle; }
.know-scope.user { background: #eef2ff; color: #4338ca; }
.know-scope.global { background: #fff7ed; color: #c2410c; }
.know-tags { display: block; font-size: 11px; color: #94a3b8; margin-top: 2px; }
.prefs-list { border: 1px solid #eef1f5; border-radius: 10px; overflow: hidden; margin-bottom: 12px; }
.prefs-empty { padding: 18px; text-align: center; font-size: 12.5px; color: #94a3b8; }
.prefs-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-bottom: 1px solid #f4f6f9; font-size: 13px; color: #334155;
}
.prefs-item:last-child { border-bottom: none; }
.prefs-text { flex: 1; min-width: 0; word-break: break-all; }
.prefs-del {
  border: none; background: transparent; color: #d93025; cursor: pointer; font-size: 12px;
  padding: 2px 6px; border-radius: 5px;
}
.prefs-del:hover { background: #fef2f2; }
.prefs-add { display: flex; gap: 8px; }
.prefs-input {
  flex: 1; height: 34px; padding: 0 10px; font-size: 13px;
  border: 1px solid #e2e8f0; border-radius: 8px; outline: none;
}
.prefs-input:focus { border-color: #a5b4fc; box-shadow: 0 0 0 3px rgba(99,102,241,.12); }
.prefs-count { margin-top: 8px; font-size: 11.5px; color: #94a3b8; text-align: right; }
.conv-archive-toggle {
  margin: 6px 10px 6px; padding: 7px 10px; font-size: 12.5px; color: #64748b;
  border-top: 1px solid #eef1f5; cursor: pointer; user-select: none;
}
.conv-archive-toggle:hover { color: #6366F1; }
.conv-item-del:hover { color: #ef4444; }



/* ==== 右侧主区域 ==== */
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  position: relative;   /* 系统设置页用绝对定位铺满主区域 */
}

/* ==== 顶部标题栏 ==== */
.top-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
  user-select: none;
}
.top-bar-btn {
  width: 34px; height: 34px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #475569;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}
.top-bar-btn:hover { background: #e2e8f0; }
.top-bar-title { font-size: 16px; font-weight: 600; color: #1e293b; }

/* ==== 主内容 ==== */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  max-width: 800px;
  width: 100%;
  margin: 0 auto;
  padding: 0 20px;
}

/* ==== 欢迎页 ==== */
.welcome { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px 20px; text-align: center; }
.welcome-bot { margin-bottom: 18px; animation: bounce 2s ease-in-out infinite; }
.welcome-title { font-size: 18px; font-weight: 600; color: #1e293b; letter-spacing: 0.3px; }

@keyframes bounce { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }

/* ==== 消息 ==== */
.msgs { flex: 1; overflow-y: auto; padding: 16px 0; display: flex; flex-direction: column; gap: 16px; }
.msgs::-webkit-scrollbar { width: 5px; }
.msgs::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
.msgs::-webkit-scrollbar-track { background: transparent; }
.msg { display: flex; gap: 10px; }
.msg.user { align-self: flex-end; flex-direction: row-reverse; }
.msg.assistant { align-self: flex-start; }
.msg-avatar {
  width: 32px; height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
}
.msg.assistant .msg-avatar { background: linear-gradient(135deg, #3b82f6, #2563eb); color: #fff; }
.msg.user .msg-avatar { background: linear-gradient(135deg, #f59e0b, #ea580c); color: #fff; }
.msg-name { font-size: 11px; color: #94a3b8; margin-bottom: 4px; text-align: right; font-weight: 500; }
.msg-main { min-width: 0; max-width: 82%; }
.msg-file {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #f1f5f9;
  border-radius: 8px;
  font-size: 12px;
  color: #475569;
  margin-bottom: 6px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.msg-text {
  font-size: 14px;
  line-height: 1.6;
  color: #1e293b;
  word-break: break-word;
}
.msg.user .msg-text {
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  color: #fff;
  padding: 10px 14px;
  border-radius: 16px 4px 16px 16px;
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.25);
}
.msg.assistant .msg-text {
  background: #ffffff;
  padding: 10px 14px;
  border-radius: 4px 16px 16px 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  border: 1px solid #e2e8f0;
}

/* ==== 过程展示（思考 + 工具步骤）====
   仅前端内存展示，不入库：刷新/切设备即消失；样式对齐 Hermes 的「可折叠思考块」 */
.msg-process {
  margin: 0 0 6px;
  border: 1px solid #e2e8f0;
  border-left: 3px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  overflow: hidden;
}
.proc-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  color: #64748b;
}
.proc-head:hover { background: #f1f5f9; }
.proc-title { flex: 1; }
.proc-caret {
  display: inline-block;
  transition: transform 0.15s;
  transform: rotate(-90deg);
}
.proc-caret.open { transform: rotate(0deg); }
.proc-body { padding: 0 10px 8px; }
.proc-steps { display: flex; flex-direction: column; gap: 3px; margin-bottom: 6px; }
.proc-step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #475569;
}
.proc-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
  flex-shrink: 0;
}
.proc-step.running .proc-dot { background: #3b82f6; animation: procPulse 1.2s ease-in-out infinite; }
.proc-step.done .proc-dot { background: #10b981; }
@keyframes procPulse {
  0%, 100% { opacity: 0.35; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.15); }
}
.proc-step-label { font-weight: 500; }
.proc-step-detail {
  color: #94a3b8;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  max-width: 46ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.proc-step-time { color: #94a3b8; font-size: 11px; margin-left: auto; }
.proc-think {
  font-size: 12px;
  line-height: 1.65;
  color: #64748b;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
  border-left: 2px solid #e2e8f0;
  padding-left: 8px;
}

.dots { display: flex; gap: 4px; padding: 8px 0; }
.dots span { width: 6px; height: 6px; border-radius: 50%; background: #94a3b8; animation: dot 1.3s ease-in-out infinite; }
.dots span:nth-child(2) { animation-delay: 0.2s; }
.dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot { 0%, 60%, 100% { opacity: 0.3; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-5px); } }

/* ==== 底部输入 ==== */
.bottom { padding: 16px 0 20px; flex-shrink: 0; }
.input-box {
  background: #ffffff;
  border-radius: 16px;
  padding: 10px 14px;
  transition: all 0.2s;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  border: 1px solid #e2e8f0;
}
.input-box:focus-within {
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1), 0 0 0 3px rgba(59, 130, 246, 0.1);
  border-color: #3b82f6;
}

.file-tag-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 4px 0 8px; font-size: 12px; color: #475569; }
.file-tag-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 0 1 auto; max-width: 220px; }
.file-total { margin-left: auto; color: #64748b; white-space: nowrap; }
.file-total.over { color: #dc2626; font-weight: 600; }
.file-tag-close { background: none; border: none; cursor: pointer; color: #94a3b8; font-size: 18px; line-height: 1; padding: 0 4px; transition: color 0.2s; }
.file-tag-close:hover { color: #ef4444; }

.input-row { display: flex; align-items: flex-end; gap: 8px; }
.input-row textarea {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  line-height: 1.5;
  resize: none;
  padding: 2px 0;
  font-family: inherit;
  color: #1e293b;
}
.input-row textarea::placeholder { color: #94a3b8; }
.send {
  width: 36px; height: 36px;
  border: none;
  border-radius: 50%;
  background: #e2e8f0;
  color: #94a3b8;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  flex-shrink: 0;
}
.send.on {
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  color: #fff;
  box-shadow: 0 2px 10px rgba(59, 130, 246, 0.35);
}
.send.on:hover { transform: scale(1.05); box-shadow: 0 4px 14px rgba(59, 130, 246, 0.45); }
.send:disabled { cursor: not-allowed; }

.input-tools { display: flex; align-items: center; gap: 6px; padding-top: 8px; border-top: 1px solid transparent; }
.input-tools .send { margin-left: auto; }
.input-box:focus-within .input-tools { border-top-color: #e2e8f0; }
.tool-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 20px;
  font-size: 12px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
  background: #f8fafc;
}
.tool-btn:hover { border-color: #3b82f6; color: #3b82f6; background: #eff6ff; }
.tool-btn input[type=file] { display: none; }

/* ==== 侧边栏过渡动画 ==== */
.sidebar-slide-enter-active { transition: transform 0.2s ease, opacity 0.2s ease; }
.sidebar-slide-leave-active { transition: transform 0.15s ease, opacity 0.15s ease; }
.sidebar-slide-enter-from, .sidebar-slide-leave-to { transform: translateX(-100%); opacity: 0; }

/* ==== 选项（勾选样式） ==== */
.msg-options { margin-top: 8px; }
.msg-options-btns { display: flex; flex-direction: column; align-items: stretch; gap: 4px; }
.option-btn {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 5px 8px;
  border: none;
  background: transparent;
  color: #334155;
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
  text-align: left;
  transition: color 0.15s;
  font-family: inherit;
  border-radius: 6px;
}
.option-btn:hover:not(:disabled) { background: #f5f7fa; }
.option-checkbox {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  cursor: pointer;
  accent-color: #3b82f6;
}
.option-label {
  word-break: break-word;
  overflow-wrap: anywhere;
  flex: 1;
  min-width: 0;
}
.option-btn.chosen .option-label { color: #3b82f6; }
.option-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.msg-options-actions { margin-top: 4px; display: flex; align-items: center; gap: 10px; }
.option-done-tip { font-size: 12px; color: #64748b; }
.option-confirm-btn {
  padding: 8px 24px;
  border: none;
  border-radius: 8px;
  background: #3b82f6;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.option-confirm-btn:hover:not(:disabled) { background: #2563eb; }
.option-confirm-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* ==== Markdown 渲染样式 ==== */
.markdown-body table {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
  max-width: 100%;
  font-size: 12px;
}
.markdown-body th, .markdown-body td {
  border: 1px solid #e2e8f0;
  padding: 6px 8px;
  text-align: left;
  color: #334155;
  /* 表格里禁止逐字符断词：继承自 .msg-text 的 break-word 会把 Python 断成
     "Pyth / on"，英文单词必须整体换行 */
  word-break: normal;
  overflow-wrap: break-word;
}
.markdown-body th { background: #f1f5f9; color: #475569; font-weight: 600; white-space: nowrap; }
.markdown-body tr:nth-child(even) td { background: #f8fafc; }
.markdown-body pre {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 12px;
}
.markdown-body code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #f1f5f9;
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 0.9em;
}
.markdown-body pre code { background: transparent; padding: 0; }
.markdown-body ul, .markdown-body ol { padding-left: 20px; margin: 6px 0; }
.markdown-body blockquote {
  border-left: 3px solid #cbd5e1;
  padding-left: 10px;
  margin: 6px 0;
  color: #64748b;
}
.markdown-body p { margin: 4px 0; }

/* ==== 我的技能面板 ==== */
/* 次操作：淡紫底 + 描边（和主操作区分，但同样有色） */
/* 次级入口（技能与工具 / 知识库）用同一套浅底样式；
   只有「新对话」保留实心靛蓝渐变，避免侧栏里出现两块同色的实心按钮 */
.sidebar-skill-btn,
.sidebar-kb-btn {
  margin-top: 8px;
  background: linear-gradient(135deg, #eef2ff 0%, #e0e7ff 100%);
  color: #4338ca;
  border: 1px solid #c7d2fe;
  box-shadow: none;
}
.sidebar-skill-btn:hover,
.sidebar-kb-btn:hover { background: linear-gradient(135deg, #e0e7ff 0%, #c7d2fe 100%); }
.sidebar-skill-btn:hover {
  background: linear-gradient(135deg, #e0e7ff 0%, #c7d2fe 100%);
  color: #3730a3;
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.18);
  transform: translateY(-1px);
  filter: none;
}
.skill-count {
  display: none;   /* 角标已去掉，保留占位避免历史样式冲突 */
}
.skill-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.38);
  display: flex; align-items: center; justify-content: center; z-index: 60; padding: 24px;
}
/* 页签 */
.skill-tabs { display: flex; gap: 4px; padding: 10px 20px 0; }
.skill-tab {
  padding: 7px 14px; border: none; border-radius: 9px 9px 0 0; background: transparent;
  color: #64748b; font-size: 13px; font-weight: 500; cursor: pointer; font-family: inherit;
  border-bottom: 2px solid transparent;
}
.skill-tab:hover { color: #4338ca; background: #f5f3ff; }
.skill-tab.active { color: #4338ca; font-weight: 600; border-bottom-color: #6366f1; background: #eef2ff; }
/* 技能页内的二级页签：我的技能 / 平台技能（页签左，操作右，同一行） */
.skill-subhead {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 12px 20px 0; flex-wrap: wrap;
}
.skill-subtabs { display: flex; gap: 8px; }
.skill-subactions { display: flex; align-items: center; gap: 8px; }
.skill-subtab {
  padding: 5px 14px; border: 1px solid #e2e8f0; border-radius: 999px; background: #fff;
  color: #475569; font-size: 12.5px; font-weight: 500; cursor: pointer; font-family: inherit;
  transition: all .15s ease;
}
.skill-subtab:hover { border-color: #c7d2fe; color: #4338ca; background: #f5f3ff; }
.skill-subtab.active { background: #4338ca; border-color: #4338ca; color: #fff; font-weight: 600; }
.skill-hint { color: #94a3b8; font-size: 12px; }
.skill-badge.on { background: #dcfce7; color: #166534; }
.skill-badge.risk-read { background: #e0f2fe; color: #0369a1; }
.skill-badge.risk-write { background: #ffedd5; color: #9a3412; }
.skill-badge.risk-exec { background: #ede9fe; color: #5b21b6; }
.skill-panel {
  width: min(640px, 100%); max-height: 78vh; display: flex; flex-direction: column;
  background: #fff; border-radius: 16px; box-shadow: 0 24px 60px rgba(15, 23, 42, 0.25);
  overflow: hidden;
}
.skill-panel-head {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 18px 20px 12px; border-bottom: 1px solid #e2e8f0;
}
.skill-panel-head h3 { font-size: 16px; color: #0f172a; }
.skill-sub { margin-top: 4px; font-size: 12px; color: #64748b; line-height: 1.5; }
.skill-close {
  margin-left: auto; border: none; background: #f1f5f9; color: #475569;
  width: 28px; height: 28px; border-radius: 8px; cursor: pointer; flex-shrink: 0;
}
.skill-toolbar { display: flex; gap: 8px; padding: 12px 20px 6px; }
.skill-upload {
  display: inline-flex; align-items: center; cursor: pointer;
  padding: 8px 14px; border-radius: 10px; font-weight: 600;
  background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
  color: #fff; border: none; font-size: 12.5px;
  box-shadow: 0 3px 10px rgba(79, 70, 229, 0.25);
  transition: filter .16s ease, transform .16s ease;
}
.skill-upload:hover { filter: brightness(1.06); transform: translateY(-1px); }
.skill-upload input { display: none; }
.skill-refresh {
  padding: 8px 13px; border-radius: 10px; border: 1px solid #c7d2fe;
  background: #eef2ff; color: #4338ca; font-size: 12.5px; font-weight: 500; cursor: pointer;
}
.skill-refresh:hover { background: #e0e7ff; }
.skill-refresh:disabled { opacity: .55; cursor: default; }
.skill-msg {
  margin: 6px 20px 4px; padding: 8px 10px; font-size: 12px;
  border-radius: 8px; background: #f0f9ff; color: #0369a1; word-break: break-word;
}
.skill-empty { padding: 28px 20px; text-align: center; color: #94a3b8; font-size: 13px; line-height: 1.6; }
.skill-list {
  overflow-y: auto; padding: 6px 20px 18px;
  display: flex; flex-direction: column; gap: 10px;
}
.skill-item {
  display: flex; gap: 12px; align-items: center; padding: 12px;
  border: 1px solid #e2e8f0; border-radius: 12px; background: #f8fafc;
}
.skill-item.off { opacity: .62; }
.skill-item-main { flex: 1; min-width: 0; }
.skill-item-title {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 13.5px; font-weight: 600; color: #1e293b;
}
.skill-badge {
  font-size: 10.5px; font-weight: 500; padding: 2px 6px;
  border-radius: 6px; background: #e0e7ff; color: #3730a3;
}
.skill-badge.warn { background: #fef3c7; color: #92400e; }
.skill-badge.off { background: #e2e8f0; color: #475569; }
.skill-item-desc { margin-top: 3px; font-size: 12px; color: #64748b; line-height: 1.5; word-break: break-word; }
.skill-item-meta { margin-top: 4px; font-size: 11px; color: #94a3b8; }
.skill-item-actions { display: flex; flex-direction: row; align-items: center; gap: 8px; margin-left: auto; flex-shrink: 0; }
.skill-act {
  border: 1px solid #e2e8f0; background: #fff; color: #475569;
  font-size: 12.5px; padding: 5px 13px; border-radius: 7px; cursor: pointer; white-space: nowrap;
  transition: all .14s ease; font-family: inherit; line-height: 1.5;
}
.skill-act:hover { background: #f8fafc; border-color: #cbd5e1; color: #0f172a; }
.skill-act.danger { color: #b91c1c; border-color: #fecaca; background: #fff5f5; }
.skill-act.danger:hover { background: #fee2e2; }
.skill-act:disabled { opacity: .55; cursor: not-allowed; }
.fade-enter-active, .fade-leave-active { transition: opacity .18s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* ===== 账号状态徽标（用户管理用） ===== */
.appr-status { font-size: 12px; padding: 2px 8px; border-radius: 6px; }
.st-pending { background: #FEF3C7; color: #B45309; }
.st-rejected { background: #FEE2E2; color: #B91C1C; }
.st-executed { background: #DCFCE7; color: #15803D; }
.st-failed { background: #FEE2E2; color: #B91C1C; }

/* ===== 角色管理 ===== */
.rbac-hint {
  margin: 4px 18px 12px; padding: 10px 12px; background: #F0F9FF; border: 1px solid #BAE6FD;
  border-radius: 10px; font-size: 12.5px; color: #075985; line-height: 1.7;
}
.rbac-roles { display: flex; gap: 10px; padding: 0 18px 12px; flex-wrap: wrap; }
.rbac-role-card {
  flex: 1 1 160px; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px; background: #fafbff;
}
.rbac-role-name { font-size: 13px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 6px; }
.rbac-code { font-size: 11px; font-weight: 400; color: #6366f1; background: #EEF2FF; padding: 1px 6px; border-radius: 5px; }
.rbac-role-desc { font-size: 12px; color: #64748b; margin-top: 4px; line-height: 1.5; }
.rbac-role-tools { font-size: 11.5px; color: #94a3b8; margin-top: 4px; }
.rbac-toolbar { display: flex; gap: 10px; padding: 0 18px 10px; }
.rbac-input {
  flex: 1; border: 1px solid #cbd5e1; border-radius: 8px; padding: 7px 10px; font-size: 13px; outline: none;
}
.rbac-input:focus { border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.12); }
.rbac-list { padding: 0 18px 18px; overflow-y: auto; max-height: 34vh; }
.pwd-input { width: 100%; margin-top: 10px; }
.pwd-err { margin: 10px 0 0; font-size: 12.5px; color: #c0392b; }

.rbac-user {
  border-bottom: 1px solid #f0f2f5; background: #fff; margin: 0; padding: 0;
}
.rbac-user:last-child { border-bottom: none; }
.rbac-user:hover { background: #fafbfc; }

/* 单元格内容对齐修正（左右列保持居中，说明列左对齐） */
.rbac-user > div:nth-child(5) { justify-content: flex-start; text-align: left; }
.user-thead > span:nth-child(5) { justify-content: center; }
.rbac-user-name { min-width: 110px; font-size: 13px; font-weight: 600; color: #334155; }
.rbac-user-roles { display: flex; gap: 12px; flex-wrap: wrap; flex: 1; }
.rbac-check { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: #475569; cursor: pointer; }
.rbac-check input { width: 15px; height: 15px; accent-color: #4F46E5; cursor: pointer; }
.rbac-select {
  border: 1px solid #cbd5e1; border-radius: 8px; padding: 7px 10px; font-size: 13px;
  background: #fff; color: #334155; outline: none; cursor: pointer;
}
.rbac-disp { font-style: normal; font-size: 11.5px; color: #94a3b8; margin-left: 4px; }
.rbac-user-meta { display: flex; align-items: center; gap: 10px; font-size: 11.5px; color: #94a3b8; white-space: nowrap; }
.rbac-user-actions { display: flex; gap: 6px; }
.rbac-user-off { background: #fafafa; opacity: .78; }

/* ===== 系统设置：角色 → 工具 ===== */
.sidebar-sys-btn { background: #eef2f7 !important; color: #334155 !important; border: 1px solid #dbe3ec; }
.sidebar-sys-btn:hover { background: #e3e9f2 !important; }
.rbac-role-card { cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.rbac-role-card.picked { border-color: #6366F1; box-shadow: 0 0 0 3px rgba(99,102,241,.12); background: #ffffff; }
.role-tool-head { display: flex; align-items: center; gap: 14px; margin: 2px 0 10px; font-size: 13px; color: #1e293b; }
/* ===== 分区卡片：列表 / 工具配置 之间有明确层次 ===== */
.panel-card {
  margin-top: 20px; padding: 16px 18px 18px;
  background: #fff; border: 1px solid #e6e9ef; border-radius: 10px;
  box-shadow: 0 1px 2px rgba(15,23,42,.045);
}
.panel-card .panel-head {
  margin: 0 0 14px; padding-bottom: 12px; border-bottom: 1px solid #f0f2f5;
}
/* 分页与下方卡片拉开距离，避免挤在一起 */
.pager { padding: 14px 2px 4px; }
.role-tool-group {
  font-size: 12px; font-weight: 700; color: #4338CA; background: #EEF2FF;
  display: inline-block; padding: 2px 9px; border-radius: 6px; margin: 10px 0 8px;
}
.role-tool-item { margin: 3px 0; font-size: 12.5px; line-height: 1.6; }
.role-tool-item code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 12px; color: #0f172a; }
.risk-tag { font-style: normal; font-size: 11px; padding: 0 5px; border-radius: 4px; margin: 0 2px; }
.risk-read { background: #DCFCE7; color: #15803D; }
.risk-write { background: #FEF3C7; color: #B45309; }
.risk-exec { background: #EDE9FE; color: #6D28D9; }
.muted { color: #94a3b8; font-style: normal; font-size: 12px; }
.role-tool-actions { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
/* ===== 系统设置页（整页 + 左侧菜单，不是弹窗） ===== */
.sys-page {
  position: absolute; inset: 0; z-index: 30; background: #f7f9fc;
  display: flex; flex-direction: column; overflow: hidden;
}
.sys-head {
  display: flex; align-items: center; gap: 14px; padding: 13px 22px;
  background: #fff; border-bottom: 1px solid #e2e8f0; flex: none;
}
.sys-head h2 { font-size: 16px; color: #1e293b; margin: 0; }
.sys-sub { font-size: 12px; color: #94a3b8; }
.sys-back {
  border: 1px solid #dbe3ec; background: #f8fafc; color: #334155;
  border-radius: 8px; padding: 6px 12px; font-size: 13px; cursor: pointer;
}
.sys-back:hover { background: #eef2f7; }
.sys-wrap { flex: 1; display: flex; min-height: 0; }
.sys-side {
  width: 186px; flex: none; background: #fff; border-right: 1px solid #e2e8f0;
  padding: 12px 10px; display: flex; flex-direction: column; gap: 6px;
}
.sys-nav {
  text-align: left; border: none; background: transparent; color: #475569;
  font-size: 13.5px; padding: 9px 12px; border-radius: 8px; cursor: pointer;
}
.sys-nav:hover { background: #f1f5f9; }
.sys-nav.active { background: #EEF2FF; color: #4338CA; font-weight: 600; }
.sys-body { flex: 1; overflow-y: auto; padding: 12px 18px 24px; min-width: 0; }
/* 页面内复用（原弹窗里带外边距，这里改成铺满） */
.sys-body .rbac-hint { margin: 0 0 12px; }
.sys-body .rbac-toolbar { padding: 0 0 10px; }
.sys-body .rbac-roles { padding: 0 0 12px; }
.sys-body .rbac-list { padding: 0; max-height: none; }
/* ===== 报表式表格（表头灰底居中 + 单元格竖线分隔） ===== */
.table-wrap {
  border: 1px solid #e6e9ef; border-radius: 4px; overflow: hidden; background: #fff;
}
.user-thead, .rbac-user {
  display: grid;
  grid-template-columns: 150px 96px minmax(240px, 1fr) 76px 128px 56px 208px;
  align-items: stretch;
  gap: 0;
}
.user-thead {
  position: sticky; top: 0; z-index: 2;
  background: #f7f8fa; border-bottom: 1px solid #e6e9ef;
  font-size: 13px; font-weight: 600; color: #333;
}
.user-thead > span, .rbac-user > div {
  padding: 13px 10px; text-align: center;
  display: flex; align-items: center; justify-content: center;
  border-right: 1px solid #f0f2f5; min-width: 0;
}
.user-thead > span:last-child, .rbac-user > div:last-child { border-right: none; }
.user-thead > span:nth-child(6) { text-align: right; }
.user-thead > span:nth-child(7) { text-align: right; }
.cell-user { font-size: 13px; font-weight: 600; color: #334155; overflow: hidden; text-overflow: ellipsis; }
.cell-disp { font-size: 12.5px; color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cell-muted { font-size: 12px; color: #94a3b8; white-space: nowrap; }
.cell-muted:nth-of-type(1) { text-align: right; }

/* ===== 角色列表（表头同用户表） ===== */
.role-thead, .role-row {
  grid-template-columns: 180px 190px 90px 80px minmax(200px, 1fr) 160px;
}
.role-row { cursor: pointer; transition: border-color .15s, background .15s; }
.role-row:hover { background: #fafbfc; }
.role-row-on { background: #F5F8FF; box-shadow: inset 3px 0 0 #6366F1; }
.rbac-tag.builtin { background: #f1f5f9; color: #64748b; }
.rbac-tag.custom { background: #EEF2FF; color: #4338CA; }
.role-thead > span:nth-child(4), .role-thead > span:nth-child(6) { text-align: right; }
.role-row .cell-muted:nth-child(4) { text-align: right; }

/* ===== 状态文字 / 操作链接 ===== */
.status-ok { color: #0f9d58; font-size: 12.5px; }
.status-off { color: #d93025; font-size: 12.5px; }
.link-btn {
  border: none; background: transparent; color: #1a73e8; font-size: 13px;
  cursor: pointer; padding: 2px 5px; white-space: nowrap;
}
.link-btn:hover { text-decoration: underline; }
.link-btn:disabled { color: #b0b7c3; cursor: not-allowed; }
.link-btn.danger { color: #d93025; }
.link-btn.danger:disabled { color: #b0b7c3; }

/* ===== 分页 ===== */.pager {
  display: flex; align-items: center; gap: 10px; justify-content: flex-end;
  padding: 10px 2px 4px; border-top: 1px dashed #e2e8f0; margin-top: 6px;
}
.pager-info { font-size: 12.5px; color: #64748b; margin-right: auto; }
.rbac-toolbar.toolbar-right { justify-content: flex-end; }
.input-narrow { flex: 0 0 230px !important; width: 230px; min-width: 0; }
.push-right { margin-left: auto; }
/* 三段式网格：中间组严格居中，右侧靠右 */
.rbac-toolbar.toolbar-center {
  display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 10px;
}
.tb-group { display: flex; align-items: center; gap: 10px; }
.tb-mid { justify-self: center; }
.tb-right { justify-self: end; }

/* ===== 角色标签 ===== */
.role-chip {
  display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
  background: #EEF2FF; color: #4338CA; border: 1px solid #DDE3FB;
  border-radius: 999px; padding: 2px 9px; white-space: nowrap;
}
.role-chip.more { background: #f1f5f9; color: #64748b; border-color: #e2e8f0; cursor: help; }
.chip-x {
  border: none; background: transparent; color: #818cf8; font-size: 10px;
  cursor: pointer; padding: 0 1px; line-height: 1;
}
.chip-x:hover { color: #ef4444; }
.chip-add { padding: 3px 8px; font-size: 12px; border-radius: 999px; }

/* ===== 行内编辑 / 按钮 ===== */
.btn-primary {
  background: linear-gradient(135deg, #4F46E5, #6366F1); color: #fff; border: none;
  border-radius: 8px; padding: 7px 14px; font-size: 13px; font-weight: 600; cursor: pointer;
  box-shadow: 0 3px 10px rgba(79,70,229,.22); white-space: nowrap;
}
.btn-primary:hover { filter: brightness(1.06); }
.btn-primary:disabled { opacity: .6; cursor: not-allowed; }
.edit-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  background: #F8FAFF; border: 1px dashed #C7D2FE; border-radius: 10px;
  padding: 10px 12px; margin-bottom: 10px;
}
.input-sm { flex: 1 1 160px; min-width: 140px; }
.input-xs { width: 110px; padding: 3px 8px; font-size: 12px; border: 1px solid #cbd5e1; border-radius: 6px; }
.rbac-tag {
  font-size: 10.5px; color: #64748b; background: #f1f5f9; border-radius: 4px;
  padding: 0 5px; margin-left: 6px; font-weight: 400;
}

/* ===== 右侧标签页 ===== */
.tab-strip { display: flex; align-items: center; gap: 4px; overflow-x: auto; flex: 1; min-width: 0; }
.tab-item {
  display: flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 8px;
  font-size: 13px; color: #64748b; cursor: pointer; white-space: nowrap;
}
.tab-item:hover { background: #f1f5f9; color: #1e293b; }
.tab-item.active { background: #EEF2FF; color: #4338CA; font-weight: 600; }
.tab-x { border: none; background: transparent; color: #94a3b8; font-size: 11px; cursor: pointer; padding: 0 2px; line-height: 1; }
.tab-x:hover { color: #ef4444; }
/* tab 内容区：盖住聊天区，标题跟随 tab */
.tab-pane {
  position: absolute; top: 57px; left: 0; right: 0; bottom: 0; z-index: 20;
  background: #f7f9fc; overflow-y: auto;
}
.pane-head { padding: 14px 20px 6px; }
.pane-head h3 { margin: 0 0 2px; font-size: 15px; color: #1e293b; }
.pane-head .skill-sub { margin: 0; }

/* ===== 系统设置：侧边栏子菜单（唯一导航） ===== */
.sys-chevron { margin-left: auto; transition: transform .16s ease; opacity: .8; }
.sys-chevron.collapsed { transform: rotate(-90deg); }
.sys-submenu { padding: 2px 0 6px 0; display: flex; flex-direction: column; gap: 2px; }
.sys-menu-item {
  font-size: 13px; color: #475569; padding: 7px 12px 7px 34px; border-radius: 8px;
  cursor: pointer; position: relative;
}
.sys-menu-item::before {
  content: ''; position: absolute; left: 20px; top: 50%; width: 5px; height: 5px;
  border-radius: 50%; background: #cbd5e1; transform: translateY(-50%);
}
.sys-menu-item:hover { background: #eef2f7; color: #1e293b; }
.sys-menu-item.active { background: #EEF2FF; color: #4338CA; font-weight: 600; }
.sys-menu-item.active::before { background: #6366F1; }
.tool-alias { font-size: 13px; font-weight: 600; color: #1e293b; margin-right: 8px; }

/* ===== 一级大标题（内置工具 / MCP 工具） ===== */
.sec-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin: 18px 0 12px; padding: 9px 14px;
  border-radius: 8px; font-size: 14px; font-weight: 800; letter-spacing: .02em;
  border-left: 4px solid transparent;
}
.sec-head:first-child { margin-top: 4px; }
.sec-builtin { background: linear-gradient(90deg, #EEF2FF 0%, #F6F5FF 100%); color: #3730A3; border-left-color: #6366F1; }
.sec-mcp { background: linear-gradient(90deg, #ECFEFF 0%, #EFF6FF 100%); color: #0E7490; border-left-color: #06B6D4; }
.sec-count { font-size: 11.5px; font-weight: 600; opacity: .72; }

/* ===== 角色工具：MCP 按服务分组 ===== */
.mcp-group-head {
  display: flex; align-items: center; gap: 10px;
  margin: 10px 0 6px; padding: 6px 10px;
  background: #f6f8fc; border-left: 3px solid #c7d2fe; border-radius: 6px;
}
.mcp-group-name { font-size: 12.5px; font-weight: 700; color: #4338CA; letter-spacing: .02em; }
.mcp-group-count { font-size: 11.5px; color: #94a3b8; }
.mcp-group-item { margin-left: 6px; }

/* ===== 角色管理：左右分栏（主从布局） ===== */
.roles-split { display: grid; grid-template-columns: 430px minmax(0, 1fr); gap: 18px; align-items: stretch; height: calc(100vh - 130px); }
.roles-left, .roles-right {
  background: #fff; border: 1px solid #e6e9ef; border-radius: 10px;
  box-shadow: 0 1px 2px rgba(15,23,42,.045); overflow: hidden;
  min-height: 0;
}
.roles-left { display: flex; flex-direction: column; }
.roles-left-head { display: flex; gap: 8px; padding: 12px; border-bottom: 1px solid #f0f2f5; background: #fbfcfd; }
.roles-left-head .rbac-input { flex: 1 1 auto; min-width: 0; height: 32px; }
.btn-sm { height: 32px; padding: 0 12px; font-size: 13px; white-space: nowrap; }
.role-add { padding: 12px; border-bottom: 1px solid #f0f2f5; background: #f8fafc; display: flex; flex-direction: column; gap: 8px; }
.role-add .rbac-input { height: 32px; }
.role-add-actions { display: flex; gap: 8px; }
.roles-list { max-height: 52vh; overflow-y: auto; }
.role-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 12px 14px; border-bottom: 1px solid #f4f6f9; cursor: pointer; transition: background .12s ease;
}
.role-item:hover { background: #f8fafc; }
.role-item.active { background: #eef2ff; box-shadow: inset 3px 0 0 #6366F1; }
.role-item-main { min-width: 0; }
.role-item-name { font-size: 13.5px; font-weight: 600; color: #1e293b; }
.role-item-sub { display: flex; align-items: center; gap: 8px; margin-top: 4px; font-size: 11.5px; color: #94a3b8; }
.role-item-count { color: #64748b; }
.roles-left-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 12px; border-top: 1px solid #f0f2f5; background: #fbfcfd;
}
.roles-right { display: flex; flex-direction: column; min-height: 0; height: 100%; }
.roles-right-head {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 14px 18px; border-bottom: 1px solid #f0f2f5; background: #fbfcfd;
}
.roles-right-title { font-size: 14px; font-weight: 600; color: #1e293b; }
.roles-right-body { flex: 1; min-height: 0; padding: 14px 18px; overflow-y: auto; }
.roles-right-foot { padding: 12px 18px; border-top: 1px solid #f0f2f5; background: #fbfcfd; }
.roles-empty { flex: 1; display: flex; align-items: center; justify-content: center; color: #94a3b8; font-size: 13px; }
@media (max-width: 1100px) { .roles-split { grid-template-columns: 1fr; } }

/* 左栏：普通列表（带表头） */
.role-thead, .role-trow { display: grid; grid-template-columns: minmax(0, 1fr) 118px 64px; align-items: stretch; }
.role-thead {
  position: sticky; top: 0; z-index: 2;
  background: #f7f8fa; border-bottom: 1px solid #e6e9ef;
  font-size: 12.5px; font-weight: 600; color: #333;
}
.role-thead > span, .role-trow > div {
  display: flex; align-items: center; padding: 10px 12px; min-width: 0;
  border-right: 1px solid #f0f2f5;
}
.role-thead > span { justify-content: center; }
.role-thead > span:last-child, .role-trow > div:last-child { border-right: none; }
.role-thead > span:last-child, .role-trow > div:last-child { justify-content: center; }
.role-trow { border-bottom: 1px solid #f4f6f9; cursor: pointer; font-size: 13px; transition: background .12s ease; }
.role-builtin {
  display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 999px;
  background: #eef2ff; color: #4338ca; font-size: 10.5px; font-weight: 500; vertical-align: 1px;
}
.role-trow:hover { background: #f8fafc; }
.role-trow.active { background: #eef2ff; box-shadow: inset 3px 0 0 #6366F1; }

/* ===== 角色工具：保存按钮 ===== */
.role-tool-actions { display: flex; align-items: center; gap: 10px; margin-top: 16px; }
.appr-ok {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  min-width: 100px; height: 36px; padding: 0 24px;
  border: 0; border-radius: 9px; cursor: pointer;
  font-size: 13.5px; font-weight: 600; letter-spacing: .3px; color: #fff;
  background: linear-gradient(135deg, #6366F1 0%, #4338CA 100%);
  box-shadow: 0 4px 14px rgba(79,70,229,.32), inset 0 1px 0 rgba(255,255,255,.18);
  transition: transform .12s ease, box-shadow .15s ease, filter .15s ease;
}
.appr-ok:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 7px 20px rgba(79,70,229,.42), inset 0 1px 0 rgba(255,255,255,.22);
  filter: brightness(1.04);
}
.appr-ok:active:not(:disabled) { transform: translateY(0); box-shadow: 0 3px 10px rgba(79,70,229,.3); }
.appr-ok:disabled { opacity: .62; cursor: not-allowed; box-shadow: none; }
.appr-ok:focus-visible { outline: 3px solid rgba(99,102,241,.32); outline-offset: 2px; }

/* ===== 工具管理：表格列宽（说明列放大） ===== */
.tools-thead, .tools-row {
  display: grid;
  grid-template-columns: 200px 110px 84px minmax(360px, 1fr) 96px 90px 130px;
  align-items: stretch; gap: 0;
}
.tools-thead {
  position: sticky; top: 0; z-index: 2;
  background: #f7f8fa; border-bottom: 1px solid #e6e9ef;
  font-size: 13px; font-weight: 600; color: #333;
}
.tools-thead > span, .tools-row > div {
  padding: 12px 10px; text-align: center;
  display: flex; align-items: center; justify-content: center;
  border-right: 1px solid #f0f2f5; min-width: 0;
}
.tools-thead > span:last-child, .tools-row > div:last-child { border-right: none; }
.tools-row { border-bottom: 1px solid #f0f2f5; }
.tools-row:last-child { border-bottom: none; }
.tools-row > div:nth-child(1) { flex-direction: column; align-items: flex-start; justify-content: center; gap: 2px; }
.tools-row > div:nth-child(4) { justify-content: flex-start; text-align: left; }
.tools-row > div:nth-child(4) .cell-disp { white-space: normal; line-height: 1.5; }

/* ===== 轨迹页 ===== */
.trace-thead, .trace-row {
  display: grid;
  grid-template-columns: 110px 110px 90px minmax(320px, 1fr) 120px 80px;
  align-items: stretch;
}
.trace-thead {
  background: #f7f8fa; border-bottom: 1px solid #e6e9ef;
  font-size: 12.5px; font-weight: 600; color: #333;
}
.trace-thead > span, .trace-row > div {
  display: flex; align-items: center; justify-content: center;
  padding: 10px 10px; min-width: 0; border-right: 1px solid #f0f2f5;
}
.trace-thead > span:last-child, .trace-row > div:last-child { border-right: none; }
.trace-row { border-bottom: 1px solid #f4f6f9; font-size: 12.5px; }
.trace-row:last-child { border-bottom: none; }
.trace-tools { flex-wrap: wrap; gap: 6px; justify-content: flex-start !important; }
.trace-tool {
  font-size: 11.5px; color: #3730A3; background: #EEF2FF;
  border: 1px solid #E0E7FF; border-radius: 5px; padding: 1px 6px;
}
.trace-tool em { color: #6366F1; font-style: normal; margin-left: 3px; }

/* ===== 只读工具清单行（技能与工具页复用角色管理排版） ===== */
.tool-line {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  padding: 7px 12px; border-radius: 6px;
}
.tool-line:hover { background: #f8fafc; }
.tool-note { font-size: 12px; color: #94a3b8; }

/* ===== MCP 服务：名称列 + 编辑面板 ===== */
.mcp-name-cell { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.mcp-name-cell b { font-size: 13px; color: #1e293b; }
.mcp-name-cell code { font-size: 11px; color: #94a3b8; }
.mcp-edit-panel {
  margin: 0 0 12px; padding: 14px 16px; background: #f8fafc;
  border: 1px solid #e2e8f0; border-radius: 10px; display: flex; flex-direction: column; gap: 9px;
}
.mcp-edit-title { font-size: 13px; font-weight: 600; color: #1e293b; }
.mcp-edit-title code { font-size: 12px; color: #6366F1; }
.mcp-edit-row { display: flex; align-items: center; gap: 10px; }
.mcp-edit-row > span { flex: 0 0 190px; font-size: 12.5px; color: #475569; }
.mcp-edit-actions { display: flex; gap: 8px; margin-top: 2px; }

/* ===== 子页签（工具 / MCP 服务） ===== */
.seg-tabs { display: flex; gap: 6px; margin-bottom: 12px; }
.seg-tab {
  height: 32px; padding: 0 16px; border-radius: 8px; cursor: pointer;
  border: 1px solid #e2e8f0; background: #fff; color: #475569; font-size: 13px;
  transition: all .14s ease;
}
.seg-tab:hover { border-color: #c7d2fe; color: #4338CA; }
.seg-tab.active { background: #EEF2FF; border-color: #c7d2fe; color: #4338CA; font-weight: 600; }

/* ===== MCP 服务表格 ===== */
.mcp-thead, .mcp-row {
  display: grid;
  grid-template-columns: 140px 84px minmax(260px, 1.3fr) 72px minmax(180px, 1fr) 64px 150px;
  align-items: stretch;
}
.mcp-thead {
  position: sticky; top: 0; z-index: 2;
  background: #f7f8fa; border-bottom: 1px solid #e6e9ef;
  font-size: 12.5px; font-weight: 600; color: #333;
}
.mcp-thead > span, .mcp-row > div {
  display: flex; align-items: center; justify-content: center;
  padding: 11px 10px; min-width: 0; border-right: 1px solid #f0f2f5;
}
.mcp-thead > span:last-child, .mcp-row > div:last-child { border-right: none; }
.mcp-row { border-bottom: 1px solid #f4f6f9; font-size: 13px; }
.mcp-row:last-child { border-bottom: none; }
.mcp-row > div:nth-child(3), .mcp-row > div:nth-child(5) { justify-content: flex-start; text-align: left; }
.mcp-row code { font-size: 11.5px; color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 传输标识：统一 streamable_http */
.mcp-transport-chip {
  flex: 0 0 auto; margin-right: 6px; padding: 1px 6px; border-radius: 5px;
  font-size: 10.5px; font-family: ui-monospace, monospace;
  background: #eef2ff; color: #4338CA; border: 1px solid #dbe1fb;
}
.mcp-url { flex: 1 1 auto; min-width: 0; }
.mcp-headers-input { height: 84px; padding: 8px 10px; resize: vertical; line-height: 1.6; font-family: ui-monospace, monospace; font-size: 12.5px; }
.mcp-edit-row-top { align-items: flex-start; }
.mcp-edit-row-top > span { padding-top: 6px; }
.mcp-field-note { margin-top: 4px; font-size: 11.5px; color: #94a3b8; font-weight: 400; line-height: 1.5; }
.mcp-enable { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: #475569; }
.link-danger { color: #b91c1c; }
.link-danger:hover { color: #dc2626; }

/* ===== 工具管理：行内编辑 ===== */
.inline-input {
  width: 100%; height: 30px; padding: 0 8px; font-size: 13px;
  border: 1px solid #cbd5e1; border-radius: 6px; background: #fff; color: #1e293b;
}
.inline-input:focus { outline: none; border-color: #6366F1; box-shadow: 0 0 0 3px rgba(99,102,241,.15); }

/* ===== 工具元数据编辑 ===== */
.tool-code { font-size: 11.5px; color: #94a3b8; margin-left: 6px; }
.tool-edit-form {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0 4px;
  padding: 10px 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
}
.tool-edit-form .rbac-input { flex: 1 1 220px; min-width: 160px; }
.tool-edit-form .rbac-input.input-narrow { flex: 0 0 130px; }
.tool-edit-form .appr-ok { min-width: 76px; height: 32px; padding: 0 16px; }

/* ===== 轻提示 & 确认框 ===== */
.toast {
  position: fixed; top: 22px; left: 50%; transform: translateX(-50%); z-index: 9999;
  padding: 10px 18px; border-radius: 10px; font-size: 13.5px; line-height: 1.5;
  background: #1e293b; color: #fff; box-shadow: 0 10px 26px rgba(15,23,42,.22);
  max-width: 70vw; text-align: center;
}
.toast.err { background: #b91c1c; }
.confirm-card {
  background: #fff; border-radius: 14px; padding: 22px 24px 16px;
  width: 420px; max-width: 90vw; box-shadow: 0 24px 60px rgba(15,23,42,.25);
}
.confirm-msg { font-size: 14px; color: #1e293b; line-height: 1.75; white-space: pre-wrap; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }

</style>
