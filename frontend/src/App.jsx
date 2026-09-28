import React, { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import * as api from './api.js'
import { Bell, Social, SOCIAL_NAMES, UtsLogo } from './brand.jsx'

const args = (o) => Object.entries(o || {}).map(([k, v]) => `${k}=${v}`).join(', ')

// One nav item, because one is all that exists. The portal's other headings
// were decoration: links that went nowhere, in a prototype whose argument is
// that a system should not present itself as more than it is.
const NAV = ['Enrolment assistant']
const CURRENT = 'Enrolment assistant'

const GREETING =
  "Hi! I'm the enrolment assistant. I can search subjects, check prerequisites, " +
  'and manage your enrolment. What do you need?'

const SUGGESTIONS = [
  'What can I take next session?',
  'Tell me about 32513',
  'Enrol me in 32513',
]

// The server resolves `attachment_id` by id or by file name, case-insensitively.
// The page matches the same way so the row finds the document the tool did.
const findAttached = (attached, wanted) => {
  const key = String(wanted || '').toLowerCase()
  return attached.find((a) => a.id.toLowerCase() === key || a.name.toLowerCase() === key)
}

const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files')
const kindLabel = (a) => (a.kind === 'pdf' ? 'PDF' : 'text')
// The server says which shipped document an upload is, if any; the page
// only repeats it. It never guesses from the file name.
const knownLabel = (a) =>
  a.known?.variant === 'planted' ? ' · as received (contains a planted footer)'
    : a.known?.variant === 'clean' ? ' · matches the demo document' : ''

// `refused` is an upstream error (the call returned one), not a policy
// verdict -- `src/enrolment/agent/loop.py` says so outright. `blocked` is
// the authoritative word, written onto the row once `done` arrives; until
// then `refused` is the only signal there is, so it stands in.
const stoppedBy = (m) => m.blocked ?? m.refused

const tagFor = (m) => {
  if (m.verdict === 'require_approval' && stoppedBy(m)) return { cls: 'held', text: 'needs approval' }
  if (stoppedBy(m)) return { cls: 'blocked', text: 'stopped' }
  return { cls: 'ok', text: 'ran' }
}

const rulingFor = (verdict) =>
  verdict === 'deny' ? 'Denied' : verdict === 'require_approval' ? 'Needs approval' : 'Allowed'

const outcomeFor = (d) => {
  if (d.verdict === 'deny') {
    return d.blocked
      ? 'The call was stopped. The enrolment system never saw it.'
      : 'Enforcement is off, so the call was forwarded anyway.'
  }
  if (d.verdict === 'require_approval') {
    return d.blocked
      ? 'Held. The enrolment system has not seen it.'
      : "Enforcement is off, so the call ran without the student's confirmation."
  }
  return 'Forwarded.'
}

// The document text with the planted footer marked. `react-markdown` is not
// used here on purpose: the text is an attacker's, and it is shown as text.
function Saw({ text, payload }) {
  const at = payload ? text.indexOf(payload) : -1
  if (at < 0) return <pre className="saw">{text}</pre>
  return (
    <pre className="saw">
      {text.slice(0, at)}<mark>{text.slice(at, at + payload.length)}</mark>{text.slice(at + payload.length)}
    </pre>
  )
}

export default function App() {
  const [tools, setTools] = useState([])
  const [presets, setPresets] = useState({})
  const [models, setModels] = useState([])
  const [model, setModel] = useState('')

  const [session, setSession] = useState(null)
  const [allowed, setAllowed] = useState([])
  const [enforcement, setEnforcement] = useState('on')
  const [record, setRecord] = useState(null)
  const [personas, setPersonas] = useState([])
  const [baseline, setBaseline] = useState(null)
  const [attached, setAttached] = useState([])
  const [erequests, setErequests] = useState([])

  const [thread, setThread] = useState([])
  const [decisions, setDecisions] = useState([])
  const [decisionTurn, setDecisionTurn] = useState(0)
  // `${turn}:${index}` -> { status: 'pending' | 'confirmed' | 'refused', confirmation }
  const [approvals, setApprovals] = useState({})
  const [draft, setDraft] = useState('')
  const [dropping, setDropping] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const threadBox = useRef(null)
  const fileInput = useRef(null)
  // Mirrors `session` so `allow`'s in-flight replay can tell, when the
  // request resolves, whether the student switched persona while it was
  // out -- `session` itself is a stale closure inside that async function.
  const sessionRef = useRef(null)
  const setSessionId = (id) => { sessionRef.current = id; setSession(id) }

  const applyState = (s) => {
    setRecord(s.record)
    setAttached(s.attachments || [])
    setErequests(s.erequests || [])
  }

  useEffect(() => {
    api.getTools().then((t) => {
      setTools(t.tools)
      setPresets(t.presets)
    })
    api.getPersonas().then(setPersonas).catch(() => setPersonas([]))
    api.getModels().then((m) => {
      setModels(m)
      setModel(m.includes('qwen2.5:14b') ? 'qwen2.5:14b' : (m[0] || ''))
    })
    api.newSession('S9000001', 'enrolment').then((s) => {
      setSessionId(s.id)
      setAllowed(s.allowed_tools)
      setEnforcement(s.enforcement)
      applyState(s)
      setBaseline(s.record)
      setThread([{ role: 'assistant', text: GREETING }])
    }).catch((e) => setError(e.message))
  }, [])

  // Scroll the thread's own box, never the page: scrollIntoView walks every
  // scrollable ancestor, so it yanked the whole document down to the chat on
  // load and after each message.
  useEffect(() => {
    const box = threadBox.current
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' })
  }, [thread])

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

  // Each dropped file is one upload; the chip appears when the server
  // answers. A refused file reports its reason and the rest still go up.
  const uploadFiles = async (files) => {
    if (!session || busy) return
    setError(null)
    for (const file of Array.from(files)) {
      const s = await api.uploadFile(session, file)
        .catch((e) => { setError(`${file.name}: ${e.message}`); return null })
      if (s) applyState(s)
    }
  }
  const detach = async (id) => {
    if (!session || busy) return
    const s = await api.deleteAttachment(session, id).catch((e) => { setError(e.message); return null })
    if (s) applyState(s)
  }
  const attachedPrompts = attached.map((a) => a.known?.prompt).filter(Boolean)

  // Clearing the thread alone would leave the next run starting from the
  // record the last one mutated -- so this resets both, and says so in the
  // thread rather than changing the record panel silently.
  const startOver = async () => {
    if (!session || busy) return
    const state = await api.resetSession(session).catch((e) => { setError(e.message); return null })
    if (!state) return
    applyState(state)
    setBaseline(state.record)
    setDecisions([])
    setApprovals({})
    setError(null)
    setThread([
      { role: 'system', text: `Conversation cleared and the record restored for ${state.record.student_id}.` },
      { role: 'assistant', text: GREETING },
    ])
  }

  const switchPersona = async (persona) => {
    if (busy || !persona || persona === record?.student_id) return
    const s = await api.newSession(persona, 'enrolment').catch((e) => { setError(e.message); return null })
    if (!s) return
    // The console's settings are the presenter's, not the student's: carry
    // the allowlist and the enforcement switch into the new session. The
    // attachments are the student's, and are not carried.
    if (JSON.stringify(s.allowed_tools) !== JSON.stringify(allowed))
      await api.setTools(s.id, allowed).catch(() => {})
    if (s.enforcement !== enforcement)
      await api.setEnforcement(s.id, enforcement).catch(() => {})
    setSessionId(s.id)
    applyState(s)
    setBaseline(s.record)
    setDecisions([])
    setApprovals({})
    setError(null)
    const who = personas.find((p) => p.id === persona)
    setThread([
      { role: 'system', text: `Now assisting ${who?.name ? `${who.name} (${persona})` : persona}. New conversation, fresh record.` },
      { role: 'assistant', text: GREETING },
    ])
  }

  const send = async (text) => {
    if (!session || !text.trim() || busy) return
    setBusy(true)
    setError(null)
    setDraft('')
    setThread((t) => [...t, { role: 'student', text }])

    // Call rows are stamped with this send's token so the verdicts arriving
    // with `done` can be written back onto them, in order. Decisions and call
    // events are aligned by construction (`chat.py` zips them strictly).
    const token = `${Date.now()}-${Math.random()}`
    try {
      await api.sendMessage(session, text, model || null, (event) => {
        if (event.kind === 'call') {
          // Snapshot the document now, from the `attached` this closure
          // captured when `send` started -- controls are disabled while
          // busy, so it cannot have changed since. Rendering from a snapshot
          // (rather than re-querying live `attached` state later) keeps this
          // row showing what the model actually read even if the student
          // later attaches a different variant of the same document.
          const doc = event.tool === 'get_attachment'
            ? findAttached(attached, event.arguments?.attachment_id) : null
          setThread((t) => [...t, {
            role: 'call', token, tool: event.tool, args: event.arguments, refused: event.refused,
            doc: doc ? { id: doc.id, name: doc.name, text: doc.text,
                         payload: doc.known?.payload_text || null } : null,
          }])
        }
        if (event.kind === 'said') {
          setThread((t) => [...t, { role: 'assistant', text: event.text }])
        }
        if (event.kind === 'done') {
          setDecisions(event.calls)
          setDecisionTurn(event.turn)
          applyState(event)
          setThread((t) => {
            let k = 0
            return t.map((m) => {
              if (m.role !== 'call' || m.token !== token) return m
              const d = event.calls[k++]
              return d ? { ...m, verdict: d.verdict, blocked: d.blocked } : m
            })
          })
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

  const allow = async (index) => {
    if (busy || !session) return
    const current = session
    const key = `${decisionTurn}:${index}`
    setBusy(true)
    setApprovals((a) => ({ ...a, [key]: { status: 'pending' } }))
    try {
      const body = await api.approve(session, decisionTurn, index)
        .catch((e) => { setError(e.message); return null })
      // The replay is a request against `current`'s session id. If the
      // student switched persona while it was in flight, `newSession` has
      // already replaced `session` and its record -- writing this reply in
      // now would splice the old session's state, attachments and
      // eRequests into the new persona's view. Drop it silently; the new
      // session already has its own answer.
      if (sessionRef.current !== current) {
        setApprovals((a) => { const next = { ...a }; delete next[key]; return next })
        return
      }
      if (!body) {
        setApprovals((a) => { const next = { ...a }; delete next[key]; return next })
        return
      }
      applyState(body)
      setApprovals((a) => ({ ...a, [key]: { status: 'confirmed', confirmation: body.confirmation } }))
      setThread((t) => [...t, { role: 'system', text:
        `You confirmed ${body.confirmation.call.tool}. It re-read ${body.confirmation.reread} ` +
        `document${body.confirmation.reread === 1 ? '' : 's'}, then ran.` }])
    } finally {
      setBusy(false)
    }
  }

  // Refuse is page-only: nothing ran, so there is nothing to log.
  const refuse = (index) =>
    setApprovals((a) => ({ ...a, [`${decisionTurn}:${index}`]: { status: 'refused' } }))

  const changed = (list) =>
    baseline && JSON.stringify(list) !== JSON.stringify(baseline.enrolled)

  const personaLabel = (p) =>
    [p.name, p.id, p.major].filter(Boolean).join(' · ')

  return (
    <>
      <div className="topbar">
        <span className="wordmark">
          <UtsLogo />
          <span className="demo">demo</span>
        </span>
        <span className="who">
          <Bell />
          <select
            className="whopicker"
            aria-label="Switch student"
            value={record?.student_id || ''}
            onChange={(e) => switchPersona(e.target.value)}
            disabled={busy}
          >
            {(personas.length ? personas : [{ id: record?.student_id || 'Student' }]).map((p) => (
              <option key={p.id} value={p.id}>{personaLabel(p)}</option>
            ))}
          </select>
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
          An LLM agent with {tools.length || 'its'} enrolment tools. Every tool call is intercepted by the
          policy layer before it executes. Documents the student attaches are read
          through <code>get_attachment</code>, which the policy pack labels untrusted.
        </p>
      </div>

      <div className="layout">
        <section
          className={`card chat${dropping ? ' dropping' : ''}`}
          onDragEnter={(e) => { if (!busy && hasFiles(e)) { e.preventDefault(); setDropping(true) } }}
          onDragOver={(e) => { if (!busy && hasFiles(e)) { e.preventDefault(); setDropping(true) } }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDropping(false) }}
          onDrop={(e) => { e.preventDefault(); setDropping(false); if (!busy) uploadFiles(e.dataTransfer.files) }}
        >
          {dropping && <div className="dropzone" aria-hidden="true">Drop to attach</div>}
          <h2>
            <span>Assistant &middot; student {record?.student_id || '…'}</span>
            <span className="sub">
              <button className="startover" onClick={startOver} disabled={busy}
                      title="Clear the conversation and restore the student record">
                Start over
              </button>
              <label htmlFor="model">driven by </label>
              <select id="model" value={model} onChange={(e) => setModel(e.target.value)}
                      disabled={busy}>
                {models.length === 0 && <option value="">no tool-capable model</option>}
                {models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </span>
          </h2>

          <div className="thread" ref={threadBox}>
            {thread.map((m, i) => {
              if (m.role === 'call') {
                const tag = tagFor(m)
                const doc = m.doc
                return (
                  <div key={i} className={`toolcall${stoppedBy(m) ? ' blocked' : ''}`}>
                    <span>{m.tool}</span>{' '}
                    {doc
                      ? <span className="args">{doc.name}</span>
                      : <span className="args">{args(m.args)}</span>}
                    <span className={`tag ${tag.cls}`}>{tag.text}</span>
                    {doc && !stoppedBy(m) && (
                      <details className="saw-panel">
                        <summary>what the model saw</summary>
                        <Saw text={doc.text} payload={doc.payload} />
                      </details>
                    )}
                  </div>
                )
              }
              // The assistant writes markdown -- lists of subject codes, bold
              // headings -- so it is rendered rather than shown as literal
              // asterisks. `react-markdown` does not render raw HTML, which
              // matters here: an assistant reply can contain text an attacker
              // planted in a document, and it must stay inert.
              if (m.role === 'assistant') {
                return (
                  <div key={i} className="bubble assistant">
                    <Markdown>{m.text}</Markdown>
                  </div>
                )
              }
              return <div key={i} className={`bubble ${m.role}`}>{m.text}</div>
            })}
            {busy && <div className="bubble system">Running the model…</div>}
            {error && <div className="bubble system">{error}</div>}
          </div>

          <div className="chips">
            {[...SUGGESTIONS, ...attachedPrompts].map((s) => (
              <button key={s} className="chip" disabled={busy} onClick={() => send(s)}>{s}</button>
            ))}
          </div>

          {attached.length > 0 && (
            <div className="attachments">
              {attached.map((a) => (
                <span key={a.id} className="attachment">
                  <span className="clip" aria-hidden="true">📎</span>
                  {a.name} <span className="variant">· {kindLabel(a)}{knownLabel(a)}</span>
                  <button type="button" onClick={() => detach(a.id)} disabled={busy}
                          aria-label={`Remove ${a.name}`}>×</button>
                </span>
              ))}
            </div>
          )}

          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(draft) }}>
            <div className="attach">
              <button type="button" onClick={() => fileInput.current?.click()} disabled={busy}>
                Attach
              </button>
              <input ref={fileInput} type="file" accept=".pdf,.txt" multiple hidden
                     onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
            </div>
            <input value={draft} onChange={(e) => setDraft(e.target.value)}
                   placeholder="Message the assistant" disabled={busy} />
            <button type="submit" disabled={busy || !draft.trim()}>Send</button>
          </form>
        </section>

        <div className="sidecol">
          <section className="card console">
            <h2>
              <span>Policy console</span>
              <label className="switch">
                <span>Enforcement {enforcement}</span>
                <input type="checkbox" checked={enforcement === 'on'}
                       onChange={toggleEnforcement} disabled={busy} />
                <span className="track" />
              </label>
            </h2>

            {enforcement === 'off' && (
              <p className="unenforced-note">
                Enforcement is off. These rules are still evaluated and still appear in
                the decision log — they are simply not acted on.
              </p>
            )}

            <div className={`presets${enforcement === 'off' ? ' unenforced' : ''}`}>
              {Object.keys(presets).map((key) => (
                <button key={key} disabled={busy} onClick={() => applyPreset(key)}
                        aria-pressed={JSON.stringify([...allowed].sort()) ===
                                      JSON.stringify([...(presets[key] || [])].sort())}>
                  {key.replace('-', ' ')} task
                </button>
              ))}
            </div>

            <div className={`tools${enforcement === 'off' ? ' unenforced' : ''}`}>
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
                const held = d.verdict === 'require_approval' && d.blocked
                const approval = approvals[`${decisionTurn}:${i}`]
                return (
                  <div key={i} className={`decision${denied ? ' denied' : ''}${held ? ' held' : ''}`}>
                    <div className="tool">
                      {d.tool}
                      {d.tainted && <span className="taint">after untrusted read</span>}
                    </div>
                    <div className="args">{args(d.arguments)}</div>
                    <div className="ruling">
                      <b>{rulingFor(d.verdict)}</b> by {d.policy}. {d.reason}
                    </div>
                    <div className={`outcome${denied && !d.blocked ? ' overruled' : ''}`}>
                      {outcomeFor(d)}
                    </div>
                    {held && (
                      <div className="approval">
                        {!approval && (
                          <>
                            <span>Does the student confirm this?</span>
                            <button onClick={() => allow(i)} disabled={busy}>Allow</button>
                            <button onClick={() => refuse(i)} disabled={busy} className="refuse">Refuse</button>
                          </>
                        )}
                        {approval?.status === 'pending' && <span>Replaying…</span>}
                        {approval?.status === 'refused' && <span>Refused by the student. Nothing ran.</span>}
                        {approval?.status === 'confirmed' && (
                          <span>
                            <b>Confirmed by the student.</b> Re-read {approval.confirmation.reread}{' '}
                            document{approval.confirmation.reread === 1 ? '' : 's'}, then forwarded
                            ({rulingFor(approval.confirmation.call.verdict).toLowerCase()} by{' '}
                            {approval.confirmation.call.policy}, approval mode approve).
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </section>

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
            <div className="erequests">
              <div className="caption">eRequests</div>
              {erequests.length === 0 && <p className="muted">No eRequests submitted.</p>}
              <div className="entries">
                {erequests.map((e, i) => (
                  <div key={i} className="erequest">
                    <div className="meta">
                      {e.type} · turn {e.turn}{e.confirmed ? ' · confirmed by the student' : ''}
                    </div>
                    <pre className="text">{e.text}</pre>
                    {e.attachment_ids?.length > 0 && (
                      <div className="cites">cites {e.attachment_ids.join(', ')}</div>
                    )}
                  </div>
                ))}
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
