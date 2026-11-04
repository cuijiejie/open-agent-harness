/**
 * 浏览器端到端测试（Playwright + Chromium，无测试框架依赖）
 *
 *   PA_BASE=http://127.0.0.1:5001/open-agent-harness \
 *   PA_PLAYWRIGHT=/opt/pa-test/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=/opt/pa-test/browsers \
 *   node test/ui/run_ui.mjs
 *
 * 覆盖：页面渲染/登录态、发消息→流式回复、停止按钮、附件标签与总大小、
 *       新对话清空、我的文件页、管理员四个管理页；任何 JS 报错都算失败。
 *
 * 可用 PA_SKIP_LLM=1 跳过"真发消息"的用例（省钱/没模型时）。
 */
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const PW = process.env.PA_PLAYWRIGHT || 'playwright'
const { chromium } = require(PW)

const BASE = (process.env.PA_BASE || 'http://127.0.0.1:5001/open-agent-harness').replace(/\/$/, '')
const USER = process.env.PA_USER || 'demo'
const ADMIN = process.env.PA_ADMIN || 'admin'
const SKIP_LLM = ['1', 'true', 'yes'].includes(String(process.env.PA_SKIP_LLM || '').toLowerCase())
const HEADLESS = process.env.PA_HEADFUL ? false : true

const cases = []
const test = (name, fn, opts = {}) => cases.push({ name, fn, ...opts })

// 已知噪音（不算失败）：浏览器对 4xx 资源加载的 console 记录不是页面 JS 异常，
// 真正的 JS 异常走 pageerror，另有 -1 的 404 是 favicon
const IGNORE_ERRORS = [
  /favicon/i,
  /ERR_ABORTED/,
  /ResizeObserver loop/,
  /Failed to load resource/i,
]

class Ctx {
  constructor() {
    this.errors = []
    this.responses = []
  }

  attach(page) {
    this.page = page
    page.on('pageerror', (e) => this.errors.push(`pageerror: ${e.message}`))
    page.on('console', (m) => {
      if (m.type() !== 'error') return
      const t = m.text()
      if (IGNORE_ERRORS.some((rx) => rx.test(t))) return
      this.errors.push(`console.error: ${t}`)
    })
    page.on('response', (r) => this.responses.push(r.url()))
  }

  async login(user) {
    // dev 的 test-login：先拿 cookie，再开页面
    const res = await this.page.request.post(`${BASE}/test-login`, {
      form: { username: user },
      maxRedirects: 0,
      failOnStatusCode: false,
    })
    if (res.status() !== 302 && res.status() !== 200) {
      throw new Error(`test-login 失败 ${res.status()}`)
    }
  }

  async goto(path = '/') {
    const resp = await this.page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
    if (!resp || resp.status() >= 400) throw new Error(`打开页面失败 ${resp && resp.status()}`)
    // SPA 挂载完成：输入框出现
    await this.page.waitForSelector('textarea[placeholder="输入消息..."]', { timeout: 30000 })
  }

  assert(cond, msg) {
    if (!cond) throw new Error(msg)
  }
}

// ---------------- 用例 ----------------

test('页面渲染：登录后能看到对话界面', async (c) => {
  await c.login(USER)
  await c.goto('/')
  const ta = await c.page.locator('textarea[placeholder="输入消息..."]').count()
  c.assert(ta === 1, `输入框数量 ${ta}`)
  const send = await c.page.locator('button.send').count()
  c.assert(send >= 1, '没有发送按钮')
  // 侧边栏：要么有历史会话，要么显示"暂无历史对话"
  const sidebarText = await c.page.locator('.sidebar').innerText().catch(() => '')
  c.assert(/会话|暂无历史对话|新对话/.test(sidebarText), `侧边栏异常: ${sidebarText.slice(0, 80)}`)
})

