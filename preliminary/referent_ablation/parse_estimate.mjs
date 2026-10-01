#!/usr/bin/env node
// Extract the minutes estimate from a subject's raw answer.
// Stage 1: exactly one `MINUTES=<number>` line (a leading digit is required, so `.5` goes to stage 2).
// Stage 2, only when stage 1 finds zero or several matches: one claude-haiku-4-5 structured-output call
// that sees the raw answer and nothing else.
// Usage: node parse_estimate.mjs < raw_answer.txt   (prints the result as JSON)
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REGEX = /^\s*MINUTES\s*=\s*(\d+(?:\.\d+)?)\s*$/m
const PARSER_MODEL = 'claude-haiku-4-5'
const SYSTEM = 'Extract the positive number of minutes (decimals are allowed) that the subject intended as its answer. Return null when there is no defensible single duration estimate. Do not use or infer any task context. The user message is the complete subject output and the only source text.'
const SCHEMA = {
  type: 'object',
  properties: {
    minutes: { anyOf: [{ type: 'number' }, { type: 'null' }] },
  },
  required: ['minutes'],
  additionalProperties: false,
}

const validMinutes = (value) => typeof value === 'number' && Number.isFinite(value) && value > 0

export function parseEstimateRegex(rawText) {
  const matches = [...rawText.matchAll(new RegExp(REGEX.source, `${REGEX.flags}g`))]
  if (matches.length !== 1) {
    return { outcome: 'parse_fail', estimate_min: null, parse_method: 'none', regex_match_count: matches.length, needs_llm: true }
  }
  const minutes = Number(matches[0][1])
  const ok = validMinutes(minutes)
  return { outcome: ok ? 'ok' : 'parse_fail', estimate_min: ok ? minutes : null, parse_method: 'regex', regex_match_count: 1, needs_llm: false }
}

async function extractWithLlm(rawText) {
  const { default: Anthropic } = await import('@anthropic-ai/sdk')
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY, maxRetries: 0, timeout: 60_000 })
  const response = await client.messages.create({
    model: PARSER_MODEL,
    max_tokens: 64,
    system: SYSTEM,
    messages: [{ role: 'user', content: rawText }],
    output_config: { format: { type: 'json_schema', schema: SCHEMA } },
  })
  const parsed = response.parsed_output
    ?? JSON.parse((response.content || []).filter((b) => b.type === 'text').map((b) => b.text).join(''))
  if (!parsed || !Object.hasOwn(parsed, 'minutes')) throw new Error('structured parser response omitted required minutes field')
  if (parsed.minutes === null) return { outcome: 'parse_fail', estimate_min: null, parse_method: 'none', parser_model: PARSER_MODEL }
  const ok = validMinutes(parsed.minutes)
  return { outcome: ok ? 'ok' : 'parse_fail', estimate_min: ok ? parsed.minutes : null, parse_method: 'llm', parser_model: PARSER_MODEL }
}

export async function parseEstimate(rawText) {
  const regexResult = parseEstimateRegex(rawText)
  if (!regexResult.needs_llm) return regexResult
  const count = { regex_match_count: regexResult.regex_match_count }
  try {
    return { ...(await extractWithLlm(rawText)), ...count }
  } catch (error) {
    return { outcome: 'parse_fail', estimate_min: null, parse_method: 'none', ...count, parser_model: PARSER_MODEL,
      parser_error: error instanceof Error ? error.message : String(error) }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk))
  process.stdout.write(`${JSON.stringify(await parseEstimate(Buffer.concat(chunks).toString('utf8')))}\n`)
}
