// Every call the page makes. Kept in one file so the page's view of the
// backend is readable in one place.

const json = async (response) => {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.detail || `request failed (${response.status})`)
  return body
}

export const getTools = () => fetch('/api/tools').then(json)
export const getPersonas = () => fetch('/api/personas').then(json)
export const getModels = () => fetch('/api/models').then(json)

export const newSession = (persona, preset) =>
  fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ persona, preset }),
  }).then(json)

export const setTools = (id, allowed_tools) =>
  fetch(`/api/chat/${id}/tools`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ allowed_tools }),
  }).then(json)

export const setEnforcement = (id, enforcement) =>
  fetch(`/api/chat/${id}/enforcement`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enforcement }),
  }).then(json)

export const resetSession = (id) =>
  fetch(`/api/chat/${id}/reset`, { method: 'POST' }).then(json)

// The message endpoint streams `text/event-stream`. `EventSource` cannot POST,
// so the body is read from the fetch stream directly and split on the blank
// line SSE uses as a record separator.
export async function sendMessage(id, text, model, onEvent) {
  const response = await fetch(`/api/chat/${id}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, model }),
  })
  if (!response.ok || !response.body) {
    throw new Error(`the message could not be sent (${response.status})`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let split
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (line) onEvent(JSON.parse(line.slice(6)))
    }
  }
}
