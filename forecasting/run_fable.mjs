#!/usr/bin/env node
// No-request run of Fable 5 in Claude Code (Claude Agent SDK): the task text, no requested duration, no cap.
//   node run_fable.mjs [--legacy-wrapper] <instructions_file> <config_dir> <source> <task_id> [rep=1] [workspace_seed]
// <config_dir> is a logged-in Claude Code config directory. Writes $ART_ROOT/<model>/<source>__<task_id>/r<rep>/
// (ART_ROOT defaults to ./runs). CLAUDE_AGENT_SDK may name the SDK module path.
import cp from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const LEGACY = argv[0] === '--legacy-wrapper'
const [instrFile, configDir, source, taskId, rep = '1', wsSeed = ''] = LEGACY ? argv.slice(1) : argv
if (!instrFile || !configDir || !source || !taskId) {
  console.error('usage: run_fable.mjs [--legacy-wrapper] <instructions_file> <config_dir> <source> <task_id> [rep] [workspace_seed]')
  process.exit(2)
}
const sdk = await import(process.env.CLAUDE_AGENT_SDK || '@anthropic-ai/claude-agent-sdk')
const MODEL = process.env.MODEL || 'claude-fable-5'
const EFFORT = process.env.EFFORT || 'xhigh'
const CAP_MIN = Number(process.env.EXEC_CAP_MIN || 0)      // 0 = no wall-clock cap
const MAX_TURNS = Number(process.env.EXEC_MAX_TURNS ?? 0)   // 0 = no turn cap
const safe = `${source}__${taskId}`.replace(/[^A-Za-z0-9._-]/g, '_')
const ART = path.resolve(process.env.ART_ROOT || 'runs', MODEL, safe, `r${rep}`)
if (fs.existsSync(`${ART}/meta.json`)) { console.error(`refusing to overwrite ${ART}`); process.exit(6) }
const work = `${ART}/workspace`
fs.rmSync(work, { recursive: true, force: true })
fs.mkdirSync(work, { recursive: true })
if (wsSeed && fs.existsSync(wsSeed)) cp.execFileSync('cp', ['-R', `${wsSeed}/.`, `${work}/`])

const instructions = fs.readFileSync(instrFile, 'utf8').trim()
let prompt = instructions
if (LEGACY) {  // runs before 27 July 2026
  const [prefix, suffix] = fs.readFileSync(path.join(HERE, 'prompts/legacy_wrapper_fable.txt'), 'utf8').split('{task}')
  prompt = `${prefix}${instructions}${suffix}`
}
fs.writeFileSync(`${ART}/prompt.txt`, prompt)
for (const log of ['events.jsonl', 'stderr.log']) fs.writeFileSync(`${ART}/${log}`, '')
// Web tools were added to the allowlist only for open-web tasks.
const WEB = process.env.EXEC_WEB ?? (/^(AssistantBench__|Agents_Last_Exam__business_finance_ar_full_)/.test(safe) ? '1' : '')
const ALLOWED = process.env.EXEC_ALLOWED_TOOLS
  || `Read,Write,Edit,Glob,Grep,Bash,Task,TodoWrite,NotebookEdit${WEB ? ',WebFetch,WebSearch' : ''}`
const t0 = Date.now()
const ac = new AbortController()

// Containers the agent starts get a run label through a docker shim on its PATH. When the agent
// returns, the clock keeps running until they exit. ProgramBench uses Docker only as a probe, so
// its leftover helper containers are removed instead.
let dockerBin = null
try { dockerBin = cp.execSync('command -v docker', { encoding: 'utf8' }).trim() } catch {}
if (!/^\/[A-Za-z0-9_./-]+$/.test(dockerBin || '')) dockerBin = null
const LABEL = `forecasting.run=${safe}.r${rep}.${process.pid}`
let shimDir = null
if (dockerBin) {
  shimDir = fs.mkdtempSync(path.join(os.tmpdir(), 'forecasting-docker-'))
  fs.writeFileSync(`${shimDir}/docker`, `#!/bin/sh
case "\${1:-}" in
  run|create)
    sub="$1"; shift
    exec '${dockerBin}' "$sub" --label '${LABEL}' "$@"
    ;;
  *) exec '${dockerBin}' "$@" ;;
esac
`, { mode: 0o755 })
}
function runContainers() {
  if (!dockerBin) return []
  try {
    return cp.execFileSync(dockerBin, ['ps', '-q', '--filter', `label=${LABEL}`], { encoding: 'utf8' })
      .split('\n').filter(Boolean)
  } catch { return [] }
}
function waitForBackgroundWork() {
  const mine = runContainers()
  if (!mine.length) return 0
  if (source === 'ProgramBench') {
    for (const id of mine) try { cp.execFileSync(dockerBin, ['rm', '-f', id], { stdio: 'ignore' }) } catch {}
    return 0
  }
  const start = Date.now()
  while (runContainers().length) cp.execSync('sleep 15')
  return Math.round((Date.now() - start) / 6000) / 10
}