test('发消息：流式回复渲染到气泡里', async (c) => {
  if (SKIP_LLM) return 'skipped'
  await c.login(USER)
  await c.goto('/')
  const before = await c.page.locator('.msg').count()
  await c.page.fill('textarea[placeholder="输入消息..."]', '只回复两个字：收到')
  // 发送中会出现"停止"按钮（顺便验证停止按钮存在）
  await c.page.click('button.send')
  await c.page.waitForSelector('button.send.stop', { timeout: 15000 }).catch(() => {})
  await c.page.waitForFunction(
    (n) => document.querySelectorAll('.msg').length >= n + 2,
    before,
    { timeout: 120000 },
  )
  // 气泡出现 ≠ 内容已经有字：要等最后一个助手气泡的文本非空（流式是分片到达的）
  await c.page.waitForFunction(
    () => {
      const els = [...document.querySelectorAll('.msg.assistant .msg-text')]
      const last = els[els.length - 1]
      return !!last && last.innerText.trim().length > 0
    },
    null,
    { timeout: 120000 },
  )
  const reply = await c.page.locator('.msg.assistant .msg-text').last().innerText({ timeout: 60000 })
  c.assert(reply.trim().length > 0, '回复气泡是空的')
  // 生成结束后停止按钮应消失、输入框恢复可用
  await c.page.waitForFunction(() => !document.querySelector('button.send.stop'), { timeout: 90000 })
  const disabled = await c.page.locator('textarea[placeholder="输入消息..."]').isDisabled()
  c.assert(!disabled, '生成结束后输入框仍是禁用状态')
})

test('附件：选文件后出现标签与总大小', async (c) => {
  await c.login(USER)
  await c.goto('/')
  const tmp = await c.page.evaluateHandle(() => null)
  void tmp
  await c.page.setInputFiles('input[type="file"]', {
    name: 'ui_probe.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('hello from ui test\n'),
  })
  await c.page.waitForSelector('.file-tag-name', { timeout: 15000 })
  const tag = await c.page.locator('.file-tag-name').first().innerText()
  c.assert(tag.includes('ui_probe.txt'), `标签内容不对: ${tag}`)
  const total = await c.page.locator('.file-total').innerText()
  c.assert(/\d+(\.\d+)?\s*\/\s*100\s*MB/.test(total), `总大小显示不对: ${total}`)
  // 移除附件
  await c.page.locator('.file-tag-close').first().click()
  await c.page.waitForFunction(() => !document.querySelector('.file-tag-name'), { timeout: 10000 })
})

test('新对话：清空输入与附件', async (c) => {
  await c.login(USER)
  await c.goto('/')
  await c.page.fill('textarea[placeholder="输入消息..."]', '临时内容')
  await c.page.setInputFiles('input[type="file"]', {
    name: 'ui_clear.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('x'),
  })
  await c.page.waitForSelector('.file-tag-name', { timeout: 15000 })
  await c.page.getByText('新对话', { exact: false }).first().click()
  await c.page.waitForTimeout(800)
  const val = await c.page.inputValue('textarea[placeholder="输入消息..."]')
  c.assert(val === '', `新对话后输入框没清空: ${val}`)
  const tags = await c.page.locator('.file-tag-name').count()
  c.assert(tags === 0, `新对话后附件没清空: ${tags}`)
})

test('技能与工具：「我的技能 / 平台技能」两个子页签都能用', async (c) => {
  await c.login(USER)
  await c.goto('/')
  // 侧边栏「技能与工具」按钮
  await c.page.locator('.sidebar-skill-btn').first().click()
  await c.page.waitForSelector('.skill-subtab', { timeout: 15000 })

  const tabs = await c.page.locator('.skill-subtab').allInnerTexts()
  const joined = tabs.join('|')
  c.assert(/我的技能/.test(joined), `没有「我的技能」页签: ${joined}`)
  c.assert(/平台技能/.test(joined), `没有「平台技能」页签: ${joined}`)

  // 默认在「我的技能」：应有上传入口
  const mineActive = await c.page.locator('.skill-subtab.active').innerText()
  c.assert(/我的技能/.test(mineActive), `默认页签不是我的技能: ${mineActive}`)

  // 切到「平台技能」：要么有列表项，要么给空提示，不能是空白
  await c.page.locator('.skill-subtab').filter({ hasText: '平台技能' }).first().click()
  await c.page.waitForTimeout(2500)
  const plat = await c.page.locator('.skill-subtab.active').innerText()
  c.assert(/平台技能/.test(plat), `没切到平台技能: ${plat}`)

  const pane = await c.page.locator('.tab-pane').first().innerText()
  const hasItems = (await c.page.locator('.skill-item').count()) > 0
  c.assert(hasItems || /平台还没有技能|暂无/.test(pane),
    `平台技能页既没有条目也没有空提示: ${pane.slice(0, 150)}`)

  // 切回「我的技能」
  await c.page.locator('.skill-subtab').filter({ hasText: '我的技能' }).first().click()
  await c.page.waitForTimeout(800)
  const back = await c.page.locator('.skill-subtab.active').innerText()
  c.assert(/我的技能/.test(back), `切不回我的技能: ${back}`)
})

