#!/usr/bin/env node
// One Fable 5 forecast session: a single-turn Claude Agent SDK query() with file, shell and web tools
// disallowed (none was used), run in a fresh empty temporary directory with no setting sources, hooks or skills.
// Writes <out_dir>/<safe_id>.<arm>.fable.r<rep>.a<attempt>.json unless it exists.
// Exit code: 0 ok, 3 refusal or parse_fail, 4 infra_fail.
// Usage: ANTHROPIC_API_KEY=... node elicit_claude.mjs <instructions_file> <source> <task_id> <out_dir> <arm> <rep> [attempt]
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { parseEstimate } from './parse_estimate.mjs'

const PROMPTS = JSON.parse(fs.readFileSync(new URL('./prompts.json', import.meta.url), 'utf8'))
const MODEL = 'claude-fable-5'
const EFFORT = 'xhigh'
const REASON = /^\s*REASON\s*=\s*(.+?)\s*$/m
// Only these variables and the API key reach the session, so no OAuth token, config dir or provider switch applies.
const ENV_KEEP = ['HOME', 'PATH', 'TMPDIR', 'SHELL', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TERM']

// The directory name prefix is the one used in the runs (the agent's context can include its working directory).
function cleanRoom() {
  const cwd = fs.realpathSync(fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'dbyh-fable-')))
  for (let dir = cwd; ; dir = path.dirname(dir)) {
    for (const name of ['CLAUDE.md', 'AGENTS.md']) {
      if (fs.existsSync(path.join(dir, name))) throw new Error(`instruction file on cwd walk-up: ${path.join(dir, name)}`)
    }
    if (path.dirname(dir) === dir) return cwd
  }
}

// Runs the session and returns the record fields that depend on it.
async function session(prompt, cwd) {
  const { query } = await import('@anthropic-ai/claude-agent-sdk')
  const env = Object.fromEntries(ENV_KEEP.filter((k) => process.env[k] !== undefined).map((k) => [k, process.env[k]]))
  const stream = query({
    prompt,
    options: {
      cwd,
      env: { ...env, ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY },
      model: MODEL,
      effort: EFFORT,
      settingSources: [],
      persistSession: false,
      permissionMode: 'bypassPermissions',
      allowedTools: [],
      disallowedTools: ['Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit', 'WebFetch', 'WebSearch'],
      settings: { disableAllHooks: true, disableBundledSkills: true },
      maxTurns: 1,
      // fallbackModel stays unset: an answer from another model must never count as Fable's.
    },
  })
  const text = []
  let refusal = null
  let fallback = null
  let result = null
  for await (const message of stream) {
    if (message.type === 'assistant') {
      for (const block of message.message?.content || []) if (block.type === 'text') text.push(block.text)
    }
    if (message.type === 'system' && message.subtype === 'model_refusal_no_fallback') refusal = message
    // The SDK can still hand a refused turn to another model: stop it and discard the answer.
    if (message.type === 'system' && message.subtype === 'model_refusal_fallback') {
      fallback = message
      await stream.interrupt?.().catch(() => {})
    }
    if (message.type === 'result') result = message
  }
  const rawText = typeof result?.result === 'string' && result.result.length ? result.result : text.join('')
  const modelsSeen = Object.keys(result?.modelUsage ?? {})
  const notAux = modelsSeen.filter((m) => !/^claude-haiku/.test(m))
  const fields = { raw_text: rawText, models_seen: modelsSeen, refusal_category: (refusal ?? fallback)?.api_refusal_category ?? null }
  if (fallback) return { ...fields, outcome: 'refusal', error: `refused; answer from ${fallback.fallback_model} discarded` }
  if (!result) return { ...fields, error: 'SDK stream ended without a result message' }
  if (notAux.length !== 1 || notAux[0] !== MODEL) return { ...fields, error: `unexpected models ${JSON.stringify(modelsSeen)}` }
  if (!(result.total_cost_usd > 0)) return { ...fields, error: `API-key cost check failed: ${result.total_cost_usd}` }
  if (refusal) return { ...fields, outcome: 'refusal' }
  if (result.is_error || result.subtype !== 'success') return { ...fields, error: `SDK result ${result.subtype}` }
  if (!rawText.trim()) return { ...fields, error: 'empty answer' }
  const parsed = await parseEstimate(rawText)
  return { ...fields, outcome: parsed.outcome, estimate_min: parsed.estimate_min, parse_method: parsed.parse_method,
    reason_text: rawText.match(REASON)?.[1] ?? null, ...(parsed.parser_error ? { error: parsed.parser_error } : {}) }
}

const argv = process.argv.slice(2)
const [instrFile, source, taskId, outDir, arm, rep, attempt = '1'] = argv
if (argv.length < 6 || argv.length > 7 || !(arm in PROMPTS.arms) || !process.env.ANTHROPIC_API_KEY) {
  console.error('usage: ANTHROPIC_API_KEY=... node elicit_claude.mjs <instructions_file> <source> <task_id> <out_dir> <arm> <rep> [attempt]')
  process.exit(2)
}
fs.mkdirSync(outDir, { recursive: true })
const safeId = `${source}__${taskId}`.replace(/[^A-Za-z0-9._-]/g, '_')
const recordPath = path.join(outDir, `${safeId}.${arm}.fable.r${rep}.a${attempt}.json`)
if (fs.existsSync(recordPath)) process.exit(0)
const instructions = new TextDecoder('utf-8', { fatal: true }).decode(fs.readFileSync(instrFile))
const prompt = `${PROMPTS.arms[arm]}${PROMPTS.fence_prefix}${instructions}${PROMPTS.fence_suffix}`
const base = { source, task_id: taskId, subject: 'fable', arm, rep: Number(rep), attempt: Number(attempt),
  model: MODEL, effort: EFFORT, prompt_sha256: crypto.createHash('sha256').update(prompt, 'utf8').digest('hex'),
  ts_utc: new Date().toISOString(), outcome: 'infra_fail', estimate_min: null, parse_method: 'none' }
const start = process.hrtime.bigint()
let cwd = null
let record
try {
  cwd = cleanRoom()
  record = { ...base, ...(await session(prompt, cwd)) }
} catch (error) {
  record = { ...base, error: error instanceof Error ? error.message : String(error) }
} finally {
  if (cwd) fs.rmSync(cwd, { recursive: true, force: true })
}
record.session_sec = Number(process.hrtime.bigint() - start) / 1e9
fs.writeFileSync(recordPath, `${JSON.stringify(record, null, 2)}\n`)
console.log(`fable/${arm}/r${rep} ${source}/${taskId}: ${record.outcome}`)
process.exitCode = record.outcome === 'ok' ? 0 : record.outcome === 'infra_fail' ? 4 : 3
