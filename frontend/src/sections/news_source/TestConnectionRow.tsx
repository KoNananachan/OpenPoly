/**
 * Config-tab action for news_source nodes: asks the backend to open a
 * short-lived WS connection to verify (endpoint, api_key_ref) before the
 * pipeline relies on them. Mirrors analyzer's AnalyzerTestRow; relocated here
 * from the Live tab so it sits at the bottom of the config form.
 */
import { useState } from 'react'

import type { ConfigValues } from '../types'
import {
  testNewsConnection,
  type TestConnectionResult,
} from '../../setting/testConnection'

type TestState =
  | { status: 'idle' }
  | { status: 'testing' }
  | { status: 'done'; result: TestConnectionResult }

export function TestConnectionRow({ config }: { config: ConfigValues }) {
  const [test, setTest] = useState<TestState>({ status: 'idle' })

  const endpoint = typeof config.endpoint === 'string' ? config.endpoint : ''
  const apiRef =
    typeof config.api_key_ref === 'string' ? config.api_key_ref : ''
  const canTest = endpoint !== '' && apiRef !== '' && test.status !== 'testing'

  async function run() {
    setTest({ status: 'testing' })
    const result = await testNewsConnection({ endpoint, api_ref: apiRef })
    setTest({ status: 'done', result })
  }

  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={() => void run()}
        disabled={!canTest}
        title={
          canTest
            ? 'Open a short WS connection to verify endpoint + key'
            : 'Set endpoint and api_key_ref first'
        }
        className="px-3 py-1.5 rounded text-xs font-medium transition-colors bg-neutral-800 hover:bg-neutral-700 disabled:bg-neutral-900 disabled:text-neutral-600 disabled:cursor-not-allowed text-neutral-100"
      >
        {test.status === 'testing' ? 'Testing…' : 'Test connection'}
      </button>
      {test.status === 'done' && (
        <span
          className={`text-[11px] ${
            test.result.ok ? 'text-emerald-300' : 'text-red-300'
          }`}
        >
          {test.result.ok
            ? `connected (${test.result.latency_ms}ms)`
            : `failed: ${test.result.error}`}
        </span>
      )}
    </div>
  )
}
