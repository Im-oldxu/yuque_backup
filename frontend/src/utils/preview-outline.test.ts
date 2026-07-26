import { describe, expect, it } from 'vitest'
import { createHeadingId, extractPreviewOutline, hasReadablePreview, preparePreviewDocument } from './preview-outline'

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
      { id: 'overview', level: 1, text: '概览' },
      { id: '安装与启动', level: 2, text: '安装与启动' },
      { id: '环境变量', level: 3, text: '环境变量' },
      { id: '安装与启动-2', level: 2, text: '安装与启动' },
    ])
  })

  it('prepares an existing sanitized preview for styled anchored reading', () => {
    const parsed = new DOMParser().parseFromString(`
      <html><head></head><body>
        <p><span>[TOC]</span></p>
        <h2>安装与启动</h2>
        <h2>安装与启动</h2>
        <img src="/api/v1/assets/one/content" title="null">
      </body></html>
    `, 'text/html')

    preparePreviewDocument(parsed)

    expect(parsed.getElementById('yb-preview-reader-styles')?.textContent).toContain('.lake-content')
    expect(parsed.body.textContent).not.toContain('[TOC]')
    expect(Array.from(parsed.querySelectorAll('h2')).map((heading) => heading.id)).toEqual([
      '安装与启动',
      '安装与启动-2',
    ])
    expect(parsed.querySelector('img')?.hasAttribute('title')).toBe(false)
  })
})

describe('hasReadablePreview', () => {
  it('ignores generated empty wrappers and the Yuque TOC marker', () => {
    expect(hasReadablePreview('<div class="lake-content"><p>[TOC]</p><pre></pre></div>')).toBe(false)
  })

  it('accepts text and non-text article content', () => {
    expect(hasReadablePreview('<p>正文</p>')).toBe(true)
    expect(hasReadablePreview('<figure><img src="local.png"></figure>')).toBe(true)
  })
})
