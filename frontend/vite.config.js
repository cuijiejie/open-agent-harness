import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { readFileSync, existsSync, statSync, writeFileSync } from 'fs'

export default defineConfig({
  base: '/open-agent-harness/',
  plugins: [
    vue(),
    {
      name: 'serve-dist',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          let urlPath = req.url
          if (urlPath.startsWith('/open-agent-harness/')) {
            urlPath = urlPath.slice('/open-agent-harness'.length) || '/'
          }
          const filePath = resolve(__dirname, 'dist', urlPath.slice(1))
          if (existsSync(filePath) && statSync(filePath).isFile()) {
            const ext = filePath.split('.').pop()
            const mime = ext === 'js' ? 'application/javascript; charset=utf-8'
              : ext === 'css' ? 'text/css; charset=utf-8'
              : ext === 'html' ? 'text/html; charset=utf-8'
              : 'text/plain'
            res.writeHead(200, { 'Content-Type': mime, 'Access-Control-Allow-Origin': '*' })
            res.end(readFileSync(filePath))
            return
          }
          next()
        })
      }
    },
    {
        name: 'copy-test-html',
        // 资产名带内容哈希，写死 main-CxAWAneT.js 这种名字在下次构建后就 404 了，
        // 直接从本次 bundle 里取实际文件名
        writeBundle(_options, bundle) {
          const keys = Object.keys(bundle || {})
          const js = keys.find((k) => /assets\/main-.*\.js$/.test(k))
          const css = keys.find((k) => /assets\/main-.*\.css$/.test(k))
          let testHtml = readFileSync(resolve(__dirname, 'test.html'), 'utf-8')
          if (js) testHtml = testHtml.replace('/src/main.js', '/open-agent-harness/' + js)
          if (css) {
            testHtml = testHtml.replace('</body>', '  <link rel="stylesheet" href="/open-agent-harness/' + css + '">\n</body>')
          }
          writeFileSync(resolve(__dirname, 'dist', 'test.html'), testHtml)
        }
      }
  ],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html')
      },
      output: {
        // 文件名必须带**内容哈希**：以前写死成 main-CxAWAneT.js，
        // 代码更新后 URL 不变 → 浏览器/中间代理继续用缓存的旧 JS，用户看不到任何改动。
        entryFileNames: 'assets/main-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: (info) => {
          if (info.name?.endsWith('.css')) return 'assets/main-[hash][extname]'
          return 'assets/[name]-[hash][extname]'
        }
      }
    }
  },
  define: {
    // 构建时间戳：界面上显示，用来一眼判断"看到的是不是最新构建"
    __BUILD_TIME__: JSON.stringify(new Date().toISOString().slice(0, 16).replace('T', ' '))
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/open-agent-harness': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})
