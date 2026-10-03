'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { DEFAULT_SUPPORT_API_BASE, editSupportCase, supportApiBase, withdrawSupportCase } = require('./support-cases.cjs')

function fakeFetch(status, body) {
  const calls = []
  const impl = async (url, init) => {
    calls.push({ url, init })

    return { ok: status >= 200 && status < 300, status, json: async () => body }
  }

  return { calls, impl }
}

test('supportApiBase follows the upload endpoint, https or local http only', () => {
  assert.equal(supportApiBase('https://support.example.com/api/v1/upload'), 'https://support.example.com/api/v1')
  assert.equal(supportApiBase('http://localhost:8080/api/v1/upload'), 'http://localhost:8080/api/v1')
  assert.equal(supportApiBase('http://support.example.com/api/v1/upload'), DEFAULT_SUPPORT_API_BASE)
  assert.equal(supportApiBase('https://support.example.com/other'), DEFAULT_SUPPORT_API_BASE)
  assert.equal(supportApiBase(undefined), DEFAULT_SUPPORT_API_BASE)
})

test('editSupportCase PATCHes only the given fields with the edit token', async () => {
  const { calls, impl } = fakeFetch(200, { status: 'OPEN' })
  const result = await editSupportCase(
    { caseId: 'SUP-1', editToken: 'tok', summary: 'new', userDescription: '', uploadUrl: 'https://s.example/api/v1/upload' },
    impl
  )

  assert.deepEqual(result, { ok: true, code: 200, status: 'OPEN' })
  assert.equal(calls[0].url, 'https://s.example/api/v1/cases/SUP-1')
  assert.equal(calls[0].init.method, 'PATCH')
  assert.equal(calls[0].init.headers['X-Case-Edit-Token'], 'tok')
  assert.deepEqual(JSON.parse(calls[0].init.body), { summary: 'new', user_description: '' })
})

test('withdrawSupportCase reports server errors with their status', async () => {
  const { calls, impl } = fakeFetch(409, { error: 'case is already closed' })
  const result = await withdrawSupportCase({ caseId: 'SUP 2', editToken: 'tok' }, impl)

  assert.deepEqual(result, { ok: false, code: 409, error: 'case is already closed' })
  assert.equal(calls[0].url, `${DEFAULT_SUPPORT_API_BASE}/cases/SUP%202`)
  assert.equal(calls[0].init.method, 'DELETE')
  assert.equal(calls[0].init.body, undefined)
})

test('requests without case id or token never leave the machine', async () => {
  const { calls, impl } = fakeFetch(200, {})

  assert.equal((await withdrawSupportCase({ caseId: 'SUP-3' }, impl)).ok, false)
  assert.equal((await editSupportCase({ editToken: 'tok' }, impl)).ok, false)
  assert.equal(calls.length, 0)
})

test('network failures come back as code 0', async () => {
  const result = await withdrawSupportCase({ caseId: 'SUP-4', editToken: 'tok' }, async () => {
    throw new Error('offline')
  })

  assert.deepEqual(result, { ok: false, code: 0, error: 'offline' })
})
