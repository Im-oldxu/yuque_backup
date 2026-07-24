export interface PreviewHeading {
  fragment: string
  id: string
  level: number
  text: string
}

export function createHeadingId(text: string, index: number): string {
  const normalized = text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
  return normalized || `heading-${index + 1}`
}

export function extractPreviewOutline(html: string): PreviewHeading[] {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const usedIds = new Map<string, number>()

  return Array.from(parsed.querySelectorAll<HTMLHeadingElement>('h1, h2, h3, h4, h5, h6'))
    .map((heading, index) => {
      const text = heading.textContent?.trim() ?? ''
      const baseId = heading.id || createHeadingId(text, index)
      const occurrence = usedIds.get(baseId) ?? 0
      usedIds.set(baseId, occurrence + 1)
      return {
        fragment: heading.id ? encodeURIComponent(heading.id) : `:~:text=${encodeURIComponent(text)}`,
        id: occurrence ? `${baseId}-${occurrence + 1}` : baseId,
        level: Number(heading.tagName.slice(1)),
        text,
      }
    })
    .filter((heading) => heading.text.length > 0)
}
