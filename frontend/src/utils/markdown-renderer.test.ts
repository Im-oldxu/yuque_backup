import { describe, expect, it } from 'vitest'
import { headingSlug, renderMarkdown } from './markdown-renderer'

describe('Markdown renderer', () => {
  it('renders headings, tables, code and remote images', async () => {
    const result = await renderMarkdown(`# 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n\`\`\`ts\nconst ok = true\n\`\`\`\n\n![图](https://cdn.example/a.png)`)

    expect(result.html).toContain('<table>')
    expect(result.html).toContain('class="shiki github-light"')
    expect(result.html).toContain('src="https://cdn.example/a.png"')
    expect(result.html).toContain('loading="lazy"')
    expect(result.headings).toEqual([{ id: 'heading-标题', text: '标题', level: 1 }])
    expect(result.hasReadableContent).toBe(true)
  })

  it('sanitizes active HTML and falls back for unknown code languages', async () => {
    const result = await renderMarkdown('<script>alert(1)</script><font color="red">保留文字</font>\n\n```unknown\n<x>\n```')

    expect(result.html).not.toContain('<script')
    expect(result.html).not.toContain('alert(1)')
    expect(result.html).toContain('保留文字')
    expect(result.html).toContain('&lt;x&gt;')
  })

  it('creates stable unicode heading ids', () => {
    expect(headingSlug('安装 / Setup')).toBe('heading-安装-setup')
  })
})
