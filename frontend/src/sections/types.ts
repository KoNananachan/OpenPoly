export type SectionType =
  | 'news_source'
  | 'market_source'
  | 'embedding'
  | 'analyzer'
  | 'entry'
  | 'exit'
  | 'database'

export type Capability =
  | 'news'
  | 'llm'
  | 'market_data'
  | 'order_book'
  | 'news_history'
  | 'portfolio'

export type ConfigValues = Record<string, string | number | boolean>

export type RuntimeCatalogEntry = {
  type: SectionType
  name: string
  version: string
  module: string
  requires: Capability[]
  param_schema: Record<string, unknown>
  source: 'builtin' | 'user'
}
