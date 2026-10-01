#!/usr/bin/env node
// Fable 5 forecast: one fresh, tool-less, single-turn Claude Code session (Claude Agent SDK, API key).
//   node forecast_fable.mjs <instructions_file> <source> <task_id> <out_dir> [model=claude-fable-5]
// Writes <out_dir>/<source>__<task_id>.fable.json once and never overwrites it.
// CLAUDE_AGENT_SDK may name the SDK module path (default: @anthropic-ai/claude-agent-sdk).
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { parseEstimate } from './parse_estimate.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const [PREFIX, SUFFIX] = fs.readFileSync(path.join(HERE, 'prompts/forecast.txt'), 'utf8').split('{task}')
const EFFORT = 'xhigh'

export function buildPrompt(instructionText) {
  return `${PREFIX}${instructionText}${SUFFIX}`
}

// A fresh empty directory under TMPDIR, outside this repository, with no CLAUDE.md or AGENTS.md
// anywhere on its path to the root.
function cleanRoom() {
  const tmp = fs.realpathSync(process.env.TMPDIR || os.tmpdir())
  const cwd = fs.realpathSync(fs.mkdtempSync(path.join(tmp, 'forecast-fable-')))
  let problem = path.relative(fs.realpathSync(path.join(HERE, '..')), cwd).startsWith('..')
    ? null : 'clean room is inside the repository'
  for (let dir = cwd; !problem; dir = path.dirname(dir)) {
    for (const name of ['CLAUDE.md', 'AGENTS.md']) {
      if (fs.existsSync(path.join(dir, name))) problem = `instruction file on walk-up: ${path.join(dir, name)}`
    }
    if (path.dirname(dir) === dir) break
  }
  if (!problem) return cwd
  fs.rmSync(cwd, { recursive: true, force: true })
  throw new Error(problem)
}

// Only these variables reach Claude Code: no OAuth token and no config directory.
function sdkEnv(apiKey) {
  const env = {}
  for (const key of ['HOME', 'PATH', 'TMPDIR', 'SHELL', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TERM']) {
    if (process.env[key] !== undefined) env[key] = process.env[key]
  }
  env.ANTHROPIC_API_KEY = apiKey
  return env
}

async function forecast(record, prompt, model, apiKey, cwd, stderr) {
  const sdk = await import(process.env.CLAUDE_AGENT_SDK || '@anthropic-ai/claude-agent-sdk')
  const options = {  // fallbackModel stays unset
    cwd,
    env: sdkEnv(apiKey),
    model,
    effort: EFFORT,
    settingSources: [],
    persistSession: false,
    permissionMode: 'bypassPermissions',
    allowedTools: [],
    disallowedTools: ['Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit', 'WebFetch', 'WebSearch'],
    settings: { disableAllHooks: true, disableBundledSkills: true },
    maxTurns: 1,
    stderr: (chunk) => stderr.push(String(chunk)),
  }
  const query = sdk.query({ prompt, options })
  const text = []
  let result = null, refusal = null, fallback = null
  for await (const message of query) {
    if (message.type === 'assistant') {
      for (const block of message.message?.content || []) if (block.type === 'text') text.push(block.text)
    }
    if (message.type === 'system' && message.subtype === 'model_refusal_no_fallback') {
      refusal = message.api_refusal_category ?? 'unknown'
    }
    if (message.type === 'system' && message.subtype === 'model_refusal_fallback') {
      fallback = message
      try { await query.interrupt?.() } catch {}
    }
    if (message.type === 'result') result = message
  }
  const rawText = typeof result?.result === 'string' && result.result.length ? result.result : text.join('')
  const modelsSeen = Object.keys(result?.modelUsage || {})
  // Claude Code also makes auxiliary claude-haiku calls; any other model means the subject was swapped.
  const subjectModels = modelsSeen.filter((m) => !/^claude-haiku/.test(m))
  Object.assign(record, { raw_text: rawText, models_seen: modelsSeen, usage: result?.usage ?? null,
    total_cost_usd: result?.total_cost_usd ?? null, num_turns: result?.num_turns ?? null })
  if (fallback) {
    // The subject refused and the harness answered with another model: discard that answer.
    record.outcome = 'refusal'
    record.error = `${fallback.original_model} refused (${fallback.api_refusal_category}); `
      + `fell back to ${fallback.fallback_model}; answer discarded`
  } else if (!result) record.error = 'SDK stream ended without a result message'
  else if (subjectModels.length !== 1 || subjectModels[0] !== model) {
    record.error = `modelUsage assertion failed: ${JSON.stringify(modelsSeen)}`
  } else if (!(Number.isFinite(result.total_cost_usd) && result.total_cost_usd > 0)) {
    record.error = 'API-key cost assertion failed'
  } else if (refusal) record.outcome = 'refusal'
  else if (result.is_error || result.subtype !== 'success') record.error = `SDK result was ${result.subtype}`
  else if (!rawText.trim()) record.error = 'empty reply'
  else Object.assign(record, await parseEstimate(rawText))
}

async function main() {
  const [instrFile, source, taskId, outDir, model = 'claude-fable-5'] = process.argv.slice(2)
  if (!instrFile || !source || !taskId || !outDir) {
    console.error('usage: forecast_fable.mjs <instructions_file> <source> <task_id> <out_dir> [model]')
    return 2
  }
  const apiKey = process.env.ANTHROPIC_API_KEY || ''
  if (!apiKey.startsWith('sk-ant-api')) {
    console.error('FATAL: ANTHROPIC_API_KEY must be an Anthropic API key')
    return 2
  }
  fs.mkdirSync(outDir, { recursive: true })
  const recordPath = path.join(outDir, `${`${source}__${taskId}`.replace(/[^A-Za-z0-9._-]/g, '_')}.fable.json`)
  if (fs.existsSync(recordPath)) {
    console.log(`skip (exists) ${recordPath}`)
    return 0
  }
  const bytes = fs.readFileSync(instrFile)
  const prompt = buildPrompt(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  const record = { source, task_id: taskId, model, effort: EFFORT, outcome: 'infra_fail', estimate_min: null,
    parse_method: 'none', raw_text: '', ts_utc: new Date().toISOString(),
    instructions_sha256_16: crypto.createHash('sha256').update(bytes).digest('hex').slice(0, 16) }
  const stderr = []
  let cwd = null
  try {
    cwd = cleanRoom()
    await forecast(record, prompt, model, apiKey, cwd, stderr)
  } catch (error) {
    record.error = error instanceof Error ? error.message : String(error)
  } finally {
    if (cwd) fs.rmSync(cwd, { recursive: true, force: true })
  }
  record.raw_stderr = stderr.join('')
  fs.writeFileSync(recordPath, `${JSON.stringify(record, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
  console.log(`forecast[fable] ${source}/${taskId}: ${record.outcome}`
    + (record.estimate_min === null ? '' : ` ${record.estimate_min}min`))
  return record.outcome === 'ok' ? 0 : record.outcome === 'infra_fail' ? 4 : 3
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main()
}
