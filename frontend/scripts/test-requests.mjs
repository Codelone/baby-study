import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import console from 'node:console'
import process from 'node:process'
import { URL } from 'node:url'
import ts from 'typescript'

const { AbortController, DOMException, setTimeout, clearTimeout } = globalThis
const source = ts.transpileModule(
  fs.readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8'),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText
let fetchMock, lastTimeout
const context = {
  exports: {},
  AbortController,
  document: { cookie: '' },
  window: {
    setTimeout: (fn, ms) => {
      lastTimeout = ms
      return setTimeout(fn, ms)
    },
    clearTimeout,
  },
  fetch: (...args) => fetchMock(...args),
}
vm.runInNewContext(source, context)
const { request } = context.exports
const response = (status, data) => ({
  status,
  ok: status >= 200 && status < 300,
  headers: { get: () => 'request-fixture' },
  json: async () => data,
})
const pending = (_, { signal }) =>
  new Promise((resolve, reject) => {
    signal.addEventListener(
      'abort',
      () => reject(new DOMException('Aborted', 'AbortError')),
      { once: true },
    )
  })
;(async () => {
  fetchMock = async (url, init) => {
    assert.equal(url, '/baby/study/test')
    assert.equal(init.credentials, 'same-origin')
    return response(200, { ok: true })
  }
  assert.equal((await request('/test')).ok, true)
  assert.equal(lastTimeout, 30000)
  fetchMock = async () => response(204)
  assert.equal(await request('/test'), undefined)
  fetchMock = async () => {
    throw new TypeError('Failed to fetch')
  }
  await assert.rejects(request('/test'), (e) => e.code === 'network_error')
  fetchMock = pending
  await assert.rejects(
    request('/test', { timeoutMs: 5 }),
    (e) => e.code === 'request_timeout',
  )
  const cancel = new AbortController()
  const cancelled = request('/test', { signal: cancel.signal })
  cancel.abort()
  await assert.rejects(
    cancelled,
    (e) =>
      e.name === 'AbortError' &&
      e.code !== 'network_error' &&
      e.code !== 'request_timeout',
  )
  fetchMock = async () => ({
    ...response(502),
    json: async () => {
      throw new SyntaxError('HTML')
    },
  })
  await assert.rejects(
    request('/test'),
    (e) =>
      e.code === 'service_unavailable' &&
      e.status === 502 &&
      e.requestId === 'request-fixture',
  )
  fetchMock = async () =>
    response(400, {
      error: { code: 'invalid', details: { files: ['照片无效'] } },
    })
  await assert.rejects(
    request('/test'),
    (e) => e.code === 'invalid' && e.message === '照片无效',
  )
  fetchMock = async () => response(200, null)
  await assert.rejects(request('/test'), (e) => e.code === 'invalid_response')
  fetchMock = async (_, { signal }) => ({
    ...response(200),
    json: () => pending('', { signal }),
  })
  await assert.rejects(
    request('/test', { timeoutMs: 5 }),
    (e) => e.code === 'request_timeout',
  )
  console.log(
    '9 request-layer scenarios passed: success, 204, network, timeout, cancellation, gateway HTML, validation, invalid JSON shape, body timeout',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
