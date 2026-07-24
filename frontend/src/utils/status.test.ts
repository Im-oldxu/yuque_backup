import { describe, expect, it } from 'vitest'
import { completenessText, credentialStatusText, isTerminalJob, jobStatusText, statusTone } from './status'

describe('API enum presentation', () => {
  it('maps stable API enums to the approved Chinese labels', () => {
    expect(jobStatusText.waiting_quota).toBe('等待额度')
    expect(credentialStatusText.action_required).toBe('需要处理')
    expect(completenessText.partial).toBe('部分成功')
  })

  it('recognizes only terminal backup job states', () => {
    expect(isTerminalJob('running')).toBe(false)
    expect(isTerminalJob('waiting_quota')).toBe(false)
    expect(isTerminalJob('partial')).toBe(true)
    expect(isTerminalJob('cancelled')).toBe(true)
  })

  it('uses destructive treatment for states requiring intervention', () => {
    expect(statusTone('action_required')).toBe('destructive')
    expect(statusTone('failed')).toBe('destructive')
    expect(statusTone('complete')).toBe('default')
  })
})
