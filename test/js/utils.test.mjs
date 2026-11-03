/**
 * 前端纯函数单测（零依赖：node 内置 node:test）
 *
 *   node --test test/js/
 *
 * 覆盖：导航 URL 白名单（安全红线）、分页窗口换算、消息缓存 LRU 淘汰。
 * 这些以前内联在 App.vue 里只能手测。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { isSafeNavUrl, windowSizeFor, pageForWindow, evictMsgCache } from '../../frontend/src/utils.js'

test('isSafeNavUrl：放行 http(s) 与站内路径', () => {
  assert.equal(isSafeNavUrl('https://example.com/a'), true)
  assert.equal(isSafeNavUrl('HTTP://EXAMPLE.COM'), true)
  assert.equal(isSafeNavUrl('http://example.com:5001/x'), true)
  assert.equal(isSafeNavUrl('/open-agent-harness/static/a.xlsx'), true)
  assert.equal(isSafeNavUrl('  /foo  '), true)          // 前后空白先 trim
})

test('isSafeNavUrl：挡掉一切可在同源执行脚本的形态', () => {
  const bad = [
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    ' javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
    '//evil.com/x',            // 协议相对外链
    'ftp://x/y',
    'about:blank',
    '',
    null,
    undefined,
    'relative/path',           // 相对路径不白名单（避免被 base 劫持）
  ]
  for (const u of bad) {
    assert.equal(isSafeNavUrl(u), false, `应该拒绝: ${JSON.stringify(u)}`)
  }
})

test('windowSizeFor：至少一页，且不小于已加载条数', () => {
  assert.equal(windowSizeFor(0), 20)
  assert.equal(windowSizeFor(7), 20)
  assert.equal(windowSizeFor(20), 20)
  assert.equal(windowSizeFor(45), 45)      // 翻到第 3 页后刷新仍取 45 条
  assert.equal(windowSizeFor(undefined), 20)
  assert.equal(windowSizeFor('60'), 60)    // 宽容字符串
})

test('pageForWindow：窗口大小换算页数（滚动加载不重复）', () => {
  assert.equal(pageForWindow(0), 1)
  assert.equal(pageForWindow(20), 1)
  assert.equal(pageForWindow(21), 2)
  assert.equal(pageForWindow(45), 3)
  assert.equal(pageForWindow(60), 3)
})

test('evictMsgCache：超过上限按最久未访问淘汰', () => {
  const order = ['a', 'b', 'c', 'd']
  const dropped = evictMsgCache(order, 3, 'd')
  assert.deepEqual(dropped, ['a'])
  assert.deepEqual(order, ['b', 'c', 'd'])
})

test('evictMsgCache：当前会话即使最老也不能被淘汰', () => {
  const order = ['a', 'b', 'c', 'd']
  const dropped = evictMsgCache(order, 3, 'a')
  assert.ok(!dropped.includes('a'), '当前会话被误删')
  // 'a' 被挪到队尾保留，只淘汰最老的 'b'
  assert.deepEqual(dropped, ['b'])
  assert.deepEqual(order, ['c', 'd', 'a'])
  assert.equal(order.length, 3)
})

test('evictMsgCache：未超上限时不动', () => {
  const order = ['a', 'b']
  assert.deepEqual(evictMsgCache(order, 12, 'a'), [])
  assert.deepEqual(order, ['a', 'b'])
})

test('evictMsgCache：空列表/空元素不崩', () => {
  assert.deepEqual(evictMsgCache([], 12, 'x'), [])
  const order = ['', 'a', 'b', 'c']
  const dropped = evictMsgCache(order, 2, 'c')
  assert.ok(dropped.length >= 1)
  assert.equal(order.length <= 2, true)
})
