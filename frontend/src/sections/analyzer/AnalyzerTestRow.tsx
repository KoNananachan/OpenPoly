/**
 * Config-tab action for analyzer nodes: runs one minimal LLM call against the
 * node's current config to verify the api key / base URL / model are usable
 * before the pipeline relies on them. Mirrors news_source's TestConnectionRow.
 */
import { useState } from 'react'

import type { ConfigValues } from '../types'
import { testLLMConnection, type LLMTestResult } from './llmTest'

type TestState =
  | { status: 'idle' }
  | { status: 'testing' }
  | { status: 'done'; result: LLMTestResult }

export function AnalyzerTestRow({ config }: { config: ConfigValues }) {
  const [test, setTest] = useState<TestState>({ status: 'idle' })

  const llmModel = typeof config.llm_model === 'string' ? config.llm_model : ''
  const apiKeyRef =
    typeof config.api_key_ref === 'string' ? config.api_key_ref : ''
  const baseUrl = typeof config.base_url === 'string' ? config.base_url : ''
  const temperature =
    typeof config.temperature === 'number' ? config.temperature : 0.2

  const canTest =
    llmModel !== '' && apiKeyRef !== '' && test.status !== 'testing'

  async function run() {
    setTest({ status: 'testing' })
    const result = await testLLMConnection({
      llm_model: llmModel,
      api_key_ref: apiKeyRef,
      base_url: baseUrl,
      temperature,
    })
    setTest({ status: 'done', result })
  }

  return (
    <div className="flex flex-col gap-2 border-t border-neutral-800 pt-3">
      <button
        type="button"
        onClick={() => void run()}
        disabled={!canTest}
        title={
          canTest
            ? 'Make one minimal LLM call to verify the api key, base URL, and model'
            : 'Set llm_model and api_key_ref first'
        }
        className="self-start px-3 py-1.5 rounded text-xs font-medium transition-colors bg-neutral-800 hover:bg-neutral-700 disabled:bg-neutral-900 disabled:text-neutral-600 disabled:cursor-not-allowed text-neutral-100"
      >
        {test.status === 'testing' ? 'Testing…' : 'Test connection'}
      </button>
      {test.status === 'done' && (
        <span
          className={`text-[11px] break-words ${
            test.result.ok ? 'text-emerald-300' : 'text-red-300'
          }`}
        >
          {test.result.ok
            ? `ok (${test.result.latency_ms}ms)`
            : `failed: ${test.result.error}`}
        </span>
      )}
    </div>
  )
}
