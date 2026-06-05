/**
 * Demo bootstrap — must be the FIRST import in main.tsx.
 *
 * Section status stores (e.g. news_source/statusStore) self-mount and fire a
 * `/api/...` poll the moment they're imported. Installing the mock here, before
 * main.tsx pulls in `./App` (and its store graph), guarantees `window.fetch` is
 * already patched when that first poll runs.
 *
 * In a normal build `__DEMO__` is `false`, so this whole body is dead code and
 * `./install` (with all of src/demo's fixtures) is tree-shaken away.
 */
import { installDemoServer } from './install'

if (__DEMO__) installDemoServer()
