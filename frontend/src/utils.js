/**
 * 纯函数工具（不依赖 Vue / DOM）—— 便于用 node 直接跑单测
 *
 * 之所以抽出来：这几个函数是**安全/逻辑关键**的（导航白名单、分页窗口），
 * 但以前内联在 App.vue 里，只能靠手测。抽成纯函数后 `node --test test/js/` 就能覆盖。
 */

/**
 * 导航 URL 白名单：只允许 http(s) 与站内绝对路径
 *
 * 为什么必须挡：模型/工具输出可能被提示注入成 `javascript:` / `data:`，
 * 而调用处是 window.open / a.click —— 等于同源执行任意脚本。
 * 注意 `//evil.com` 是协议相对外链，必须挡掉。
 */
export const isSafeNavUrl = (u) => {
  const s = String(u == null ? '' : u).trim()
  if (/^https?:\/\//i.test(s)) return true
  return s.startsWith('/') && !s.startsWith('//')
}

/**
 * 侧边栏"保留当前窗口"刷新时，一次应该取多少条
 * （用户翻到第 3 页 → 已加载 40+ 条 → 刷新也按这个量取，别把人打回第 1 页）
 */
export const windowSizeFor = (loaded, pageSize = 20) => {
  const n = Number(loaded) || 0
  return Math.max(pageSize, n)
}

/**
 * 上面那个窗口对应"第几页"（下次滚动加载从 +1 开始，不能重复）
 */
export const pageForWindow = (loaded, pageSize = 20) => {
  const size = windowSizeFor(loaded, pageSize)
  return Math.max(1, Math.ceil(size / pageSize))
}

/**
 * 按最近访问顺序淘汰会话消息缓存（LRU），返回被淘汰的 sessionId 列表
 * @param {string[]} order 最近访问顺序（尾部最新，会被就地修改）
 * @param {number} limit 最多保留几个
 * @param {string} keep 当前正在看的会话（永不淘汰）
 */
export const evictMsgCache = (order, limit, keep) => {
  const dropped = []
  let guard = order.length * 2 + 4
  while (order.length > limit && guard-- > 0) {
    const sid = order.shift()
    if (!sid) continue
    if (sid === keep) {
      order.push(sid)          // 当前会话不能丢：挪到队尾
      continue
    }
    dropped.push(sid)
  }
  return dropped
}
