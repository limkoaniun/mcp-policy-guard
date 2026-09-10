import React, { useEffect, useRef, useState } from 'react'
import * as api from './api.js'
import { Bell, Social, SOCIAL_NAMES, UtsMark } from './brand.jsx'

const args = (o) => Object.entries(o || {}).map(([k, v]) => `${k}=${v}`).join(', ')

// The portal's primary navigation, for context. Only the assistant is real:
// this is a research prototype, not a UTS system, and the demo marker in the
// masthead says so.
const NAV = [
  'Home', 'UTS Handbook', 'Enrolment assistant', 'Update Personal Details',
  'My Financial Assistance Programs', 'Complaints and Grievances',
]
const CURRENT = 'Enrolment assistant'

const SUGGESTIONS = [
  'What can I take next session?',
  'Tell me about 32513',
  'Enrol me in 32513',
]

export default function App() {
  const [tools, setTools] = useState([])
  const [presets, setPresets] = useState({})
  const [injected, setInjected] = useState(null)
  const [models, setModels] = useState([])
  const [model, setModel] = useState('')

  const [session, setSession] = useState(null)
  const [allowed, setAllowed] = useState([])
  const [enforcement, setEnforcement] = useState('on')
  const [record, setRecord] = useState(null)
  const [baseline, setBaseline] = useState(null)

  const [thread, setThread] = useState([])
  const [decisions, setDecisions] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const bottom = useRef(null)

  useEffect(() => {
    api.getTools().then((t) => {
      setTools(t.tools)
      setPresets(t.presets)
      setInjected(t.injected)
    })
    api.getModels().then((m) => {
      setModels(m)
      setModel(m.includes('qwen2.5:14b') ? 'qwen2.5:14b' : (m[0] || ''))
    })
    api.newSession('S9000001', 'enrolment').then((s) => {
      setSession(s.id)
      setAllowed(s.allowed_tools)
      setEnforcement(s.enforcement)
      setRecord(s.record)
      setBaseline(s.record)
      setThread([{ role: 'assistant', text:
        "Hi! I'm the enrolment assistant. I can search subjects, check prerequisites, " +
        'and manage your enrolment. What do you need?' }])
    }).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [thread])

  const applyTools = async (next) => {
    setAllowed(next)
    if (session) await api.setTools(session, next).catch((e) => setError(e.message))
  }

  const toggleTool = (name) =>
    applyTools(allowed.includes(name) ? allowed.filter((t) => t !== name) : [...allowed, name])

  const applyPreset = (key) => applyTools(presets[key] || [])

  const toggleEnforcement = async () => {
    const next = enforcement === 'on' ? 'off' : 'on'
    setEnforcement(next)
    if (session) await api.setEnforcement(session, next).catch((e) => setError(e.message))
  }

  const send = async (text) => {
    if (!session || !text.trim() || busy) return
    setBusy(true)
    setError(null)
    setDraft('')
    setThread((t) => [...t, { role: 'student', text }])

    let sawPoison = false
    try {
      await api.sendMessage(session, text, model || null, (event) => {
        if (event.kind === 'call') {
          setThread((t) => [...t, {
            role: 'call', tool: event.tool, args: event.arguments, refused: event.refused,
          }])
          // The payload enters the conversation with the first result that
          // carries it, which is where the model read it.
          if (!sawPoison && injected && event.tool === 'get_subject_details' &&
              String(event.arguments?.code) === injected.subject) {
            sawPoison = true
            setThread((t) => [...t, { role: 'poison' }])
          }
        }
        if (event.kind === 'said') {
          setThread((t) => [...t, { role: 'assistant', text: event.text }])
        }
        if (event.kind === 'done') {
          setDecisions(event.calls)
          setRecord(event.record)
          if (event.truncated) {
            setThread((t) => [...t, { role: 'system', text:
              "The run hit the harness's turn or call bound and stopped here. " +
              'What the assistant would have done next is unknown.' }])
          }
        }
        if (event.kind === 'error') setError(event.detail)
      })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const changed = (list) =>
    baseline && JSON.stringify(list) !== JSON.stringify(baseline.enrolled)

  return (
    <>
      <div className="topbar">
        <span className="wordmark">
          <UtsMark />
          <span className="letters">UTS</span>
          <span className="demo">demo</span>
        </span>
        <span className="who">
          <Bell />
          <span>{record?.student_id || 'Student'}</span>
        </span>
      </div>

      <nav className="mainnav" aria-label="Student portal">
        <ul>
          {NAV.map((item) => (
            <li key={item}>
              <a href="#main" aria-current={item === CURRENT ? 'page' : undefined}>{item}</a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="intro" id="main">
        <h1>Enrolment assistant</h1>
        <p>
          An LLM agent with ten enrolment tools. Every tool call is intercepted by the
          policy layer before it executes.{' '}
          {injected && <>Subject <code>{injected.subject}</code>'s description contains a
          planted prompt injection.</>}
        </p>
      </div>

      <div className="layout">
        <section className="card chat">
          <h2>
            <span>Assistant &middot; student {record?.student_id || '\u2026'}</span>
            <span className="sub">
              <label htmlFor="model">driven by </label>
              <select id="model" value={model} onChange={(e) => setModel(e.target.value)}
                      disabled={busy}>
                {models.length === 0 && <option value="">no tool-capable model</option>}
                {models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </span>
          </h2>

          <div className="thread">
            {thread.map((m, i) => {
              if (m.role === 'call') {
                return (
                  <div key={i} className={`toolcall${m.refused ? ' blocked' : ''}`}>
                    <span>{m.tool}</span>{' '}
                    <span className="args">{args(m.args)}</span>
                    <span className={`tag ${m.refused ? 'blocked' : 'ok'}`}>
                      {m.refused ? 'stopped' : 'ran'}
                    </span>
                  </div>
                )
              }
              if (m.role === 'poison') {
                return (
                  <div key={i} className="poison">
                    <div className="where">returned inside the description of subject {injected.subject}</div>
                    <q>{injected.text}</q>
                  </div>
                )
              }
              return <div key={i} className={`bubble ${m.role}`}>{m.text}</div>
            })}
            {busy && <div className="bubble system">Running the model…</div>}
            {error && <div className="bubble system">{error}</div>}
            <div ref={bottom} />
          </div>

          <div className="chips">
            {SUGGESTIONS.map((s) => (
              <button key={s} className="chip" disabled={busy} onClick={() => send(s)}>{s}</button>
            ))}
          </div>

          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(draft) }}>
            <input value={draft} onChange={(e) => setDraft(e.target.value)}
                   placeholder="Message the assistant" disabled={busy} />
            <button type="submit" disabled={busy || !draft.trim()}>Send</button>
          </form>
        </section>

        <div>
          <section className="card">
            <h2>
              <span>Policy console</span>
              <label className="switch">
                <span>Enforcement {enforcement}</span>
                <input type="checkbox" checked={enforcement === 'on'}
                       onChange={toggleEnforcement} disabled={busy} />
                <span className="track" />
              </label>
            </h2>

            <div className="presets">
              {Object.keys(presets).map((key) => (
                <button key={key} disabled={busy} onClick={() => applyPreset(key)}
                        aria-pressed={JSON.stringify([...allowed].sort()) ===
                                      JSON.stringify([...(presets[key] || [])].sort())}>
                  {key.replace('-', ' ')} task
                </button>
              ))}
            </div>

            <div className="tools">
              {tools.map((t) => (
                <label key={t.name} className="tool">
                  <input type="checkbox" checked={allowed.includes(t.name)}
                         onChange={() => toggleTool(t.name)} disabled={busy} />
                  <span>{t.name}</span>
                  {t.side_effect && <span className="badge">side-effect</span>}
                </label>
              ))}
            </div>

            <div className="log">
              <div className="caption">Decision log — every intercepted call appears here.</div>
              {decisions.length === 0 && <p className="muted">Nothing yet.</p>}
              {decisions.map((d, i) => {
                const denied = d.verdict === 'deny'
                return (
                  <div key={i} className={`decision${denied ? ' denied' : ''}`}>
                    <div className="tool">{d.tool}</div>
                    <div className="args">{args(d.arguments)}</div>
                    <div className="ruling">
                      <b>{denied ? 'Denied' : 'Allowed'}</b> by {d.policy}. {d.reason}
                    </div>
                    <div className={`outcome${denied && !d.blocked ? ' overruled' : ''}`}>
                      {denied
                        ? (d.blocked
                            ? 'The call was stopped. The enrolment system never saw it.'
                            : 'Enforcement is off, so the call was forwarded anyway.')
                        : 'Forwarded.'}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          <div className="spacer" />

          <section className="card">
            <h2>Student record (live)</h2>
            <div className="record">
              <div className="row">
                <span className="label">{record?.student_id} · {record?.course}</span>
                <span className="value">cap {record?.credit_cap}cp</span>
              </div>
              <div className="row">
                <span className="label">completed</span>
                <span className="value">{record?.completed?.join(', ') || '—'}</span>
              </div>
              <div className="row">
                <span className="label">enrolled</span>
                <span className={`value${changed(record?.enrolled) ? ' changed' : ''}`}>
                  {record?.enrolled?.join(', ') || 'none'}
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>

      <footer>
        <div className="footcols">
          <div>
            <h3>About UTS</h3>
            <ul>
              {['The University', 'Campus maps', 'UTS governance', 'Faculties',
                'Privacy', 'Accessibility', 'Disclaimer'].map((item) => (
                <li key={item}><a href="#main">{item}</a></li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Contact us</h3>
            <p className="strong">Student and public enquiries</p>
            <div className="socials">
              {SOCIAL_NAMES.map((name) => <Social key={name} name={name} />)}
            </div>
          </div>
        </div>
        <p className="colophon">
          © Copyright UTS - CRICOS Provider No: 00099F - 27 August 2022 01:00 PM.
          The page is authorised by Director, Student Admin Unit &amp; Planning Quality Unit.
        </p>
        <p className="colophon prototype">
          Research prototype (mcp-policy-guard). Not a UTS system and not connected to one.
          Student records are synthetic; the catalogue and requisite rules are the
          published UTS ones for C04296.
        </p>
      </footer>
    </>
  )
}
