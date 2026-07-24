import { describe, expect, it } from 'vitest'
import { createHeadingId, extractPreviewOutline } from './preview-outline'

describe('preview outline heading ids', () => {
  it('keeps readable Chinese and Latin heading text', () => {
    expect(createHeadingId('恢复前检查', 0)).toBe('恢复前检查')
    expect(createHeadingId('API Recovery Guide', 1)).toBe('api-recovery-guide')
  })

  it('removes punctuation and provides an empty-heading fallback', () => {
    expect(createHeadingId('步骤：检查 / 恢复！', 0)).toBe('步骤检查-恢复')
    expect(createHeadingId('***', 2)).toBe('heading-3')
  })

  it('extracts the Markdown heading hierarchy from preview HTML', () => {
    const outline = extractPreviewOutline(`
      <article>
        <h1 id="overview">概览</h1>
        <h2>安装与启动</h2>
        <h3>环境变量</h3>
        <h2>安装与启动</h2>
      </article>
    `)

    expect(outline).toEqual([
      { fragment: 'overview', id: 'overview', level: 1, text: '概览' },
      { fragment: ':~:text=%E5%AE%89%E8%A3%85%E4%B8%8E%E5%90%AF%E5%8A%A8', id: '安装与启动', level: 2, text: '安装与启动' },
      { fragment: ':~:text=%E7%8E%AF%E5%A2%83%E5%8F%98%E9%87%8F', id: '环境变量', level: 3, text: '环境变量' },
      { fragment: ':~:text=%E5%AE%89%E8%A3%85%E4%B8%8E%E5%90%AF%E5%8A%A8', id: '安装与启动-2', level: 2, text: '安装与启动' },
    ])
  })
})
