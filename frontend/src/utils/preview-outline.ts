export interface PreviewHeading {
  id: string
  level: number
  text: string
}

const HEADING_SELECTOR = 'h1, h2, h3, h4, h5, h6'
const PREVIEW_STYLE_ID = 'yb-preview-reader-styles'
const PREVIEW_READER_STYLES = `
:root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #202124; background: #fff; }
* { box-sizing: border-box; letter-spacing: 0; }
html { overflow: hidden; background: #fff; }
body { margin: 0; padding: 32px 40px 64px; color: #202124; background: #fff; font-size: 16px; line-height: 1.75; overflow-wrap: anywhere; }
.lake-content { width: min(100%, 920px); margin: 0 auto; }
h1, h2, h3, h4, h5, h6 { color: #111827; font-weight: 650; line-height: 1.35; scroll-margin-top: 24px; }
h1 { margin: 0 0 28px; font-size: 32px; }
h2 { margin: 40px 0 18px; padding-bottom: 8px; border-bottom: 1px solid #e5e7eb; font-size: 25px; }
h3 { margin: 32px 0 14px; font-size: 21px; }
h4 { margin: 26px 0 12px; font-size: 18px; }
h5, h6 { margin: 22px 0 10px; font-size: 16px; }
p { margin: 12px 0; }
p:empty { display: none; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
strong, b { font-weight: 650; }
code { border-radius: 4px; background: #f3f4f6; padding: 2px 5px; color: #1f2937; font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace; font-size: 0.9em; }
pre { margin: 18px 0; overflow-x: auto; border: 1px solid #e5e7eb; border-radius: 6px; background: #f6f8fa; padding: 16px; color: #1f2937; line-height: 1.6; white-space: pre; }
pre code { border: 0; background: transparent; padding: 0; font-size: 13px; }
blockquote { margin: 18px 0; border-left: 4px solid #d1d5db; padding: 2px 0 2px 16px; color: #4b5563; }
ul, ol { margin: 12px 0; padding-left: 28px; }
li + li { margin-top: 5px; }
table { width: 100%; margin: 18px 0; border-collapse: collapse; font-size: 14px; }
th, td { border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 650; }
img { display: block; max-width: 100%; height: auto; margin: 18px auto; }
hr { margin: 28px 0; border: 0; border-top: 1px solid #e5e7eb; }
.yb-missing-resource { display: block; margin: 16px 0; border: 1px dashed #d1d5db; border-radius: 6px; background: #f9fafb; padding: 12px 14px; color: #6b7280; font-size: 14px; }
@media (max-width: 720px) { body { padding: 24px 20px 48px; font-size: 15px; } h1 { font-size: 28px; } h2 { font-size: 23px; } }
`

export function createHeadingId(text: string, index: number): string {
  const normalized = text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
  return normalized || `heading-${index + 1}`
}

export function assignPreviewHeadingIds(root: ParentNode): HTMLHeadingElement[] {
  const usedIds = new Map<string, number>()

  return Array.from(root.querySelectorAll<HTMLHeadingElement>(HEADING_SELECTOR))
    .filter((heading) => Boolean(heading.textContent?.trim()))
    .map((heading, index) => {
      const baseId = heading.id || createHeadingId(heading.textContent ?? '', index)
      const occurrence = usedIds.get(baseId) ?? 0
      usedIds.set(baseId, occurrence + 1)
      heading.id = occurrence ? `${baseId}-${occurrence + 1}` : baseId
      return heading
    })
}

export function preparePreviewDocument(document: Document): void {
  if (!document.getElementById(PREVIEW_STYLE_ID)) {
    const style = document.createElement('style')
    style.id = PREVIEW_STYLE_ID
    style.textContent = PREVIEW_READER_STYLES
    document.head.append(style)
  }

  document.querySelectorAll('p').forEach((paragraph) => {
    if (paragraph.textContent?.trim().toUpperCase() === '[TOC]') paragraph.remove()
  })
  document.querySelectorAll<HTMLImageElement>('img[title]').forEach((image) => {
    if (image.title.trim().toLowerCase() === 'null') image.removeAttribute('title')
  })
  assignPreviewHeadingIds(document)
}

export function extractPreviewOutline(html: string): PreviewHeading[] {
  const parsed = new DOMParser().parseFromString(html, 'text/html')

  return assignPreviewHeadingIds(parsed).map((heading) => ({
    id: heading.id,
    level: Number(heading.tagName.slice(1)),
    text: heading.textContent?.trim() ?? '',
  }))
}

export function hasReadablePreview(html: string): boolean {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  parsed.querySelectorAll('p').forEach((paragraph) => {
    if (paragraph.textContent?.trim().toUpperCase() === '[TOC]') paragraph.remove()
  })
  return Boolean(
    parsed.body.textContent?.trim()
    || parsed.querySelector('img, table, figure, .yb-missing-resource'),
  )
}
