import React from 'react'

/* The UTS marks, drawn as SVG so they scale and need no asset pipeline.
   These are the university's trademarks, reproduced here for a student
   project prototyping a feature for that university. The masthead keeps a
   `demo` marker and the footer says plainly what this is: the resemblance is
   the point, being mistaken for the live portal is not. */

// The device: a cross above a tapering lattice of squares.
export function UtsMark({ size = 44 }) {
  const u = 8            // one square
  const g = 1.4          // gap between squares
  const s = u + g
  const sq = (col, row, key) => (
    <rect key={key} x={col * s} y={row * s} width={u} height={u} />
  )
  // rows of column offsets, centred on column 3
  // A cross above a checkerboard diamond that widens then tapers to a point.
  const rows = [
    [3],
    [2, 3, 4],
    [3],
    [1, 3, 5],
    [0, 2, 4, 6],
    [1, 3, 5],
    [2, 4],
    [3],
  ]
  return (
    <svg
      width={size} height={size * 1.18} viewBox={`0 0 ${7 * s} ${8 * s}`}
      role="img" aria-label="UTS" fill="currentColor"
    >
      {rows.flatMap((cols, row) => cols.map((col, i) => sq(col, row, `${row}-${i}`)))}
    </svg>
  )
}

export function Bell({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
         role="img" aria-label="Notifications">
      <path d="M12 2a1.6 1.6 0 0 1 1.6 1.6v.7a6 6 0 0 1 4.4 5.8v3.4l1.6 2.4v1.2H4.4v-1.2L6 13.5v-3.4a6 6 0 0 1 4.4-5.8v-.7A1.6 1.6 0 0 1 12 2z" />
      <path d="M9.8 19.2h4.4a2.2 2.2 0 0 1-4.4 0z" />
    </svg>
  )
}

const SOCIAL = {
  facebook: 'M13.2 20v-6.6h2.2l.33-2.6H13.2V9.14c0-.75.2-1.26 1.29-1.26h1.37V5.56A18 18 0 0 0 13.85 5.5c-1.98 0-3.33 1.2-3.33 3.42v1.88H8.3v2.6h2.22V20z',
  twitter: 'M20 7.5a6.3 6.3 0 0 1-1.83.5 3.2 3.2 0 0 0 1.4-1.76 6.4 6.4 0 0 1-2.02.77 3.19 3.19 0 0 0-5.43 2.9A9.05 9.05 0 0 1 5.55 6.6a3.19 3.19 0 0 0 .99 4.25 3.16 3.16 0 0 1-1.44-.4v.04a3.19 3.19 0 0 0 2.56 3.13 3.2 3.2 0 0 1-1.44.05 3.19 3.19 0 0 0 2.98 2.21A6.4 6.4 0 0 1 4 17.2 9.02 9.02 0 0 0 8.9 18.6c5.87 0 9.08-4.86 9.08-9.08l-.01-.41A6.5 6.5 0 0 0 20 7.5z',
  instagram: 'M12 7.3a4.7 4.7 0 1 0 0 9.4 4.7 4.7 0 0 0 0-9.4zm0 7.75a3.05 3.05 0 1 1 0-6.1 3.05 3.05 0 0 1 0 6.1zM17.99 7.1a1.1 1.1 0 1 1-2.19 0 1.1 1.1 0 0 1 2.19 0zM19.9 8.22c-.04-1.47-.38-2.77-1.46-3.85-1.07-1.08-2.38-1.41-3.85-1.5-1.51-.08-6.04-.08-7.55 0-1.47.08-2.77.42-3.85 1.5C2.12 5.44 1.79 6.75 1.7 8.22c-.08 1.51-.08 6.04 0 7.55.04 1.47.38 2.77 1.46 3.85 1.08 1.08 2.38 1.41 3.85 1.5 1.51.08 6.04.08 7.55 0 1.47-.09 2.78-.42 3.85-1.5 1.08-1.08 1.42-2.38 1.5-3.85.08-1.51.08-6.04 0-7.55zm-1.96 9.17a3.09 3.09 0 0 1-1.74 1.74c-1.2.48-4.06.37-5.39.37-1.33 0-4.19.1-5.39-.37a3.09 3.09 0 0 1-1.74-1.74c-.48-1.2-.37-4.06-.37-5.39 0-1.33-.1-4.19.37-5.39A3.09 3.09 0 0 1 5.42 5c1.2-.48 4.06-.37 5.39-.37 1.33 0 4.19-.1 5.39.37a3.09 3.09 0 0 1 1.74 1.74c.48 1.2.37 4.06.37 5.39 0 1.33.11 4.19-.37 5.39z',
  youtube: 'M20.6 8.2a2.26 2.26 0 0 0-1.6-1.6C17.6 6.2 12 6.2 12 6.2s-5.6 0-7 .4a2.26 2.26 0 0 0-1.6 1.6C3 9.6 3 12 3 12s0 2.4.4 3.8a2.26 2.26 0 0 0 1.6 1.6c1.4.4 7 .4 7 .4s5.6 0 7-.4a2.26 2.26 0 0 0 1.6-1.6c.4-1.4.4-3.8.4-3.8s0-2.4-.4-3.8zM10.2 14.7V9.3l4.7 2.7z',
  linkedin: 'M8.34 18.5H5.6V9.66h2.74zM6.97 8.45a1.59 1.59 0 1 1 0-3.18 1.59 1.59 0 0 1 0 3.18zM18.5 18.5h-2.73v-4.3c0-1.03-.02-2.35-1.43-2.35-1.44 0-1.66 1.12-1.66 2.28v4.37H9.95V9.66h2.62v1.2h.04a2.87 2.87 0 0 1 2.58-1.42c2.76 0 3.27 1.82 3.27 4.18z',
}

export function Social({ name }) {
  return (
    <a className="social" href="#main" aria-label={name}>
      <svg width="30" height="30" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="12" fill="currentColor" />
        <path d={SOCIAL[name]} fill="#000" />
      </svg>
    </a>
  )
}

export const SOCIAL_NAMES = Object.keys(SOCIAL)
