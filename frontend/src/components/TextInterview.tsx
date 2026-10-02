import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
import { sendTextTurn } from '../api'

/** Mirrors MAX_TURN_CHARS in backend/app/realtime/text_interview.py. */
export const MAX_TURN_CHARS = 2000

interface Message {
  role: 'user' | 'assistant'
  text: string
}

interface TextInterviewProps {
  sessionId: string
  personaName: string
  /** The student asked to finish: the parent ends the interview and scores it. */
  onEnd: () => void
  /** True while the parent is ending and scoring. */
  ending: boolean
}

/**
 * The written interview (SR-2026-052 item 2.1): the same stakeholder in a
 * typed chat, for anyone who cannot or would rather not use a microphone.
 *
 * Built to be used by keyboard alone and with a screen reader (target WCAG 2.1
 * AA): a labelled text box that keeps focus between turns, Enter to send and
 * Shift+Enter for a new line, the conversation in a focusable, scrollable
 * `log` region that announces each reply, and a status line while the
 * stakeholder is replying. The server keeps the transcript; this view only
 * displays it.
 */
export function TextInterview({ sessionId, personaName, onEnd, ending }: TextInterviewProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [endedNote, setEndedNote] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    const log = logRef.current
    if (log) log.scrollTop = log.scrollHeight
  }, [messages, sending])

  const closed = !!endedNote

  const send = async () => {
    const text = draft.trim()
    if (!text || sending || closed) return
    setError('')
    setSending(true)
    setDraft('')
    setMessages((m) => [...m, { role: 'user', text }])
    try {
      const result = await sendTextTurn(sessionId, text)
      if (result.reply) {
        setMessages((m) => [...m, { role: 'assistant', text: result.reply as string }])
      }
      if (result.ended) {
        setEndedNote(
          result.reason === 'guardrail'
            ? 'The interview was stopped and flagged for review.'
            : 'The interview reached its time limit.'
        )
      }
    } catch (e: any) {
      const status = e?.response?.status
      const detail = e?.response?.data?.detail
      if (status === 401) return // the app is already redirecting to sign-in
      if (status === 409 && detail?.code === 'interview_ended') {
        setEndedNote('This interview has already ended.')
      } else if (status === 503 || status === 409) {
        // The message was saved on the server; only the reply is missing.
        setError(detail?.message ?? 'The stakeholder could not answer just now.')
      } else if (status === 429) {
        setError('Too many messages in a short time. Please wait a moment and try again.')
      } else {
        // Not known to have been saved: put it back so nothing is lost.
        setMessages((m) => m.slice(0, -1))
        setDraft(text)
        setError('Your message could not be sent. Check your connection and try again.')
      }
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send()
    }
  }

  return (
    <section aria-labelledby="text-interview-title" className="mt-6">
      <h3
        id="text-interview-title"
        className="text-[11px] font-semibold uppercase tracking-[0.11em] text-muted-soft"
      >
        Written interview with {personaName}
      </h3>

      <div
        ref={logRef}
        role="log"
        aria-live="polite"
        aria-label="Interview transcript"
        // Focusable so the conversation can be scrolled from the keyboard.
        tabIndex={0}
        className="mt-2 max-h-[420px] min-h-[180px] overflow-y-auto rounded-lg border border-line bg-white p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
      >
        {messages.length === 0 ? (
          <p className="text-[14px] text-muted-soft">
            Introduce yourself and ask your first question.
          </p>
        ) : (
          <ol className="space-y-3">
            {messages.map((m, i) => (
              <li key={i} className={m.role === 'user' ? 'pl-8' : 'pr-8'}>
                <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-soft">
                  {m.role === 'user' ? 'You' : personaName}
                </div>
                <p
                  className={`mt-0.5 whitespace-pre-wrap rounded-md px-3 py-2 text-[14.5px] leading-relaxed text-ink ${
                    m.role === 'user' ? 'bg-page' : 'border border-line-soft bg-white'
                  }`}
                >
                  {m.text}
                </p>
              </li>
            ))}
          </ol>
        )}
      </div>

      <p role="status" className="mt-2 min-h-[20px] text-[13px] text-muted">
        {sending ? `${personaName} is replying…` : endedNote}
      </p>

      {error && (
        <div
          role="alert"
          className="mt-1 border-l-[3px] border-danger bg-danger-bg px-3.5 py-2.5 text-[13.5px] leading-[1.5] text-danger"
        >
          {error}
        </div>
      )}

      <form onSubmit={onSubmit} className="mt-3">
        <label htmlFor="text-turn" className="block text-[13.5px] font-semibold text-ink">
          Your message to {personaName}
        </label>
        <textarea
          id="text-turn"
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={MAX_TURN_CHARS}
          rows={3}
          disabled={closed}
          aria-describedby="text-turn-help"
          className="mt-1.5 w-full resize-y rounded-md border border-line px-3 py-2 text-[14.5px] leading-relaxed text-ink focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/30 disabled:bg-page"
        />
        <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
          <p id="text-turn-help" className="text-[12px] text-muted-soft">
            Enter to send · Shift+Enter for a new line · {draft.length}/{MAX_TURN_CHARS} characters
          </p>
          <div className="flex gap-3">
            <button
              type="submit"
              // aria-disabled rather than disabled while a reply is pending, so
              // focus is never dropped from a control the student is on.
              aria-disabled={sending || closed || !draft.trim()}
              className="h-10 rounded-md border border-brand px-5 text-[12px] font-semibold uppercase tracking-[0.1em] text-brand hover:bg-primary-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
            >
              Send
            </button>
            <button
              type="button"
              onClick={onEnd}
              disabled={ending || sending}
              className="h-10 rounded-md bg-brand px-5 text-[12px] font-semibold uppercase tracking-[0.1em] text-white hover:bg-brand-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-brand-disabled"
            >
              {ending ? 'Scoring…' : closed ? 'Get feedback' : 'End interview'}
            </button>
          </div>
        </div>
      </form>
    </section>
  )
}
