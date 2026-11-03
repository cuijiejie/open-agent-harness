import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './style.css'

// 自动创建挂载容器，宿主页面无需提供 #app
let mountEl = document.getElementById('cw-app')
if (!mountEl) {
  mountEl = document.createElement('div')
  mountEl.id = 'cw-app'
  document.body.appendChild(mountEl)
}

const app = createApp(App)
app.use(ElementPlus)
app.mount(mountEl)