// The agent holds Bash with bypassed permissions, so credentials are kept out of its environment.
// Claude Code authenticates from the config directory instead.
const SECRET_ENV = /^(ANTHROPIC_|CLAUDE_CODE_|CLAUDE_CONFIG_DIR$|OPENAI_|AWS_|GITHUB_|GH_|HF_|OPENROUTER_|GOOGLE_|GEMINI_|RUNPOD_|HCLOUD_|HETZNER_|OP_|MODAL_|DAYTONA_)|(_API_KEY|_TOKEN|_SECRET|_ACCESS_KEY|_CREDENTIALS)$/i
const withheld = Object.keys(process.env).filter((k) => SECRET_ENV.test(k))
const agentEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !SECRET_ENV.test(k)))
agentEnv.CLAUDE_CONFIG_DIR = configDir
if (shimDir) agentEnv.PATH = `${shimDir}:${agentEnv.PATH || ''}`

let transcript = '', nText = 0, nTool = 0, nTurns = 0, tokens = null, sessionId = null
const servedModels = new Set()
// Stops the clock (after background work), writes the run record and exits.
function finish(censored, isErr, censorReason = null) {
  let bgWaitMin = 0
  try { bgWaitMin = waitForBackgroundWork() } catch (e) { fs.appendFileSync(`${ART}/stderr.log`, `[bg] ${e}\n`) }
  const actualMin = Math.round((Date.now() - t0) / 6000) / 10
  // Completion, not a FINAL ANSWER line: the agent stopped on its own, with no error or cap, and said something.
  const match = transcript.match(/FINAL ANSWER:\s*([\s\S]{0,400})/)
  const finalText = match ? match[1].trim() : (transcript.trim().slice(-400) || null)
  const answer = (isErr || censored || !(nTool > 0 || nText > 0)) ? null : finalText
  fs.writeFileSync(`${ART}/transcript.txt`, transcript)
  fs.writeFileSync(`${ART}/final_output.txt`, finalText || transcript.slice(-1200))
  try {
    cp.execFileSync('tar', ['czf', 'workspace.tar.gz', 'workspace'], { cwd: ART, stdio: 'ignore' })
    fs.rmSync(work, { recursive: true, force: true })
  } catch {}
  if (shimDir) fs.rmSync(shimDir, { recursive: true, force: true })
  const meta = { source, task_id: taskId, model: MODEL, effort: EFFORT, rep: Number(rep), legacy_wrapper: LEGACY,
    actual_min: actualMin, bg_wait_min: bgWaitMin, censored: !!censored, censor_reason: censorReason,
    is_error: !!isErr, answered: !!answer, final_answer: answer ? answer.slice(0, 300) : null,
    served_models: [...servedModels], n_tool_calls: nTool, n_turns: nTurns, tokens, session_id: sessionId,
    started_epoch: Math.round(t0 / 1000), max_turns: MAX_TURNS > 0 ? MAX_TURNS : null, env_secrets_withheld: withheld }
  fs.writeFileSync(`${ART}/meta.json`, JSON.stringify(meta, null, 1))
  console.log(`run[fable] ${source}/${taskId} r${rep}: actual=${actualMin}min answered=${!!answer}`
    + (censored ? ` CENSORED:${censorReason}` : ''))
  process.exit(0)
}
const capTimer = CAP_MIN > 0 ? setTimeout(() => { ac.abort(); finish(true, false, 'wall_clock') }, CAP_MIN * 60000) : null

const q = sdk.query({ prompt, options: {
  cwd: work, env: agentEnv, model: MODEL, effort: EFFORT,
  settingSources: [], persistSession: true,
  permissionMode: 'bypassPermissions', allowDangerouslySkipPermissions: true,
  // allowedTools only pre-approves tools; it does not remove others (disallowedTools does).
  allowedTools: ALLOWED.split(','),
  ...(process.env.EXEC_DISALLOWED_TOOLS ? { disallowedTools: process.env.EXEC_DISALLOWED_TOOLS.split(',') } : {}),
  abortController: ac, ...(MAX_TURNS > 0 ? { maxTurns: MAX_TURNS } : {}),
  includePartialMessages: false,
  settings: { disableAllHooks: true, disableBundledSkills: true },
  stderr: (s) => fs.appendFileSync(`${ART}/stderr.log`, s) } })
try {
  for await (const m of q) {
    fs.appendFileSync(`${ART}/events.jsonl`, `${JSON.stringify({ _ms: Date.now() - t0, ...m })}\n`)
    if (m.session_id) sessionId = m.session_id
    if (m.type === 'assistant') {
      nTurns++
      if (m.message?.model) servedModels.add(m.message.model)
      for (const c of m.message?.content || []) {
        if (c.type === 'text') { nText++; transcript += `${c.text}\n` } else if (c.type === 'tool_use') nTool++
      }
    }
    if (m.type === 'result') {
      clearTimeout(capTimer)
      tokens = m.usage || null
      // A usage limit or the turn cap censors the run: the duration is a lower bound, not an error.
      const byQuota = m.api_error_status === 429 || /hit your limit|rate limit|quota/i.test(String(m.result || ''))
      const byTurns = m.subtype === 'error_max_turns'
      if (byQuota) finish(true, false, 'quota_429')
      else finish(byTurns, byTurns ? false : m.is_error, byTurns ? 'max_turns' : null)
    }
  }
  clearTimeout(capTimer)
  finish(false, false)  // stream ended without a result message
} catch (e) {
  fs.appendFileSync(`${ART}/stderr.log`, `${e}\n`)
  clearTimeout(capTimer)
  finish(false, true)
}
