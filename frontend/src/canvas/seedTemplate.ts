import {
  defaultConfigForType,
  MOCK_RUNTIME_CATALOG,
} from '../sections/catalog'
import { TEMPLATE_VERSION, type Template } from './templateIO'

export const SEED_TEMPLATE: Template = {
  version: TEMPLATE_VERSION,
  name: 'demo baseline',
  nodes: [
    {
      id: 'news_source-seed',
      sectionType: 'news_source',
      position: { x: 0, y: -300 },
      config: defaultConfigForType('news_source', MOCK_RUNTIME_CATALOG),
    },
    {
      id: 'market_source-seed',
      sectionType: 'market_source',
      position: { x: 340, y: -300 },
      config: defaultConfigForType('market_source', MOCK_RUNTIME_CATALOG),
    },
    {
      id: 'embedding-seed',
      sectionType: 'embedding',
      position: { x: 0, y: -100 },
      config: defaultConfigForType('embedding', MOCK_RUNTIME_CATALOG),
    },
    {
      id: 'database-seed',
      sectionType: 'database',
      position: { x: 340, y: -100 },
      config: defaultConfigForType('database', MOCK_RUNTIME_CATALOG),
    },
    {
      id: 'analyzer-seed',
      sectionType: 'analyzer',
      position: { x: 0, y: 100 },
      config: defaultConfigForType('analyzer', MOCK_RUNTIME_CATALOG),
    },
    {
      id: 'exit-seed',
      sectionType: 'exit',
      position: { x: 340, y: 100 },
      config: defaultConfigForType('exit', MOCK_RUNTIME_CATALOG),
    },
    {
      id: 'entry-seed',
      sectionType: 'entry',
      position: { x: 0, y: 300 },
      config: defaultConfigForType('entry', MOCK_RUNTIME_CATALOG),
    },
  ],
  edges: [
    // Pipeline flow: news → embedding → analyzer → entry.
    { source: 'news_source-seed', target: 'embedding-seed' },
    { source: 'embedding-seed', target: 'analyzer-seed' },
    { source: 'analyzer-seed', target: 'entry-seed' },
    // Write-to-DB: each persisting section feeds the database section. The
    // embedding edge mirrors EmbeddingManager persisting the market_embedding
    // vector cache through the database section's engine.
    { source: 'market_source-seed', target: 'database-seed' },
    { source: 'news_source-seed', target: 'database-seed' },
    { source: 'embedding-seed', target: 'database-seed' },
  ],
}