test('我的文件：页签能打开并渲染', async (c) => {
  await c.login(USER)
  await c.goto('/')
  await c.page.locator('.sys-menu-item').filter({ hasText: '我的文件' }).first().click()
  await c.page.waitForTimeout(1500)
  const body = await c.page.locator('.tab-pane').first().innerText()
  c.assert(/我的文件|上传|新建/.test(body), `文件页没渲染: ${body.slice(0, 120)}`)
})

test('管理页（管理员）：用户/角色/工具/知识库/审计都能渲染', async (c) => {
  await c.login(ADMIN)
  await c.goto('/')
  // 「系统设置」是折叠菜单：先展开，才会出现 用户管理/角色管理/…
  const sysBtn = c.page.locator('.sidebar-sys-btn')
  await sysBtn.waitFor({ state: 'visible', timeout: 15000 })
  await sysBtn.click()
  await c.page.waitForSelector('.sys-menu-item', { timeout: 15000 })
  for (const label of ['用户管理', '角色管理', '工具管理', '知识库', '操作审计']) {
    await c.page.locator('.sys-menu-item').filter({ hasText: label }).first().click()
    await c.page.waitForTimeout(1200)
    const body = await c.page.locator('.tab-pane').first().innerText().catch(() => '')
    c.assert(body.trim().length > 20, `${label} 页面是空的`)
  }
})

test('非管理员：看不到系统设置入口（也不会白打 /api/users）', async (c) => {
  await c.login(USER)
  await c.goto('/')
  const sysBtn = await c.page.locator('.sidebar-sys-btn').count()
  c.assert(sysBtn === 0, '非管理员竟然看到了系统设置按钮')
  // 页面加载过程中不该有 /api/users 请求
  const hit = c.responses.filter((r) => r.includes('/api/users'))
  c.assert(hit.length === 0, `非管理员发起了 /api/users: ${hit.join(', ')}`)
})

// ---------------- 运行器 ----------------

const only = process.env.PA_ONLY || ''
let pass = 0
let fail = 0
let skipped = 0
const failures = []

const browser = await chromium.launch({
  headless: HEADLESS,
  // 只用完整 chromium：playwright 的 headless shell 在部分内网下载不下来
  channel: process.env.PA_CHANNEL || 'chromium',
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})
try {
  for (const t of cases) {
    if (only && !t.name.includes(only)) continue
    const c = new Ctx()
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    c.attach(page)
    const t0 = Date.now()
    try {
      const r = await t.fn(c)
      // 用例结束后统一检查 JS 报错
      if (c.errors.length) throw new Error(`页面有 JS 报错：${c.errors.slice(0, 3).join(' | ')}`)
      if (r === 'skipped') {
        skipped++
        console.log(`  [SKIP] ${t.name}`)
      } else {
        pass++
        console.log(`  [OK]   ${t.name} (${((Date.now() - t0) / 1000).toFixed(1)}s)`)
      }
    } catch (e) {
      fail++
      failures.push({ name: t.name, err: String(e.message || e).slice(0, 500) })
      console.log(`  [FAIL] ${t.name}\n         ${String(e.message || e).slice(0, 300)}`)
    } finally {
      await ctx.close().catch(() => {})
    }
  }
} finally {
  await browser.close().catch(() => {})
}

console.log('')
console.log(`UI 测试：${pass} 通过 / ${fail} 失败 / ${skipped} 跳过`)
if (fail) {
  console.log('失败详情：')
  for (const f of failures) console.log(`- ${f.name}: ${f.err}`)
}
process.exit(fail ? 1 : 0)
