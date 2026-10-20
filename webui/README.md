# Tenko WebUI 前端

Vue 3 + TypeScript + Vite + Element Plus（按需引入，主题变量见 `src/styles/theme.css`）。
开发与构建流程见仓库根目录 README 的“前端开发”一节：`npm run build` 的产物写入
`../tenko/webui/static/` 并随仓库提交。

## 结构

- `src/api/`：`request()` 与 `api.*` 类型化接口、全部载荷类型（`types.ts`）。
- `src/composables/`：登录态 `useAuth`、共享数据与 30 秒轮询 `usePanelData`、主题 `useTheme`、
  格式化 `format`、状态语义 `status`、二次确认 `confirmAction` 与窄屏判断 `useNarrow`。写操作统一用
  `confirmAction` 确认、成功后调用 `usePanelData().refresh()`（保证取回写入之后的数据）。
- `src/components/`：`PageContainer`、`PagePanel`、`ResourceBoundary`、`StatusTag` 等页面共用组件。
- `src/layouts/`：已登录外壳（侧栏、页头）。路由与页头文案在 `src/router/index.ts` 的 `meta` 中。
- `src/views/<Name>View.vue`：各页面，一页一个文件。

脚本：`npm run dev | build | type-check | lint | format`。
