// 从后端契约（tenko/webui/contracts.py）生成 src/api/generated.ts。
// 用法：npm run gen:types；需要仓库根目录可用的 uv 环境。CI 重新生成后检查与提交的是否一致。
import { execFileSync } from 'node:child_process'
import { writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { compile } from 'json-schema-to-typescript'
import * as prettier from 'prettier'

const webui = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const target = resolve(webui, 'src/api/generated.ts')

const schema = JSON.parse(
  execFileSync('uv', ['run', '--frozen', 'python', '-m', 'scripts.webui_schema'], {
    cwd: resolve(webui, '..'),
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'inherit'],
  }),
)

const banner = `/**
 * 由 tenko/webui/contracts.py 生成，不要手工修改。
 * 修改契约后在 webui/ 运行 npm run gen:types 并提交本文件。
 */`

const source = await compile(schema, schema.title, {
  additionalProperties: false,
  unreachableDefinitions: true,
  bannerComment: banner,
  format: false,
})
const options = (await prettier.resolveConfig(target)) ?? {}
const formatted = await prettier.format(source, { ...options, filepath: target })
// 去掉生成器给每个定义附加的来源说明和空的根类型，它们对使用者没有信息。
const origin = ' \\* This interface was referenced by `\\w+`\'s JSON-Schema\\n \\* via the `definition` "\\w+"\\.\\n'
await writeFile(
  target,
  formatted
    .replace(new RegExp(` \\*\\n${origin}`, 'g'), '')
    .replace(new RegExp(`/\\*\\*\\n${origin} \\*/\\n`, 'g'), '')
    .replace(`export interface ${schema.title} {}\n`, ''),
)
