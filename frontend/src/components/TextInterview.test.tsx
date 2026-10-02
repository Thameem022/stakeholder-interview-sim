// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { sendTextTurn } from '../api'
import ScoreReport from '../ScoreReport'
import { TextInterview } from './TextInterview'

vi.mock('../api', () => ({ sendTextTurn: vi.fn() }))
const send = vi.mocked(sendTextTurn)

// Tell React this is a test environment, so act() flushes updates.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  send.mockReset()
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

function textbox() {
  return container.querySelector<HTMLTextAreaElement>('#text-turn')!
}

/** Type into the controlled textarea the way React sees a user typing. */
function type(text: string) {
  const el = textbox()
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, text)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function press(key: string, init: KeyboardEventInit = {}) {
  act(() => {
    textbox().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init }))
  })
}

async function settle() {
  await act(async () => {
    await Promise.resolve()
  })
}

function renderInterview(onEnd = vi.fn()) {
  act(() =>
    root.render(
      <TextInterview sessionId="s-1" personaName="Alex Martinez" onEnd={onEnd} ending={false} />
    )
  )
  return onEnd
}

describe('TextInterview — keyboard only, no microphone', () => {
  it('starts with focus in a labelled message box', () => {
    renderInterview()
    expect(document.activeElement).toBe(textbox())
    const label = container.querySelector('label[for="text-turn"]')
    expect(label?.textContent).toBe('Your message to Alex Martinez')
    expect(textbox().getAttribute('aria-describedby')).toBe('text-turn-help')
  })

  it('sends on Enter, keeps Shift+Enter for a new line, and shows the reply in the log', async () => {
    send.mockResolvedValue({ reply: 'It floods every spring.', ended: false, reason: null })
    renderInterview()

    type('What about flooding?')
    press('Enter', { shiftKey: true })
    expect(send).not.toHaveBeenCalled()

    press('Enter')
    await settle()

    expect(send).toHaveBeenCalledWith('s-1', 'What about flooding?')
    const log = container.querySelector('[role="log"]')!
    expect(log.getAttribute('aria-live')).toBe('polite')
    expect(log.getAttribute('tabindex')).toBe('0')
    expect(log.textContent).toContain('What about flooding?')
    expect(log.textContent).toContain('It floods every spring.')
    // Focus stays where the student is typing, ready for the next question.
    expect(document.activeElement).toBe(textbox())
    expect(textbox().value).toBe('')
  })

  it('says when the stakeholder is replying', async () => {
    let resolve!: (v: { reply: string; ended: boolean; reason: null }) => void
    send.mockReturnValue(new Promise((r) => (resolve = r)))
    renderInterview()
    type('Hello')
    press('Enter')
    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      'Alex Martinez is replying…'
    )
    await act(async () => resolve({ reply: 'Hi.', ended: false, reason: null }))
    expect(container.querySelector('[role="status"]')?.textContent).toBe('')
  })

  it('closes the conversation when the server stops it, and still offers feedback', async () => {
    send.mockResolvedValue({ reply: null, ended: true, reason: 'guardrail' })
    const onEnd = renderInterview()
    type('Hello')
    press('Enter')
    await settle()
    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      'The interview was stopped and flagged for review.'
    )
    expect(textbox().disabled).toBe(true)
    const finish = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Get feedback'
    )!
    act(() => finish.click())
    expect(onEnd).toHaveBeenCalled()
  })

  it('puts an unsent message back if the network failed', async () => {
    send.mockRejectedValue(new Error('Network Error'))
    renderInterview()
    type('Are you there?')
    press('Enter')
    await settle()
    expect(textbox().value).toBe('Are you there?')
    expect(container.querySelector('[role="alert"]')?.textContent).toMatch(/could not be sent/)
  })
})

describe('AI-generated, not graded (SR-2026-052 item 2.2)', () => {
  it('is stated on the feedback report', () => {
    window.scrollTo = vi.fn() as unknown as typeof window.scrollTo
    act(() =>
      root.render(
        <MemoryRouter>
          <ScoreReport
            evaluation={{
              metadata: { persona_key: 'alex_martinez' } as never,
              dimensions: [],
              overall_score: 5,
              skill_label: 'Developing',
            }}
          />
        </MemoryRouter>
      )
    )
    // Lands on the report itself, not on <body> at the old scroll position.
    expect(document.activeElement?.textContent).toBe(
      'Feedback on your interview with Alex Martinez'
    )
    const notice = container.querySelector('[data-testid="ai-notice"]')
    expect(notice?.getAttribute('role')).toBe('note')
    expect(notice?.textContent).toContain('generated by AI')
    expect(notice?.textContent).toContain('do not affect your grade')
  })
})
