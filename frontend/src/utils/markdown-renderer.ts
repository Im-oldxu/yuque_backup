import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { createHighlighterCore, type HighlighterCore } from '@shikijs/core'
import { createJavaScriptRegexEngine } from '@shikijs/engine-javascript'
import bash from '@shikijs/langs/bash'
import dockerfile from '@shikijs/langs/dockerfile'
import javascript from '@shikijs/langs/javascript'
import json from '@shikijs/langs/json'
import markdownLanguage from '@shikijs/langs/markdown'
import nginx from '@shikijs/langs/nginx'
import python from '@shikijs/langs/python'
import sql from '@shikijs/langs/sql'
import typescript from '@shikijs/langs/typescript'
import yaml from '@shikijs/langs/yaml'
import githubLight from '@shikijs/themes/github-light'
import type { PreviewHeading } from './preview-outline'

const supportedLanguages = [
  'bash', 'javascript', 'typescript', 'json', 'yaml', 'python', 'sql', 'dockerfile', 'nginx', 'markdown',
] as const
const aliases: Record<string, string> = {
  sh: 'bash', shell: 'bash', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
  yml: 'yaml', py: 'python', md: 'markdown', text: 'text', plaintext: 'text', txt: 'text',
}

let highlighterPromise: Promise<HighlighterCore> | null = null

function getHighlighter(): Promise<HighlighterCore> {
  highlighterPromise ??= createHighlighterCore({
    themes: [githubLight],
    langs: [
      ...bash,
      ...dockerfile,
      ...javascript,
      ...json,
      ...markdownLanguage,
      ...nginx,
      ...python,
      ...sql,
      ...typescript,
      ...yaml,
    ],
    engine: createJavaScriptRegexEngine(),
  })
  return highlighterPromise
}

export interface RenderedMarkdown {
  html: string
  headings: PreviewHeading[]
  hasReadableContent: boolean
}

export async function renderMarkdown(source: string): Promise<RenderedMarkdown> {
  const highlighter = await getHighlighter()
  const markdown = new MarkdownIt({ html: true, linkify: true, typographer: true })
  const defaultFence = markdown.renderer.rules.fence

  markdown.renderer.rules.fence = (tokens, index, options, env, self) => {
    const token = tokens[index]
    if (!token) return ''
    const requested = token.info.trim().split(/\s+/)[0]?.toLowerCase() ?? ''
    const language = aliases[requested] ?? requested
    if (supportedLanguages.includes(language as typeof supportedLanguages[number])) {
      return highlighter.codeToHtml(token.content, { lang: language, theme: 'github-light' })
    }
    return defaultFence?.(tokens, index, options, env, self)
      ?? `<pre><code>${markdown.utils.escapeHtml(token.content)}</code></pre>`
  }

  const headingCounts = new Map<string, number>()
  markdown.core.ruler.push('yuque-heading-ids', (state) => {
    state.tokens.forEach((token, index) => {
      if (token.type !== 'heading_open') return
      const text = state.tokens[index + 1]?.content ?? ''
      const base = headingSlug(text)
      const count = headingCounts.get(base) ?? 0
      headingCounts.set(base, count + 1)
      token.attrSet('id', count ? `${base}-${count + 1}` : base)
    })
  })

  const sanitized = DOMPurify.sanitize(markdown.render(source), {
    USE_PROFILES: { html: true },
    ADD_TAGS: ['font'],
    ADD_ATTR: ['target', 'rel', 'loading', 'referrerpolicy'],
  })
  const parsed = new DOMParser().parseFromString(`<main>${sanitized}</main>`, 'text/html')
  const root = parsed.querySelector('main')
  root?.querySelectorAll<HTMLAnchorElement>('a[href]').forEach((link) => {
    const href = link.getAttribute('href') ?? ''
    if (/^https?:\/\//i.test(href)) {
      link.target = '_blank'
      link.rel = 'noopener noreferrer'
    }
  })
  root?.querySelectorAll<HTMLImageElement>('img').forEach((image) => {
    image.setAttribute('loading', 'lazy')
    image.setAttribute('referrerpolicy', 'no-referrer')
  })
  const headings = [...(root?.querySelectorAll<HTMLHeadingElement>('h1,h2,h3,h4,h5,h6') ?? [])]
    .map((heading) => ({
      id: heading.id,
      text: heading.textContent?.trim() || '未命名标题',
      level: Number(heading.tagName.slice(1)),
    }))
    .filter((heading) => Boolean(heading.id))
  const text = root?.textContent?.replace(/\s+/g, '') ?? ''
  const hasReadableContent = Boolean(text || root?.querySelector('img,table,pre,hr'))
  return { html: root?.innerHTML ?? '', headings, hasReadableContent }
}

export function headingSlug(value: string): string {
  const normalized = value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/<[^>]+>/g, '')
    .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
  return `heading-${normalized || 'section'}`
}
