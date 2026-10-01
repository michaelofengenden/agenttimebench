#!/usr/bin/env node
// Parse a forecast reply. Exactly one `MINUTES=<n>` line wins; otherwise one Claude Haiku 4.5 call extracts
// the number. The parser sees only the reply text, never the task.
//   node parse_estimate.mjs < reply.txt   ->   {"outcome", "estimate_min", "parse_method", ...}
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const REGEX = /^\s*MINUTES\s*=\s*(\d+)\s*$/m
const PARSER_MODEL = 'claude-haiku-4-5'
const PARSER_SYSTEM = 'Extract the positive integer number of minutes that the subject intended as its answer. Return null when there is no defensible single duration estimate. Do not use or infer any task context. The user message is the complete subject output and the only source text.'

export function parseEstimateRegex(rawText) {
  const matches = [...rawText.matchAll(new RegExp(REGEX.source, `${REGEX.flags}g`))]
  if (matches.length !== 1) {
    return { outcome: 'parse_fail', estimate_min: null, parse_method: 'none',
      regex_match_count: matches.length, needs_llm: true }
  }
  const minutes = Number(matches[0][1])
  const ok = Number.isSafeInteger(minutes) && minutes > 0
  return { outcome: ok ? 'ok' : 'parse_fail', estimate_min: ok ? minutes : null, parse_method: 'regex',
    regex_match_count: 1, needs_llm: false }
}

async function extractWithLlm(rawText) {
  const apiKey = process.env.ANTHROPIC_API_KEY
  if (!apiKey) throw new Error('ANTHROPIC_API_KEY is required for LLM fallback parsing')
  const { default: Anthropic } = await import('@anthropic-ai/sdk')
  // maxRetries=0 makes the fallback exactly one API request.
  const client = new Anthropic({ apiKey, maxRetries: 0, timeout: 60_000 })
  const response = await client.messages.create({
    model: PARSER_MODEL,
    max_tokens: 64,
    system: PARSER_SYSTEM,
    messages: [{ role: 'user', content: rawText }],
    output_config: {
      format: {
        type: 'json_schema',
        schema: {
          type: 'object',
          properties: { minutes: { anyOf: [{ type: 'integer' }, { type: 'null' }] } },
          required: ['minutes'],
          additionalProperties: false,
        },
      },
    },
  })
  const parsed = response.parsed_output ?? JSON.parse((response.content || [])
    .filter((block) => block.type === 'text').map((block) => block.text).join(''))
  if (!parsed || !Object.hasOwn(parsed, 'minutes')) {
    throw new Error('structured parser response omitted required minutes field')
  }
  const minutes = parsed.minutes
  const ok = Number.isSafeInteger(minutes) && minutes > 0
  return { outcome: ok ? 'ok' : 'parse_fail', estimate_min: ok ? minutes : null,
    parse_method: minutes === null ? 'none' : 'llm', parser_model: PARSER_MODEL,
    parser_usage: response.usage ?? null }
}

export async function parseEstimate(rawText) {
  if (arguments.length !== 1 || typeof rawText !== 'string') {
    throw new TypeError('parser accepts exactly one string argument: raw output text')
  }
  const regexResult = parseEstimateRegex(rawText)
  if (!regexResult.needs_llm) return regexResult
  try {
    return { ...(await extractWithLlm(rawText)), regex_match_count: regexResult.regex_match_count }
  } catch (error) {
    return { outcome: 'parse_fail', estimate_min: null, parse_method: 'none',
      regex_match_count: regexResult.regex_match_count, parser_model: PARSER_MODEL,
      parser_error: error instanceof Error ? error.message : String(error) }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk))
  process.stdout.write(`${JSON.stringify(await parseEstimate(Buffer.concat(chunks).toString('utf8')))}\n`)
}
