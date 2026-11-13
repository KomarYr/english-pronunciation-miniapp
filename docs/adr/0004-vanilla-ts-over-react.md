# Vanilla TypeScript, not React, for the Mini App

The Mini App frontend is vanilla TypeScript + Vite +
`@telegram-apps/sdk`, with small modules that render DOM directly. The
author is a JavaScript/TS beginner learning HTML and CSS; React's
concepts (hooks, JSX, state) would make the codebase unreadable to them,
while vanilla stays close to the HTML/CSS they know. All TypeScript
skills from `setup-ts-deep-modules` still apply.

## Considered options

- **React (rejected)**: the ecosystem default for Mini Apps and easiest
  for an agent to generate correctly, but dense with framework concepts
  the maintainer cannot read or debug.
