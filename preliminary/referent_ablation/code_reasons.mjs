#!/usr/bin/env node
// Code the one-sentence REASON of every ok receipt with claude-haiku-4-5 structured output (8 requests at a
// time). The coder sees only the sentence: never the arm, subject, estimate or task. Appends one JSON line per
// receipt to <out.jsonl>, keyed by receipt file name; keys already present are skipped, so a re-run resumes.
// Usage: ANTHROPIC_API_KEY=... node code_reasons.mjs <receipts_dir> <out.jsonl>
import fs from 'node:fs'
import path from 'node:path'

const MODEL = 'claude-haiku-4-5'
const CODEBOOK = `Code this one-sentence duration-estimate rationale. Judge ONLY what the text explicitly says; never infer the speaker's identity or the doer from context. A sentence that merely narrates work steps ("I'll inspect the deck, then validate the output") asserts NEITHER human effort NOR agent capability.
Booleans:
- explicit_human_referent: the text explicitly names a person or human role (human, person, engineer, professional, analyst, expert, skilled X, by hand, man-hours, engineer-years).
- explicit_capability_claim: the text explicitly cites tooling, automation, scripting, parallelism, compute, generation speed, or being an AI/model/agent.
- work_step_narration: the text mainly enumerates the steps/phases of the work, without asserting who does it or how fast.
- scale_anchor: cites a named person, project, codebase size, or historical fact as a magnitude anchor.
- uncertainty: hedges (roughly, depends, could vary, unknown).
primary_basis: the single dominant basis actually present in the text — one of "explicit_human_effort", "explicit_capability", "work_decomposition", "task_difficulty", "other".`
const BOOLEANS = ['explicit_human_referent', 'explicit_capability_claim', 'work_step_narration', 'scale_anchor', 'uncertainty']
const SCHEMA = {
  type: 'object',
  properties: {
    ...Object.fromEntries(BOOLEANS.map((name) => [name, { type: 'boolean' }])),
    primary_basis: { enum: ['explicit_human_effort', 'explicit_capability', 'work_decomposition', 'task_difficulty', 'other'] },
  },
  required: [...BOOLEANS, 'primary_basis'],
  additionalProperties: false,
}

const [receiptsDir, outPath] = process.argv.slice(2)
if (!receiptsDir || !outPath || !process.env.ANTHROPIC_API_KEY) {
  console.error('usage: ANTHROPIC_API_KEY=... node code_reasons.mjs <receipts_dir> <out.jsonl>')
  process.exit(2)
}
const { default: Anthropic } = await import('@anthropic-ai/sdk')
const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY, maxRetries: 3, timeout: 60000 })
const done = new Set(fs.existsSync(outPath)
  ? fs.readFileSync(outPath, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l).key) : [])
const jobs = fs.readdirSync(receiptsDir).filter((name) => name.endsWith('.json') && !done.has(name))
  .map((key) => ({ key, r: JSON.parse(fs.readFileSync(path.join(receiptsDir, key), 'utf8')) }))
  .filter(({ r }) => r.outcome === 'ok' && r.reason_text)
const out = fs.createWriteStream(outPath, { flags: 'a' })
let failed = 0
async function worker() {
  for (let job = jobs.shift(); job; job = jobs.shift()) {
    let codes
    try {
      const response = await client.messages.create({
        model: MODEL,
        max_tokens: 200,
        system: CODEBOOK,
        messages: [{ role: 'user', content: job.r.reason_text }],
        output_config: { format: { type: 'json_schema', schema: SCHEMA } },
      })
      codes = response.parsed_output
        ?? JSON.parse((response.content || []).filter((b) => b.type === 'text').map((b) => b.text).join(''))
    } catch (error) {
      failed++
      codes = { coder_error: String(error).slice(0, 120) }
    }
    out.write(`${JSON.stringify({ key: job.key, coder_model: MODEL, ...codes })}\n`)
  }
}
const total = jobs.length
await Promise.all(Array.from({ length: 8 }, worker))
await new Promise((resolve) => out.end(resolve))
console.log(`coded ${total - failed}, failed ${failed}`)
process.exitCode = failed ? 1 : 0
