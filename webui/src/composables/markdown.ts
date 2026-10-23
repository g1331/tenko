// 发布说明用到的 Markdown 子集：标题、（嵌套）列表、段落、代码块、粗体、行内代码和链接。
// 先整体转义再加标签，输出只含这里生成的标签，可以直接 v-html。

const escape = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function inline(text: string): string {
  const codes: string[] = []
  let html = escape(text).replace(/`([^`]+)`/g, (_, code: string) => {
    codes.push(`<code>${code}</code>`)
    return `\u0000${codes.length - 1}\u0000`
  })
  html = html
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    )
  return html.replace(/\u0000(\d+)\u0000/g, (_, index: string) => codes[Number(index)] ?? '')
}

export function renderMarkdown(source: string): string {
  const out: string[] = []
  const lists: { indent: number; tag: 'ul' | 'ol' }[] = []
  let paragraph: string[] = []
  let code: string[] | null = null

  const flushParagraph = () => {
    if (paragraph.length) out.push(`<p>${inline(paragraph.join(' '))}</p>`)
    paragraph = []
  }
  const closeLists = (indent = -1) => {
    while (lists.length && lists[lists.length - 1]!.indent > indent) {
      out.push(`</li></${lists.pop()!.tag}>`)
    }
  }

  for (const line of source.replace(/\r\n?/g, '\n').split('\n')) {
    if (code) {
      if (line.trimStart().startsWith('```')) {
        out.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`)
        code = null
      } else code.push(line)
      continue
    }
    if (line.trimStart().startsWith('```')) {
      flushParagraph()
      closeLists()
      code = []
      continue
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    const item = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph()
      closeLists()
      const level = Math.min(heading[1]!.length + 2, 6)
      out.push(`<h${level}>${inline(heading[2]!)}</h${level}>`)
    } else if (item) {
      flushParagraph()
      const indent = item[1]!.length
      const tag = /\d/.test(item[2]!) ? 'ol' : 'ul'
      const top = lists[lists.length - 1]
      if (!top || indent > top.indent) {
        out.push(`<${tag}><li>`)
        lists.push({ indent, tag })
      } else {
        closeLists(indent)
        out.push('</li><li>')
      }
      out.push(inline(item[3]!))
    } else if (!line.trim()) {
      flushParagraph()
    } else if (lists.length && /^\s+/.test(line)) {
      out.push(` ${inline(line.trim())}`) // 列表项的续行
    } else {
      closeLists()
      paragraph.push(line.trim())
    }
  }
  if (code) out.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`)
  flushParagraph()
  closeLists()
  return out.join('')
}
