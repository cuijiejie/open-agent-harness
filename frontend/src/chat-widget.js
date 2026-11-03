/**
 * 通用聊天窗口触发器 — ChatWidget
 *
 * 右下角浮动图标，点击后触发主聊天页面淡入/淡出。
 *
 * 使用方式:
 *   import { ChatWidget } from './chat-widget.js'
 *   import './chat-widget.css'
 *   const widget = ChatWidget.init({ onToggle: (open) => { chatVisible.value = open } })
 *   widget.setOpen(true)   // 外部控制图标状态
 */

const ChatWidget = (() => {
  let _onToggle = null
  let _open = false
  let _root = null
  let _toggleBtn = null

  function buildDOM(onToggle) {
    _onToggle = onToggle
    _root = document.createElement('div')
    _root.className = 'cw-container'
    _root.innerHTML = `
      <button class="cw-toggle" title="打开聊天">
        <svg class="cw-chat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        <svg class="cw-close-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    `
    _toggleBtn = _root.querySelector('.cw-toggle')
    _toggleBtn.addEventListener('click', () => {
      _open = !_open
      _root.classList.toggle('open', _open)
      if (_onToggle) _onToggle(_open)
    })
    document.body.appendChild(_root)
  }

  function setOpen(open) {
    _open = open
    if (_root) _root.classList.toggle('open', open)
  }

  function init(options = {}) {
    if (_root) _root.remove()
    buildDOM(options.onToggle || null)
    return { setOpen }
  }

  function destroy() {
    if (_root) { _root.remove(); _root = null }
  }

  return { init, destroy, setOpen }
})()

if (typeof window !== 'undefined') {
  window.ChatWidget = ChatWidget
}

export { ChatWidget }
