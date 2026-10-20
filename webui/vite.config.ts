import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// 开发时把 /webui/api 转发到真实后端；可用 TENKO_BACKEND 指向其他地址。
const backend = process.env.TENKO_BACKEND ?? 'http://127.0.0.1:8081'

// https://vite.dev/config/
export default defineConfig({
  // 后端把面板挂在 /webui 下，会话 Cookie 的 Path 也是 /webui，开发服务器保持一致。
  base: '/webui/',
  plugins: [
    vue(),
    vueDevTools(),
    AutoImport({ resolvers: [ElementPlusResolver()], dts: 'src/auto-imports.d.ts' }),
    Components({ resolvers: [ElementPlusResolver()], dts: 'src/components.d.ts' }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/webui/api': {
        target: backend,
        changeOrigin: true,
        configure: (proxy) => {
          // 后端会拒绝 Origin 与自身主机不同的写请求，开发代理直接去掉该请求头。
          proxy.on('proxyReq', (proxyReq) => proxyReq.removeHeader('origin'))
        },
      },
    },
  },
  build: {
    // 构建产物提交进仓库，由宿主在 tenko/webui/static 下直接提供。
    outDir: '../tenko/webui/static',
    emptyOutDir: true,
  },
})
